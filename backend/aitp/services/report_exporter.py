"""测试报告导出：Markdown / 独立 HTML / PDF / 打印视图。

四种产物来自同一份报告快照，内容结构一致，只是渲染通道不同：
- md    → YAML frontmatter + 正文与章节
- html  → 单文件 HTML（内嵌 CSS），可离线双击打开
- pdf   → reportlab 直出的 .pdf；中文字体在本机字体目录按黑体类优先逐级探测，找不到才降级为英文占位
- print → 面向浏览器的打印视图，用户自行「另存为 PDF」

GAP-35 的「失败样本 + 执行证据」维度由 ``samples`` 参数带进来，三种格式各自排一次版
（md 走表格 + 摘要代码块、html 走真表格 + 证据块、pdf 走 reportlab 表格 + 段落），
不共用同一段渲染，所以「某一种格式漏带样本」是能被门禁单独抓红的。
"""

from __future__ import annotations

import html as html_lib
import os
import platform
import re
from io import BytesIO
from pathlib import Path
from typing import Any

from ..common.time_utils import utcnow_iso

SECTION_LABELS: dict[str, str] = {
    "overview": "总览",
    "case": "用例情况",
    "bug": "缺陷情况",
    "coverage": "覆盖情况",
    "trend": "趋势",
    "risk": "风险",
    "conclusion": "结论与建议",
}

#: GAP-35：失败样本与执行证据章节。三种导出格式各自渲染这一章节，
#: 所以「漏带样本」在某一种格式里红得出来，而不是靠通用章节蒙混过关。
FAILURE_SECTION_LABEL = "失败样本执行证据"
#: 空态文案：没有失败样本时仍然出章节，只是写明空态，避免被误读成导出漏了内容
FAILURE_EMPTY_TEXT = "本次统计范围内没有失败样本，报告不附带执行证据。"
#: 一条证据都没有时的提示：快照上线之前 / 超过保留期被清理
FAILURE_NO_EVIDENCE_TEXT = "这条失败样本没有可下钻的执行证据快照。"
#: 证据编号前缀，与 execution_protocol.describe_evidence 同口径
EVIDENCE_NO_PREFIX = "EV-"
#: 导出里最多带多少条失败样本；超出只在标题写明总数，避免长报告把 PDF 撑爆
MAX_FAILURE_SAMPLES = 30
#: 单个样本最多列几条失败要点
MAX_FAILURE_POINTS = 5

TYPE_LABELS: dict[str, str] = {
    "summary": "测试总结",
    "iteration": "迭代测试报告",
    "regression": "回归测试报告",
    "acceptance": "验收测试报告",
}

STATUS_LABELS: dict[str, str] = {
    "draft": "草稿",
    "published": "已发布",
    "archived": "已归档",
}

EXPORT_FORMATS = ("md", "html", "pdf", "print")
FORMAT_EXTENSIONS = {"md": "md", "html": "html", "pdf": "pdf", "print": "html"}

# 中文字体候选：文件名按「黑体类优先、宋体类兜底」排序，目录按「系统自带优先、用户目录最后」排序。
# 三份系统都列全，不按当前系统硬切，避免用户把 Windows 字体拷到 Linux 后反而找不到。
_FONT_NAME = "AitpCJK"
_FONT_PATTERNS: tuple[str, ...] = (
    "simhei.ttf",  # Windows 黑体
    "msyh.ttc",  # Windows 微软雅黑
    "msyh.ttf",
    "PingFang.ttc",  # macOS 苹方
    "STHeiti Medium.ttc",  # macOS 黑体-简
    "NotoSansCJK*.ttc",  # Linux / 思源黑体，ttc 取第 0 个子字体
    "NotoSansCJK*.otc",
    "SourceHanSansCN*.otf",
    "SourceHanSansCN*.ttf",
    "wqy-zenhei*.ttc",  # Linux 文泉驿正黑
    "wqy-microhei*.ttc",
    "DroidSansFallback.ttf",
    "simsun.ttc",  # Windows 宋体
    "Songti.ttc",  # macOS 宋体
    "Arial Unicode.ttf",
    "NotoSerifCJK*.ttc",
)

_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_ILLEGAL_NAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_FENCE_RE = re.compile(r"^\s*```")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_METRIC_TABLE_RE = re.compile(r"^\s*\|\s*指标\s*\|\s*值\s*\|")


# ---------------- 导出文件名 ----------------

def file_stem(title: str, report_no: str, report_id: int) -> str:
    """导出文件名主干：去掉文件系统非法字符，保留中文。"""
    parts: list[str] = []
    for raw in (title or "", report_no or ""):
        cleaned = _ILLEGAL_NAME_RE.sub("_", str(raw)).strip(" ._")
        if cleaned:
            parts.append(cleaned)
    if not parts:
        return f"report-{report_id}"
    return "-".join(parts)[:120]


