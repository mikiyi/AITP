"""流式短轮询契约门禁：C2 换承载之后，「点执行 → 帧回到界面」这条链路两端都要钉住。

门禁来自两次真实漏网，两条都只有流式路径会踩：

  1. GAP-26 把 `POST /lab/<capability>/stream` 从 SSE 长连接改成「先回 taskId，
     再由 `GET /lab/<taskId>/frames` 按 after 增量取帧」。后端与既有门禁同步改了，
     `frontend/src/api/stream.ts` 却仍按 `text/event-stream` 判响应——JSON 信封落进
     错误分支，实验室「执行」点下去只有一句「已开始」，产出永远不出现。
  2. 同一条 stream 路由把 `projectId` 收进了 META_FIELDS，却没往下传给
     `lab_service.stream_capability`。去重比对域因此永远没有项目，`existingTotal`
     恒为 0，界面永远显示「没有重复」。

`/run` 同步路径两条都不受影响，所以 GAP-31 的跑批门禁全绿也照不出来。
判据因此必须落在流式路径本身，并且带一条「不带 projectId」的正控：
比对不到才算这条规则真的在测转发，而不是恒真。

判据：
  A. 载体：发起响应是轮询信封（taskId/mode/framesUrl/intervalMs），不是长连接；
     帧能按 start→…→done 取到且序号只前进，游标之后的帧不重复投递，
     收尾后 finished 为真。
  B. 回落：缓冲区命中不到（进程内帧随重启消失）时取帧接口回落到任务终态，
     给 expired/truncated + 终局帧，而不是 404 或空转到超时。
  C. 比对域：done 帧的四份结论与 /run 同口径，scope.projectId 等于传入值；
     不传时必须一条也比对不到。
  D. 前端消费：stream.ts 认 poll 信封并真的进入取帧循环（带 after 游标、
     终局/停止/超时都有出口），CapabilityView 的流式请求体带 projectId。

跑的是系统临时目录里的独立 sqlite 库 + 本机 mock 模型（复用 GAP-31 的工装），
不起服务、不占固定端口、不碰 `data/aitp.db`、绝不联网、不调任何真实模型。

    python backend/scripts/check_stream_polling.py

退出码：FAIL=0 为 0，任何 FAIL 为 1。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import time
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import check_prompt_regression as cpr  # noqa: E402  复用同一套 mock 工装与基线种子

JsonDict = dict[str, Any]

SRC = ROOT / "frontend" / "src"
STREAM_TS = SRC / "api" / "stream.ts"
CAPABILITY_VUE = SRC / "views" / "lab" / "CapabilityView.vue"

V1 = "/api/v1"
#: 只用样例集里这一条：它有 GAP-29 的库内基线，能同时验载体与比对域
SAMPLE_ID = "case_login_typical"
#: 取帧轮询上限：mock 是本地即时产出，正常两三秒就收尾，转满说明链路真的断了
POLL_ROUNDS = 60
#: 轮次间隔：与 check_ai_masking 同一档，够快也够把「产出线程还没写完帧」等过去
POLL_SLEEP_S = 0.2

#: 后端 frames 返回体的键（api/lab.py:stream_frames 与 _expired_stream_payload 并集）
FRAMES_PAYLOAD_KEYS = ("frames", "lastSeq", "status", "finished", "truncated",
                       "intervalMs", "expired")
#: done 帧必须带回的四份结论，流式与同步给的是同一套
CONCLUSION_KEYS = ("context", "schema", "dedup", "cost")

#: 承载判定分支：非 SSE 响应里必须先认轮询信封，再落错误出口
SSE_GUARD = "if (!contentType.includes('text/event-stream'))"
#: 只解析不消费（`const poll = pollTargetOf(envelope)` 之后不取帧）就是当初的形态
POLL_TARGET_RE = re.compile(r"pollTargetOf\(\s*envelope\s*\)")
POLL_FRAMES_RE = re.compile(r"pollFrames\(\s*poll\.framesUrl")
CURSOR_RE = re.compile(r"get<\w+>\(\s*path\s*,\s*\{\s*after\s*\}\s*\)")
TERMINAL_RE = re.compile(r"type\s*===\s*'done'[\s\S]{0,40}type\s*===\s*'error'")
START_FRAME_RE = re.compile(r"type:\s*'start',\s*seq:\s*0")
ABORT_RE = re.compile(r"signal\?\.aborted")
DEADLINE_RE = re.compile(r"const LONG_POLL_MS = ([\d_]+)")
STREAM_PROJECT_ID_RE = re.compile(r"projectId:\s*appStore\.currentProjectId")

_passed = 0
_failed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        _failures.append(name)
        print(f"[FAIL] {name}  {detail}")


def block_of(text: str, marker: str) -> str:
    """取 marker 之后第一个花括号块的原文（含首尾括号）。

    静态判据按块作用域收：整份文件里的字符串匹配会把「定义」当「调用」，
    当年「解析了信封却没去取帧」这种半成品正好能从块里照出来。
    `${...}` 是模板插值不是对象字面量，要跳过（streamPost 的第一个参数就是模板串）。
    """
    start = text.find(marker)
    if start < 0:
        return ""
    index = start
    open_at = -1
    while True:
        found = text.find("{", index)
        if found < 0:
            return ""
        if found == 0 or text[found - 1] != "$":
            open_at = found
            break
        index = found + 1
    depth = 0
    for cursor in range(open_at, len(text)):
        if text[cursor] == "{":
            depth += 1
        elif text[cursor] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at:cursor + 1]
    return text[open_at:]


def of(client: Any, method: str, path: str, payload: JsonDict | None = None) -> JsonDict:
    """cpr.data_of 的 GET 分支只会走 client.get，这里补上查询串路径直接复用。"""
    return cpr.data_of(client, method, path, payload)


def poll_all(client: Any, frames_url: str) -> tuple[list[JsonDict], JsonDict]:
    """按前端同一套语义取帧：after 游标推进，拿到终局帧且任务收尾才停。

    frames_url 自带 /api/v1 前缀，test_client 要的就是完整路径，
    削前缀是浏览器 api_base 那一层的事，别在这儿学。
    轮次之间必须真的等：产出在另一个线程上跑，不 sleep 就是把 60 轮瞬间跑完，
    然后对着「只取到 start 帧」的残局判红——门禁自己制造假失败。
    """
    frames: list[JsonDict] = []
    after = 0
    terminal = False
    payload: JsonDict = {}
    for _ in range(POLL_ROUNDS):
        payload = of(client, "GET", f"{frames_url}?after={after}")
        batch = cast(list[JsonDict], payload.get("frames") or [])
        frames.extend(batch)
        after = max([after, int(payload.get("lastSeq") or 0)]
                    + [int(f.get("seq") or 0) for f in batch])
        if any(str(f.get("type")) in ("done", "error") for f in batch):
            terminal = True
        if payload.get("finished") and (terminal or not batch):
            break
        time.sleep(POLL_SLEEP_S)
    return frames, payload


# ---------------- A/B：后端载体与比对域 ----------------

def check_carrier(client: Any, submitted: JsonDict, task_id: int) -> JsonDict:
    check("发起响应是 JSON 信封而不是长连接",
          str(submitted.get("mode") or "") == "poll", str(submitted)[:200])
    check("发起响应带回 taskId", task_id > 0, str(submitted)[:200])
    frames_url = str(submitted.get("framesUrl") or "")
    check("取帧地址指向该任务的 frames 路由",
          frames_url == f"{V1}/lab/stream/{task_id}/frames", frames_url)
    check("发起响应给出轮询间隔", int(submitted.get("intervalMs") or 0) > 0,
          str(submitted.get("intervalMs")))
    frames, payload = poll_all(client, frames_url)
    types = [str(frame.get("type")) for frame in frames]
    check("轮询取到 start 帧", "start" in types, str(types))
    check("轮询以 done 帧收尾", types[-1:] == ["done"], str(types)[-80:])
    check("start 帧就是第一帧（顺序与 SSE 时代一致）", types[:1] == ["start"], str(types))
    seqs = [int(frame.get("seq") or 0) for frame in frames]
    check("帧序号严格递增", seqs == sorted(seqs) and len(set(seqs)) == len(seqs), str(seqs))
    check("收尾后任务标记为已结束", bool(payload.get("finished")), str(payload)[:200])
    tail = of(client, "GET", f"{V1}/lab/stream/{task_id}/frames?after={int(payload.get('lastSeq') or seqs[-1])}")
    check("已取走的帧不重复投递", not (tail.get("frames") or []), str(tail)[:200])
    done = frames[-1] if frames else {}
    check("done 帧带回非空产出（点执行后界面有东西可渲染）",
          bool(str(done.get("output") or "").strip()), str(done)[:160])
    check("done 帧带回四份结论",
          all(key in done for key in CONCLUSION_KEYS), str(sorted(done))[:200])
    return done


def check_dedup_domain(done: JsonDict, project_id: int) -> JsonDict:
    dedup = cast(JsonDict, done.get("dedup") or {})
    scope = cast(JsonDict, dedup.get("scope") or {})
    check("流式去重结论确实带回了比对域", bool(scope), str(dedup)[:200])
    check("比对域按请求传入的项目收敛（漏传时库里一条也对不上）",
          scope.get("projectId") == project_id,
          f"scope={scope} 期望 projectId={project_id}")
    seeds = len(cpr.DEDUP_SEEDS.get(SAMPLE_ID) or [])
    check("流式路径从库内召回到基线用例", int(dedup.get("existingTotal") or 0) >= seeds,
          f"existingTotal={dedup.get('existingTotal')} 基线={seeds}")
    check("流式路径判重条数与预置基线一致",
          int(dedup.get("hitCount") or 0) == cpr.EXPECTED_DUP_HITS[SAMPLE_ID],
          f"hitCount={dedup.get('hitCount')} 期望={cpr.EXPECTED_DUP_HITS[SAMPLE_ID]}")
    return dedup


def check_sync_and_stream_agree(stream_dedup: JsonDict, sync_dedup: JsonDict) -> None:
    def shape(dedup: JsonDict) -> list[tuple[str, float, bool]]:
        return [(str(item.get("title")), float(item.get("similarity") or 0),
                 bool(item.get("dropped")))
                for item in dedup.get("items") or []]

    counts = ("compared", "existingTotal", "hitCount", "suspectCount", "droppedCount", "mode")
    check("流式与同步的去重计数口径一致",
          all(stream_dedup.get(key) == sync_dedup.get(key) for key in counts),
          str({key: (stream_dedup.get(key), sync_dedup.get(key)) for key in counts}))
    check("流式与同步的逐条判定一致（换承载不改判定）",
          shape(stream_dedup) == shape(sync_dedup),
          f"stream={shape(stream_dedup)[:2]} sync={shape(sync_dedup)[:2]}")


def check_project_id_is_the_variable(client: Any, body: JsonDict, profile_id: int) -> None:
    """正控：同一套请求去掉 projectId，比对域必须塌回空。

    少了这一条，「existingTotal ≥ 基线」可能只是工装库里恰好有用例，永远红不了。
    """
    bare = of(client, "POST", f"{V1}/lab/{body['capability']}/stream", {
        "variables": body["variables"],
        "profileId": profile_id,
        "title": "流式契约门禁 · 不带项目",
    })
    frames, _payload = poll_all(client, str(bare.get("framesUrl") or ""))
    last = frames[-1] if frames else {}
    dedup = cast(JsonDict, last.get("dedup") or {}) if str(last.get("type")) == "done" else {}
    scope = cast(JsonDict, dedup.get("scope") or {})
    check("正控对照本身跑通了（对照空跑就不算对照）", bool(dedup), str(last)[:200])
    check("正控：不传项目时流式路径比对不到库内基线",
          bool(dedup) and int(dedup.get("existingTotal") or 0) == 0
          and int(dedup.get("hitCount") or 0) == 0,
          f"existingTotal={dedup.get('existingTotal')} hitCount={dedup.get('hitCount')}")
    check("正控：「按项目收敛」这条判据确实会因漏传而红",
          bool(dedup) and scope.get("projectId") is None, str(scope))


def check_expired_fallback(client: Any, sync_task_id: int) -> None:
    """同步 /run 建的任务从没往帧缓冲写过，取它的帧必然落进回落分支。"""
    payload = of(client, "GET", f"{V1}/lab/stream/{sync_task_id}/frames?after=0")
    frames = cast(list[JsonDict], payload.get("frames") or [])
    check("缓冲区命中不到时回落而不是报错",
          bool(payload.get("expired")) and int(payload.get("frames") and len(frames) or 0) > 0,
          str(payload)[:200])
    check("回落标记为截断帧（帧历史确实丢了）", bool(payload.get("truncated")), str(payload)[:120])
    check("回落直接给出终局帧，前端不用空转到超时",
          bool(frames) and str(frames[-1].get("type")) == "done", str(payload)[:200])
    check("回落帧的序号接在游标之后",
          bool(frames) and int(frames[0].get("seq") or 0) == 1, str(frames[:1])[:160])
    check("回落帧带回了落库产出",
          bool(frames) and bool(str(frames[0].get("output") or "").strip()), str(frames[:1])[:160])


def check_unknown_task(client: Any) -> None:
    """回落也要认得任务：缓冲区与库里都没有时必须是明确的 404 语义，不能空转。"""
    resp = client.get(f"{V1}/lab/stream/999999/frames?after=0")
    body = cast(JsonDict, resp.get_json() or {})
    check("不存在的任务取帧直接报错，而不是回落成空帧让人一直轮询",
          int(body.get("code") or 0) not in (0, 200000), str(body)[:200])


# ---------------- D：前端消费契约 ----------------

def check_frontend() -> None:
    stream_text = STREAM_TS.read_text(encoding="utf-8")
    vue_text = CAPABILITY_VUE.read_text(encoding="utf-8")

    guard = block_of(stream_text, SSE_GUARD)
    check("承载判定分支存在（改成只认 SSE 就要红）", bool(guard), SSE_GUARD)
    check("非 SSE 响应先按轮询信封解析", bool(POLL_TARGET_RE.search(guard)), guard[:200])
    check("解析出的取帧地址真的被拿去轮询（只解析不消费就是当年的形态）",
          bool(POLL_FRAMES_RE.search(guard)), guard[:240])
    check("取帧用 after 游标增量而不是整段重取", bool(CURSOR_RE.search(stream_text)), "")
    check("终局判定同时认 done 与 error", bool(TERMINAL_RE.search(stream_text)), "")
    check("停止按钮有 start 帧可拿 taskId", bool(START_FRAME_RE.search(stream_text)), "")
    check("轮询循环响应中断信号", bool(ABORT_RE.search(stream_text)), "")
    match = DEADLINE_RE.search(stream_text)
    check("轮询有超时上限，不会永久转圈",
          match is not None and int(match.group(1).replace("_", "")) >= 60_000,
          str(match.group(0) if match else ""))
    interface = block_of(stream_text, "interface FramesPayload")
    check("前端帧返回体钉住后端全部键（漏一个 = 界面读不到该状态）",
          all(re.search(rf"\b{key}\??:", interface) for key in FRAMES_PAYLOAD_KEYS),
          interface[:240])
    # 正控：键名规则必须真的能因缺键而红
    check("正控：缺键判据能开火（少一个 expired 就判不过）",
          not all(re.search(rf"\b{key}\??:", interface.replace("expired", "expirX"))
                  for key in FRAMES_PAYLOAD_KEYS), "规则空转：改坏返回体也不会红")

    call = block_of(vue_text, "await streamPost(")
    check("实验室发起流式执行时带上当前项目", bool(STREAM_PROJECT_ID_RE.search(call)),
          call[:240])
    check("正控：漏传项目时该判据确实会红",
          bool(STREAM_PROJECT_ID_RE.search(call))
          and not STREAM_PROJECT_ID_RE.search(call.replace("currentProjectId", "currentProjId")),
          call[:160])

    owners = sorted(
        path.relative_to(SRC).as_posix()
        for path in list(SRC.rglob("*.ts")) + list(SRC.rglob("*.vue"))
        if "framesUrl" in path.read_text(encoding="utf-8")
    )
    check("取帧信封只有承载层认（视图自己拼轮询 = 契约分裂）",
          owners == ["api/stream.ts"], str(owners))


# ---------------- 入口 ----------------

def main() -> int:
    samples = cast(list[JsonDict], cast(JsonDict, json.loads(
        cpr.SAMPLES_PATH.read_text(encoding="utf-8")))["samples"])
    sample = next(item for item in samples if str(item["id"]) == SAMPLE_ID)

    client, db_path, server = cpr.build_app()
    try:
        fixture = sample["fixture"]
        cpr.REPLIES = {SAMPLE_ID: fixture if isinstance(fixture, str)
                       else json.dumps(fixture, ensure_ascii=False)}
        projects = cpr.seed_dedup_baseline(db_path, [sample])
        project_id = int(projects[SAMPLE_ID])
        profile = of(client, "POST", f"{V1}/settings/ai/profiles", {
            "name": "流式契约门禁档案",
            "provider": "custom",
            "protocol": "openai_compat",
            "baseUrl": f"http://127.0.0.1:{server.server_address[1]}/v1",
            "model": SAMPLE_ID,
            "isLocal": True,
            "isActive": True,
            "priority": 100,
            "timeout": 10,
        })
        profile_id = int(profile["id"])
        capability = str(sample["capability"])
        body = {"capability": capability, "variables": sample["variables"],
                "projectId": project_id}

        submitted = of(client, "POST", f"{V1}/lab/{capability}/stream", {
            "variables": sample["variables"],
            "profileId": profile_id,
            "title": str(sample.get("title") or SAMPLE_ID),
            "projectId": project_id,
        })
        task_id = int(submitted.get("taskId") or 0)
        done = check_carrier(client, submitted, task_id)
        stream_dedup = check_dedup_domain(done, project_id)

        sync = of(client, "POST", f"{V1}/lab/{capability}/run", {
            "variables": sample["variables"],
            "profileId": profile_id,
            "title": str(sample.get("title") or SAMPLE_ID),
            "projectId": project_id,
            "dedupMode": "mark",
        })
        check_sync_and_stream_agree(stream_dedup, cast(JsonDict, sync.get("dedup") or {}))
        check_project_id_is_the_variable(client, body, profile_id)
        check_expired_fallback(client, int(sync.get("taskId") or 0))
        check_unknown_task(client)
        check_frontend()
    finally:
        server.shutdown()

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
