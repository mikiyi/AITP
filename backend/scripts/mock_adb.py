"""本地 mock adb（V2.7.0 App 场景 E2E / 门禁用）。

不依赖真机与 Android SDK：按 adb 的参数形态返回确定性输出，让「场景步骤 →
adb 命令 → 步骤轨迹 → 缺陷待审」整条链路可以离线验证。

用法：把「设置 · App 执行配置」里的 adb 命令指向
    <python> backend/scripts/mock_adb.py
设备序列号填 E2E123（或不填）。

行为：
  - version / devices：返回固定版本与一台在线设备；
  - shell am start / monkey / input / uiautomator dump：返回成功文本；
  - exec-out cat：返回一段含「登录成功」的界面结构；
  - exec-out screencap：返回 1x1 PNG。
"""

from __future__ import annotations

import sys

_UI_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node text="欢迎使用 AITP" resource-id="" class="android.widget.TextView"
        content-desc="" bounds="[40,120][600,200]" />
  <node text="登录成功" resource-id="com.example.app:id/title" class="android.widget.TextView"
        content-desc="" bounds="[40,240][600,320]" />
  <node text="登录" resource-id="com.example.app:id/login" class="android.widget.Button"
        content-desc="" bounds="[40,400][600,500]" />
</hierarchy>
"""

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f0300050001a5f645400000000049454e44ae426082"
)


def main(argv: list[str]) -> int:
    args = [item for item in argv[1:]]
    # 去掉 -s <serial>
    while "-s" in args:
        index = args.index("-s")
        del args[index:index + 2]
    if not args:
        print("AITP mock adb")
        return 0
    if args[0] == "version":
        print("Android Debug Bridge version 1.0.41 (AITP mock)")
        return 0
    if args[0] == "devices":
        print("List of devices attached")
        print("E2E123\tdevice")
        return 0
    if args[0] == "exec-out":
        command = args[1] if len(args) > 1 else ""
        if command == "cat":
            sys.stdout.write(_UI_XML)
            return 0
        if command == "screencap":
            sys.stdout.buffer.write(_PNG)
            return 0
        return 0
    if args[0] == "shell":
        command = args[1] if len(args) > 1 else ""
        if command == "uiautomator":
            print("UI hierarchy dumped to: /sdcard/aitp_window.xml")
            return 0
        if command == "am":
            print("Starting: Intent { cmp=com.example.app/.MainActivity }")
            return 0
        if command == "monkey":
            print("Events injected: 1")
            return 0
        if command in ("input", "getprop", "wm", "dumpsys"):
            return 0
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