# ---------------- 通用快照 ----------------

def _label(mapping: dict[str, str], key: Any) -> str:
    text = str(key or "").strip()
    return mapping.get(text, text)


def _metric_rows(metric: Any) -> list[tuple[str, str]]:
    """把 metric 字典拉平成 (名, 值) 列表，保持插入顺序。"""
    if not isinstance(metric, dict):
        return []
    rows: list[tuple[str, str]] = []
    for key, value in metric.items():
        if isinstance(value, (dict, list)):
            rows.append((str(key), str(value)))
        else:
            rows.append((str(key), "" if value is None else str(value)))
    return rows


def _items_by_section(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(items, key=lambda item: (int(item.get("sort_order") or 0), int(item.get("id") or 0)))
    return ordered


def _report_period(report: dict[str, Any]) -> str:
    start = str(report.get("period_start") or report.get("periodStart") or "").strip()
    end = str(report.get("period_end") or report.get("periodEnd") or "").strip()
    if start and end:
        return f"{start} ~ {end}"
    return start or end or "未填写"


def _meta_pairs(report: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        ("报告编号", str(report.get("report_no") or report.get("reportNo") or "")),
        ("报告类型", _label(TYPE_LABELS, report.get("report_type") or report.get("reportType"))),
        ("统计区间", _report_period(report)),
        ("状态", _label(STATUS_LABELS, report.get("status"))),
        ("更新时间", str(report.get("updated_at") or report.get("updatedAt") or "")),
    ]


def _has_metric_table(text: str) -> bool:
    """正文里是否已经带「指标 / 值」表格；带了就不再追加一份。"""
    return any(_METRIC_TABLE_RE.match(line) for line in text.splitlines())


def _residual_content(content: str, items: list[dict[str, Any]]) -> str:
    """正文里没被章节覆盖的部分；章节已覆盖全文时返回空串，避免重复输出。"""
    if not content.strip():
        return ""
    haystack = "\n".join(
        [str(item.get("title") or "") for item in items]
        + [str(item.get("content") or "") for item in items]
    )
    residual: list[str] = []
    for line in content.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text not in haystack:
            residual.append(text)
    return "\n".join(residual).strip()


def _sections_markdown(items: list[dict[str, Any]]) -> list[str]:
    """章节正文：每个 section 一段，附带指标表格。"""
    blocks: list[str] = []
    for item in _items_by_section(items):
        title = str(item.get("title") or "").strip()
        heading = title or _label(SECTION_LABELS, item.get("section"))
        parts = [f"## {heading}"]
        content = str(item.get("content") or "").strip()
        if content:
            parts.append(content)
        rows = _metric_rows(item.get("metric"))
        if rows and not _has_metric_table(content):
            table = ["| 指标 | 值 |", "| --- | --- |"]
            table.extend(f"| {name} | {value} |" for name, value in rows)
            parts.append("\n".join(table))
        blocks.append("\n\n".join(parts))
    return blocks


# ---------------- 失败样本与执行证据（GAP-35） ----------------

def _text(value: Any) -> str:
    """任意值 → 渲染用文本；None / 空值统一成空串，不给导出留下 'None' 字样。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value).strip()


def normalize_failure_samples(samples: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """调用契约：``None`` = 这次导出不带证据维度（保持既有产物不变）；
    列表 = 带证据维度，空列表就是空态章节。"""
    if samples is None:
        return None
    return [dict(item) for item in samples if isinstance(item, dict)]


def _sample_total(samples: list[dict[str, Any]]) -> int:
    for sample in samples:
        raw = sample.get("failureTotal")
        if raw not in (None, ""):
            try:
                return max(int(raw), len(samples))
            except (TypeError, ValueError):
                break
    return len(samples)


def _sample_evidence_no(sample: dict[str, Any]) -> str:
    """证据编号：与缺陷描述里的 ``EV-<id>`` 同一个号，两处下钻到同一条证据。"""
    evidence_id = sample.get("evidenceId")
    return f"{EVIDENCE_NO_PREFIX}{int(evidence_id)}" if evidence_id not in (None, "") else ""


def _sample_cells(sample: dict[str, Any]) -> dict[str, str]:
    """把一个失败样本拉平成六种格式共用的字段：标识 / 用例 / 结论 / 耗时时间 / 证据 / 要点。"""
    points_raw = sample.get("failurePoints")
    points = [ _text(item) for item in points_raw if _text(item) ] if isinstance(points_raw, list) else []
    timing = " / ".join(
        value for value in (
            f"{int(sample.get('durationMs') or 0)}ms" if sample.get("durationMs") is not None else "",
            _text(sample.get("executedAt")),
        ) if value
    )
    evidence_no = _sample_evidence_no(sample)
    attachments = sample.get("attachments")
    attachment_marks: list[str] = []
    if isinstance(attachments, list):
        for item in attachments:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("fileName")) or _text(item.get("attachmentId"))
            link = _text(item.get("previewUrl"))
            if name or link:
                attachment_marks.append(f"{name} → {link}" if link else name)
    evidence_cell = " / ".join(
        value for value in (
            evidence_no or "无快照",
            _text(sample.get("drillUrl") or sample.get("evidenceUrl")),
            "附件：" + "、".join(attachment_marks[:3]) if attachment_marks else "",
        ) if value
    )
    return {
        "key": _text(sample.get("sampleKey")) or f"#{_text(sample.get('runCaseId'))}",
        "case": " ".join(value for value in (_text(sample.get("caseNo")), _text(sample.get("title"))) if value),
        "result": " / ".join(
            value for value in (_text(sample.get("resultLabel")) or _text(sample.get("result")),
                                _text(sample.get("sourceTypeLabel"))) if value
        ),
        "timing": timing,
        "evidence": evidence_cell,
        "points": "；".join(points[:MAX_FAILURE_POINTS]) or _text(sample.get("failureSummary"))
                   or (FAILURE_NO_EVIDENCE_TEXT if not evidence_no else "无失败要点"),
        "evidenceNo": evidence_no,
        "attachments": "、".join(attachment_marks),
        "summary": _text(sample.get("evidenceSummary")),
    }


def _visible_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return samples[:MAX_FAILURE_SAMPLES]


def failure_markdown_section(samples: list[dict[str, Any]] | None) -> str:
    """证据章节的 Markdown 形态：报告详情接口与前端章节共用这一份口径。"""
    if samples is None:
        return ""
    heading = f"## {FAILURE_SECTION_LABEL}"
    if not samples:
        return f"{heading}（0 条）\n\n{FAILURE_EMPTY_TEXT}\n"
    total = _sample_total(samples)
    shown = _visible_samples(samples)
    suffix = f"（共 {total} 条，以下 {len(shown)} 条）" if total > len(shown) else f"（{total} 条）"
    lines = [f"{heading}{suffix}", ""]
    lines.append("| 样本标识 | 用例 | 结论 | 耗时 / 执行时间 | 证据 | 失败要点 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for sample in shown:
        cell = _sample_cells(sample)
        lines.append(
            f"| `{cell['key']}` | {cell['case'] or '-'} | {cell['result'] or '-'} | "
            f"{cell['timing'] or '-'} | {cell['evidence']} | {cell['points']} |"
        )
    lines.append("")
    for sample in shown:
        cell = _sample_cells(sample)
        lines.append(f"### {cell['key']} {cell['case']}".rstrip())
        lines.append(f"- 结论：{cell['result'] or '-'}")
        lines.append(f"- 耗时 / 执行时间：{cell['timing'] or '-'}")
        drill = _text(sample.get("drillUrl")) or _text(sample.get("evidenceUrl"))
        lines.append(f"- 证据下钻：{drill or FAILURE_NO_EVIDENCE_TEXT}")
        if cell["attachments"]:
            lines.append(f"- 证据附件：{cell['attachments']}")
        if cell["summary"]:
            lines.append("")
            lines.append("```text")
            lines.append(cell["summary"])
            lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _html_row(cells: dict[str, str], sample: dict[str, Any]) -> str:
    """单行 HTML：证据列给可点的下钻链接，导出文件双击就能跳到当次执行证据。"""
    drill = _text(sample.get("drillUrl")) or _text(sample.get("evidenceUrl"))
    evidence = html_lib.escape(cells["evidenceNo"]) if cells["evidenceNo"] else "无快照"
    if drill:
        evidence = f'{evidence} <a href="{html_lib.escape(drill, quote=True)}">{html_lib.escape(drill)}</a>'
    cells_html = [
        f"<code>{html_lib.escape(cells['key'])}</code>",
        html_lib.escape(cells["case"] or "-"),
        html_lib.escape(cells["result"] or "-"),
        html_lib.escape(cells["timing"] or "-"),
        evidence,
        html_lib.escape(cells["points"]),
    ]
    return f"<tr>{''.join(f'<td>{item}</td>' for item in cells_html)}</tr>"


def failure_html_section(samples: list[dict[str, Any]] | None) -> str:
    """证据章节的 HTML 形态：独立渲染一份表格 + 每条样本的证据摘要块。"""
    if samples is None:
        return ""
    if not samples:
        return (
            f'<h2>{html_lib.escape(FAILURE_SECTION_LABEL)}（0 条）</h2>\n'
            f'<p class="evidence-empty">{html_lib.escape(FAILURE_EMPTY_TEXT)}</p>\n'
        )
    total = _sample_total(samples)
    shown = _visible_samples(samples)
    suffix = f"（共 {total} 条，以下 {len(shown)} 条）" if total > len(shown) else f"（{total} 条）"
    parts = [
        f'<h2>{html_lib.escape(FAILURE_SECTION_LABEL)}{html_lib.escape(suffix)}</h2>',
        '<table class="evidence-table">',
        "<thead><tr>"
        + "".join(f"<th>{name}</th>" for name in
                  ("样本标识", "用例", "结论", "耗时 / 执行时间", "证据", "失败要点"))
        + "</tr></thead><tbody>",
    ]
    for sample in shown:
        parts.append(_html_row(_sample_cells(sample), sample))
    parts.append("</tbody></table>")
    for sample in shown:
        cell = _sample_cells(sample)
        body_lines = [
            (
                f"<div><strong>{html_lib.escape(cell['key'])}</strong>"
                f"　{html_lib.escape(cell['case'])}</div>"
            ),
            (
                f"<div>结论：{html_lib.escape(cell['result'] or '-')}"
                f"　耗时 / 执行时间：{html_lib.escape(cell['timing'] or '-')}</div>"
            ),
        ]
        if cell["attachments"]:
            body_lines.append(
                f"<div>证据附件：{html_lib.escape(cell['attachments'])}</div>"
            )
        if cell["summary"]:
            body_lines.append(f"<pre>{html_lib.escape(cell['summary'])}</pre>")
        parts.append(f'<div class="evidence-block">{"".join(body_lines)}</div>')
    return "\n".join(parts) + "\n"


def failure_pdf_flowables(
    samples: list[dict[str, Any]] | None,
    styles: dict[str, Any],
    font_name: str,
) -> list[Any]:
    """证据章节的 PDF 形态：同一份字段走 reportlab 表格 + 摘要段落，不引入新依赖。"""
    if samples is None:
        return []
    from reportlab.platypus import Paragraph, Preformatted, Spacer

    if not samples:
        return [
            Paragraph(f"{FAILURE_SECTION_LABEL}（0 条）", styles["h2"]),
            Paragraph(FAILURE_EMPTY_TEXT, styles["body"]),
            Spacer(1, 8),
        ]
    total = _sample_total(samples)
    shown = _visible_samples(samples)
    suffix = f"（共 {total} 条，以下 {len(shown)} 条）" if total > len(shown) else f"（{total} 条）"
    story: list[Any] = [Paragraph(_inline_markup(FAILURE_SECTION_LABEL + suffix), styles["h2"])]
    table_data = [["样本标识", "用例", "结论", "耗时 / 执行时间", "证据", "失败要点"]]
    for sample in shown:
        cell = _sample_cells(sample)
        drill = _text(sample.get("drillUrl")) or _text(sample.get("evidenceUrl"))
        evidence = f"{cell['evidenceNo'] or '无快照'} {drill}".strip()
        table_data.append([
            cell["key"], cell["case"] or "-", cell["result"] or "-",
            cell["timing"] or "-", evidence, cell["points"],
        ])
    story.append(_pdf_table([[ _inline_markup(text) for text in row] for row in table_data], font_name))
    story.append(Spacer(1, 8))
    for sample in shown:
        cell = _sample_cells(sample)
        story.append(Paragraph(_inline_markup(f"{cell['key']} {cell['case']}".strip()), styles["h3"]))
        lines = [
            f"结论：{cell['result'] or '-'}",
            f"耗时 / 执行时间：{cell['timing'] or '-'}",
            "证据下钻："
            + (_text(sample.get("drillUrl")) or _text(sample.get("evidenceUrl")) or FAILURE_NO_EVIDENCE_TEXT),
        ]
        if cell["attachments"]:
            lines.append(f"证据附件：{cell['attachments']}")
        story.append(Paragraph(_inline_markup("　".join(lines)), styles["body"]))
        if cell["summary"]:
            story.append(Preformatted(cell["summary"], styles["code"]))
        story.append(Spacer(1, 6))
    return story


# ---------------- 章节解析 ----------------

_SECTION_BY_LABEL = {label: key for key, label in SECTION_LABELS.items()}

_SECTION_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("用例", "case"),
    ("case", "case"),
    ("缺陷", "bug"),
    ("bug", "bug"),
    ("覆盖", "coverage"),
    ("风险", "risk"),
    ("结论", "conclusion"),
    ("建议", "conclusion"),
    ("概览", "overview"),
    ("总览", "overview"),
    ("概述", "overview"),
    ("摘要", "overview"),
)


def _section_key(title: str) -> str:
    """把章节标题映射到内置章节；认不出来时归入总览。"""
    text = title.strip()
    if text in _SECTION_BY_LABEL:
        return _SECTION_BY_LABEL[text]
    lowered = text.lower()
    for keyword, key in _SECTION_KEYWORDS:
        if keyword in lowered:
            return key
    return "overview"


def _extract_metric_table(text: str) -> dict[str, str]:
    """从正文里抽出「指标 / 值」表格，作为章节指标。"""
    lines = text.splitlines()
    metric: dict[str, str] = {}
    for index, line in enumerate(lines):
        if not _METRIC_TABLE_RE.match(line):
            continue
        for row in lines[index + 1:]:
            stripped = row.strip()
            if not stripped.startswith("|"):
                break
            if _TABLE_SEP_RE.match(stripped):
                continue
            cells = [cell.strip().strip("*` ") for cell in stripped.strip("|").split("|")]
            if len(cells) < 2 or not cells[0] or cells[0] == "指标":
                continue
            metric[cells[0]] = cells[1]
    return metric


def parse_sections(markdown_text: str) -> list[dict[str, Any]]:
    """把 Markdown 的二级标题拆成报告章节，并抽出各章节的指标表。"""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal current, body
        if current is not None:
            text = "\n".join(body).strip()
            current["content"] = text
            current["metric"] = _extract_metric_table(text)
            sections.append(current)
        current = None
        body = []

    for line in markdown_text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading and len(heading.group(1)) == 2:
            flush()
            title = heading.group(2).strip()
            current = {"section": _section_key(title), "title": title}
            continue
        if current is None:
            continue
        body.append(line)
    flush()
    return sections


# ---------------- Markdown ----------------

def render_markdown(
    report: dict[str, Any],
    items: list[dict[str, Any]],
    samples: list[dict[str, Any]] | None = None,
) -> str:
    """渲染 Markdown：frontmatter + 元信息 + 章节 + 失败样本证据 + 正文。"""
    title = str(report.get("title") or "测试报告")
    frontmatter = "\n".join([
        "---",
        f"report_no: {report.get('report_no') or report.get('reportNo') or ''}",
        f"title: {title}",
        f"report_type: {report.get('report_type') or report.get('reportType') or ''}",
        f"period_start: {report.get('period_start') or report.get('periodStart') or ''}",
        f"period_end: {report.get('period_end') or report.get('periodEnd') or ''}",
        f"status: {report.get('status') or ''}",
        f"updated_at: {report.get('updated_at') or report.get('updatedAt') or ''}",
        f"exported_at: {utcnow_iso()}",
        "---",
    ])
    blocks: list[str] = [frontmatter, f"# {title}"]

    meta_lines = [f"- **{name}**：{value}" for name, value in _meta_pairs(report) if value]
    if meta_lines:
        blocks.append("\n".join(meta_lines))

    summary = str(report.get("summary") or "").strip()
    if summary:
        blocks.append(f"## 摘要\n\n{summary}")

    blocks.extend(_sections_markdown(items))

    evidence_block = failure_markdown_section(normalize_failure_samples(samples))
    if evidence_block:
        blocks.append(evidence_block.rstrip())

    content = _residual_content(str(report.get("content") or ""), items)
    if content:
        blocks.append(f"## 正文\n\n{content}")

    return "\n\n".join(blocks).rstrip() + "\n"


# ---------------- HTML ----------------

_HTML_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 32px 40px; max-width: 900px;
  font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
  color: #1f2329; line-height: 1.75; background: #fff;
}
h1 { font-size: 26px; margin: 0 0 12px; }
h2 { font-size: 19px; margin: 28px 0 10px; padding-bottom: 6px; border-bottom: 1px solid #e5e6eb; }
h3 { font-size: 16px; margin: 20px 0 8px; }
p { margin: 8px 0; }
ul, ol { padding-left: 24px; }
li { margin: 3px 0; }
code { background: #f2f3f5; padding: 1px 5px; border-radius: 3px; font-size: 13px; }
pre { background: #f7f8fa; padding: 12px 14px; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
th, td { border: 1px solid #dcdfe6; padding: 7px 10px; text-align: left; }
th { background: #daeefc; font-weight: 600; }
blockquote { margin: 10px 0; padding: 6px 14px; border-left: 3px solid #daeefc; color: #606266; }
hr { border: none; border-top: 1px solid #e5e6eb; margin: 20px 0; }
.meta { margin: 0 0 20px; padding: 12px 16px; background: #f7f8fa; border-radius: 6px; font-size: 14px; }
.meta div { margin: 3px 0; }
.footer { margin-top: 36px; padding-top: 12px; border-top: 1px solid #e5e6eb; color: #909399; font-size: 12px; }
.evidence-block { margin: 10px 0; padding: 8px 12px; border-left: 3px solid #f0a020; background: #fcfaf5; }
.evidence-block pre { white-space: pre-wrap; }
.evidence-empty { color: #909399; }
@media print {
  body { padding: 0; max-width: none; font-size: 12pt; }
  h2 { page-break-after: avoid; }
  table, pre { page-break-inside: avoid; }
  .no-print { display: none; }
}
"""


def _escape_html_tags(text: str) -> str:
    """转义正文里的原始 HTML 标签，避免导出的文件里出现可执行脚本。"""
    return _HTML_TAG_RE.sub(lambda match: html_lib.escape(match.group(0)), text)


def _markdown_to_html(markdown_text: str) -> str:
    import markdown as markdown_lib

    return str(
        markdown_lib.markdown(
            markdown_text,
            extensions=["extra", "sane_lists", "nl2br"],
        )
    )


def _meta_html(report: dict[str, Any]) -> str:
    rows = []
    for name, value in _meta_pairs(report):
        if value:
            rows.append(f"<div><strong>{html_lib.escape(name)}</strong>：{html_lib.escape(value)}</div>")
    return f'<div class="meta">{"".join(rows)}</div>'


def render_html(
    report: dict[str, Any],
    items: list[dict[str, Any]],
    print_view: bool = False,
    samples: list[dict[str, Any]] | None = None,
) -> str:
    """渲染单文件 HTML；print_view=True 时打开即进入打印友好布局。

    ``samples`` 是 GAP-35 的失败样本快照：这里单独渲染一份 HTML 表格，
    不走 Markdown 通道，所以 md 侧章节出问题也不会连累 html 取证。
    """
    title = str(report.get("title") or "测试报告")
    blocks: list[str] = []
    summary = str(report.get("summary") or "").strip()
    if summary:
        blocks.append("## 摘要\n\n" + summary)
    for item in _items_by_section(items):
        heading = str(item.get("title") or "").strip() or _label(SECTION_LABELS, item.get("section"))
        parts = [f"## {heading}"]
        content = str(item.get("content") or "").strip()
        if content:
            parts.append(content)
        rows = _metric_rows(item.get("metric"))
        if rows and not _has_metric_table(content):
            table = ["| 指标 | 值 |", "| --- | --- |"]
            table.extend(f"| {name} | {value} |" for name, value in rows)
            parts.append("\n".join(table))
        blocks.append("\n\n".join(parts))
    content = _residual_content(str(report.get("content") or ""), items)
    if content:
        blocks.append("## 正文\n\n" + content)

    body = "\n\n".join(_markdown_to_html(_escape_html_tags(block)) for block in blocks)
    evidence_html = failure_html_section(normalize_failure_samples(samples))
    action = ""
    if print_view:
        action = (
            '<div class="no-print" style="margin:16px 0">'
            '<button onclick="window.print()" style="padding:6px 16px;font-size:14px;cursor:pointer">'
            "打印 / 另存为 PDF</button></div>"
        )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{html_lib.escape(title)}</title>\n"
        f"<style>{_HTML_CSS}</style>\n</head>\n<body>\n"
        f"<h1>{html_lib.escape(title)}</h1>\n{_meta_html(report)}\n{action}\n{body}\n"
        f"{evidence_html}\n"
        f'<div class="footer">由 AITestPlatform 导出于 {utcnow_iso()}</div>\n'
        "</body>\n</html>\n"
    )


# ---------------- PDF ----------------

#: 系统字体目录：Windows 走 SystemRoot，macOS 走三个标准库目录，Linux 走 fontconfig 常用位置。
_FONT_DIRS: dict[str, tuple[str, ...]] = {
    "Windows": (
        "%SystemRoot%\\Fonts",
        "%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts",
    ),
    "Darwin": (
        "/System/Library/Fonts",
        "/System/Library/Fonts/Supplemental",
        "/Library/Fonts",
        "~/Library/Fonts",
    ),
    "Linux": (
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        "~/.fonts",
        "~/.local/share/fonts",
    ),
}


def _font_dirs() -> list[Path]:
    system = platform.system()
    names = _FONT_DIRS.get(system) or _FONT_DIRS["Linux"]
    dirs: list[Path] = []
    for raw in names:
        expanded = os.path.expandvars(os.path.expanduser(raw))
        path = Path(expanded)
        if path.is_dir() and path not in dirs:
            dirs.append(path)
    return dirs


def _cjk_font_files() -> list[Path]:
    """按候选顺序列出本机存在的中文字体文件。

    只按文件名匹配、不读字体内容，所以顺序里黑体类在前就能保证 PDF 首选无衬线；
    目录不存在时直接跳过，未知系统按 Linux 位置尝试。
    """
    dirs = _font_dirs()
    found: list[Path] = []
    seen: set[str] = set()
    for pattern in _FONT_PATTERNS:
        for directory in dirs:
            try:
                candidates = sorted(directory.rglob(pattern))
            except OSError:
                continue
            for candidate in candidates:
                key = str(candidate).casefold()
                if key in seen or not candidate.is_file():
                    continue
                seen.add(key)
                found.append(candidate)
    return found


_font_result: tuple[str, bool, str] | None = None


def _cjk_font() -> tuple[str, bool, str]:
    """返回 (字体名, 是否降级, 命中的字体文件)，结果按进程缓存。"""
    global _font_result
    if _font_result is not None:
        return _font_result

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    name, degraded, resolved = "Helvetica", True, ""
    for path in _cjk_font_files():
        try:
            if path.suffix.lower() in {".ttc", ".otc"}:
                pdfmetrics.registerFont(TTFont(_FONT_NAME, str(path), subfontIndex=0))
            else:
                pdfmetrics.registerFont(TTFont(_FONT_NAME, str(path)))
        except Exception:  # 字体损坏、CFF 轮廓或格式不支持时继续尝试下一个
            continue
        pdfmetrics.registerFontFamily(
            _FONT_NAME, normal=_FONT_NAME, bold=_FONT_NAME, italic=_FONT_NAME, boldItalic=_FONT_NAME
        )
        name, degraded, resolved = _FONT_NAME, False, str(path)
        break
    _font_result = (name, degraded, resolved)
    return _font_result


def _register_cjk_font() -> tuple[str, bool]:
    """注册中文字体，返回 (字体名, 是否降级告警)。"""
    registered = _cjk_font()
    return registered[0], registered[1]


def _inline_markup(text: str) -> str:
    """把 Markdown 行内标记转成 reportlab Paragraph 支持的标签。"""
    escaped = html_lib.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", escaped)
    escaped = re.sub(
        r"`([^`]+)`",
        r'<font color="#b03060">\1</font>',
        escaped,
    )
    return escaped


def _pdf_flowables(
    markdown_text: str,
    styles: dict[str, Any],
    font_name: str,
) -> list[Any]:
    """把 Markdown 文本拆成 reportlab 可排版的元素。"""
    from reportlab.platypus import (HRFlowable, ListFlowable, ListItem, Paragraph,
                                    Preformatted, Spacer, Table, TableStyle)

    flowables: list[Any] = []
    lines = markdown_text.splitlines()
    index = 0
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_buffer:
            return
        body = " ".join(part.strip() for part in paragraph_buffer).strip()
        paragraph_buffer.clear()
        if body:
            flowables.append(Paragraph(_inline_markup(body), styles["body"]))

    while index < len(lines):
        line = lines[index]
        fence = _FENCE_RE.match(line)
        if fence:
            flush_paragraph()
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not _FENCE_RE.match(lines[index]):
                code_lines.append(lines[index])
                index += 1
            index += 1
            flowables.append(Preformatted("\n".join(code_lines), styles["code"]))
            flowables.append(Spacer(1, 6))
            continue

        if not line.strip():
            flush_paragraph()
            index += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            level = min(len(heading.group(1)), 6)
            flowables.append(Paragraph(_inline_markup(heading.group(2)), styles[f"h{level}"]))
            index += 1
            continue

        if _HR_RE.match(line):
            flush_paragraph()
            flowables.append(HRFlowable(width="100%", color="#dcdfe6", spaceBefore=6, spaceAfter=10))
            index += 1
            continue

        if line.strip().startswith("|"):
            flush_paragraph()
            table_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            cells = [[_inline_markup(cell.strip()) for cell in row.strip("|").split("|")] for row in table_lines]
            cells = [row for row in cells if not all(_TABLE_SEP_RE.match(row_text) for row_text in row)]
            if not cells:
                continue
            width = max(len(row) for row in cells)
            for row in cells:
                row.extend([""] * (width - len(row)))
            table = Table(cells, hAlign="LEFT")
            table.setStyle(
                TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.4, "#dcdfe6"),
                    ("BACKGROUND", (0, 0), (-1, 0), "#daeefc"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ])
            )
            flowables.append(table)
            flowables.append(Spacer(1, 8))
            continue

        if _UL_RE.match(line) or _OL_RE.match(line):
            flush_paragraph()
            ordered = bool(_OL_RE.match(line))
            bullets: list[Any] = []
            while index < len(lines):
                current = lines[index]
                match = _OL_RE.match(current) if ordered else _UL_RE.match(current)
                if not match:
                    break
                text = match.group(2) if ordered else match.group(1)
                bullets.append(ListItem(Paragraph(_inline_markup(text), styles["body"])))
                index += 1
            flowables.append(ListFlowable(bullets, bulletType="1" if ordered else "bullet"))
            flowables.append(Spacer(1, 6))
            continue

        quote = _QUOTE_RE.match(line)
        if quote:
            flush_paragraph()
            flowables.append(Paragraph(_inline_markup(quote.group(1)), styles["quote"]))
            index += 1
            continue

        paragraph_buffer.append(line)
        index += 1

    flush_paragraph()
    return flowables


def render_pdf(
    report: dict[str, Any],
    items: list[dict[str, Any]],
    samples: list[dict[str, Any]] | None = None,
) -> tuple[bytes, bool]:
    """渲染 PDF，返回 (字节流, 字体降级告警)。"""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font_name, degraded = _register_cjk_font()
    base = getSampleStyleSheet()
    styles: dict[str, Any] = {}
    for level in range(1, 7):
        styles[f"h{level}"] = ParagraphStyle(
            f"AitpH{level}",
            parent=base["Heading1"] if level == 1 else base["BodyText"],
            fontName=font_name,
            fontSize={1: 18, 2: 14, 3: 12}.get(level, 11),
            leading={1: 24, 2: 20, 3: 17}.get(level, 15),
            spaceBefore=12 if level > 1 else 0,
            spaceAfter=6,
            textColor=colors.HexColor("#1f2329"),
        )
    styles["body"] = ParagraphStyle(
        "AitpBody",
        parent=base["BodyText"],
        fontName=font_name,
        fontSize=10,
        leading=16,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    styles["code"] = ParagraphStyle(
        "AitpCode",
        parent=base["Code"],
        fontName=font_name,
        fontSize=9,
        leading=13,
        backColor=colors.HexColor("#f7f8fa"),
        borderPadding=6,
    )
    styles["quote"] = ParagraphStyle(
        "AitpQuote",
        parent=styles["body"],
        leftIndent=12,
        textColor=colors.HexColor("#606266"),
    )

    title = str(report.get("title") or "测试报告")
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="AITestPlatform",
    )

    story: list[Any] = [Paragraph(_inline_markup(title), styles["h1"])]
    meta_text = "　".join(
        f"{name}：{value}" for name, value in _meta_pairs(report) if value
    )
    if meta_text:
        story.append(Paragraph(_inline_markup(meta_text), styles["body"]))
        story.append(Spacer(1, 8))

    summary = str(report.get("summary") or "").strip()
    if summary:
        story.append(Paragraph("摘要", styles["h2"]))
        story.extend(_pdf_flowables(summary, styles, font_name))

    for item in _items_by_section(items):
        heading = str(item.get("title") or "").strip() or _label(SECTION_LABELS, item.get("section"))
        story.append(Paragraph(_inline_markup(heading), styles["h2"]))
        content = str(item.get("content") or "").strip()
        if content:
            story.extend(_pdf_flowables(content, styles, font_name))
        rows = _metric_rows(item.get("metric"))
        if rows and not _has_metric_table(content):
            table_data = [["指标", "值"]] + [
                [_inline_markup(name), _inline_markup(value)] for name, value in rows
            ]
            table = _pdf_table(table_data, font_name)
            story.append(table)
            story.append(Spacer(1, 8))

    content = _residual_content(str(report.get("content") or ""), items)
    if content:
        story.append(Paragraph("正文", styles["h2"]))
        story.extend(_pdf_flowables(content, styles, font_name))

    # GAP-35：失败样本证据章节在 PDF 里单独排一次，不借道 Markdown
    story.extend(failure_pdf_flowables(normalize_failure_samples(samples), styles, font_name))

    story.append(Spacer(1, 14))
    story.append(
        Paragraph(f"由 AITestPlatform 导出于 {utcnow_iso()}", styles["quote"])
    )

    def _footer(canvas: Any, doc_obj: Any) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.HexColor("#909399"))
        canvas.drawString(20 * mm, 12 * mm, "AITestPlatform")
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"第 {doc_obj.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue(), degraded


def _pdf_table(rows: list[list[str]], font_name: str) -> Any:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, hAlign="LEFT")
    table.setStyle(
        TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dcdfe6")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#daeefc")),
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    return table
