#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 端到端验证：桌面快捷方式 -> .bat -> fix_codex_tools.py
===============================================================================

为什么要单独写这个：
  直接 `Start-Process <lnk> -RedirectStandardOutput` 会失败并报
  "%1 不是有效的 Win32 应用程序"——因为一旦要求重定向输出，PowerShell 就必须
  走 UseShellExecute=false，而 CreateProcess 不接受 .lnk。这是测试手段的限制，
  不是快捷方式坏了。

  资源管理器双击 .lnk 时，ShellExecuteEx 做的事是：
    读取 .lnk 的 TargetPath / WorkingDirectory / Arguments
    -> 用 WorkingDirectory 作为 cwd 启动 TargetPath
  所以忠实复现 = 把这三项读出来，按它原样执行。

本脚本：
  1. 用 WScript.Shell 读出 .lnk 的 TargetPath / WorkingDirectory / Arguments
  2. 断言 TargetPath 就是 .bat、WorkingDirectory 就是脚本目录
  3. 以该 WorkingDirectory 为 cwd 执行 TargetPath + "--dry-run"
     （带参数时 .bat 不会 pause，可后台跑，不弹任何窗口）
  4. 校验退出码、stdout 是合法 UTF-8、关键中文提示出现、报告文件已生成

复现：python _verify_shortcut_e2e.py
===============================================================================
"""

import ctypes
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BAT_NAME = "双击运行-修复Codex工具.bat"
LNK_NAME = "Codex 工具修复.lnk"

EXPECT = [
    "Codex Desktop Tool Fix",
    "没有任何文件需要修改",
    "全部检查通过",
]


def ensure_utf8_console():
    if os.name != "nt":
        return
    try:
        k32 = ctypes.windll.kernel32
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001
        pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def desktop_path():
    probe = os.path.join(os.environ.get("TEMP", "."), "_desk2.txt")
    ps = ("$p = [Environment]::GetFolderPath('Desktop');"
          "[IO.File]::WriteAllText($env:OUT, $p, (New-Object Text.UTF8Encoding($false)))")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, env=dict(os.environ, OUT=probe), timeout=90)
    with open(probe, "r", encoding="utf-8-sig") as fh:
        p = fh.read().strip()
    os.remove(probe)
    return p


def read_lnk(lnk):
    out = os.path.join(os.environ.get("TEMP", "."), "_lnk2.json")
    ps = r"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK)
$o = [ordered]@{
  target      = $s.TargetPath
  workdir     = $s.WorkingDirectory
  arguments   = $s.Arguments
  icon        = $s.IconLocation
}
[IO.File]::WriteAllText($env:OUT, ($o | ConvertTo-Json -Compress),
                        (New-Object Text.UTF8Encoding($false)))
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, env=dict(os.environ, LNK=lnk, OUT=out),
                   timeout=120)
    with open(out, "r", encoding="utf-8-sig") as fh:
        d = json.load(fh)
    os.remove(out)
    return d


def main():
    ensure_utf8_console()
    print("=" * 72)
    print(" 端到端验证：快捷方式 -> bat -> fix_codex_tools.py")
    print("=" * 72)

    ok = True

    desk = desktop_path()
    lnk = os.path.join(desk, LNK_NAME)
    print("\n[1/4] 读取桌面快捷方式")
    print("      路径 : %s" % lnk)
    if not os.path.isfile(lnk):
        print("      不存在 <<<")
        print("\nRESULT: FAILED")
        return 1

    info = read_lnk(lnk)
    want_bat = os.path.join(HERE, BAT_NAME)

    tgt = info.get("target") or ""
    good_t = os.path.normcase(tgt) == os.path.normcase(want_bat)
    ok &= good_t
    print("      目标 : %s" % ("对" if good_t else "错 <<< " + tgt))

    wd = info.get("workdir") or ""
    good_w = os.path.normcase(wd) == os.path.normcase(HERE)
    ok &= good_w
    print("      起始位置 : %s" % ("对" if good_w else "错 <<< " + wd))

    args = (info.get("arguments") or "").strip()
    print("      参数 : %s" % (args if args else "(无，双击后走交互式 pause)"))

    icon = (info.get("icon") or "").strip()
    good_i = os.path.normcase(icon.split(",")[0]) == os.path.normcase(
        os.path.join(HERE, "assets", "codex-tool-fix.ico"))
    ok &= good_i
    print("      图标 : %s" % ("对" if good_i else "错 <<< " + icon))

    # ---------------- 2. 按 ShellExecuteEx 的语义执行 ----------------
    print("\n[2/4] 以快捷方式的 WorkingDirectory 为 cwd 执行目标脚本（--dry-run）")
    print("      注意：带参数时 .bat 不 pause，全程后台，不弹窗")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cmd = ["cmd", "/c", tgt, "--dry-run"]
    try:
        p = subprocess.run(cmd, cwd=wd or HERE, env=env,
                           capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        print("      超时 <<<")
        print("\nRESULT: FAILED")
        return 1

    raw = p.stdout
    print("      退出码 : %d  %s" % (p.returncode, "0 为成功" if p.returncode == 0 else "<<<"))
    if p.returncode != 0:
        ok = False

    # ---------------- 3. 输出解码与内容 ----------------
    print("\n[3/4] 输出校验")
    try:
        text = raw.decode("utf-8")
        print("      UTF-8 解码 : 成功（%d 字节）" % len(raw))
    except UnicodeDecodeError as exc:
        text = raw.decode("utf-8", "replace")
        ok = False
        print("      UTF-8 解码 : 失败 <<< %s" % exc)

    if raw.startswith(b"\xef\xbb\xbf"):
        ok = False
        print("      BOM : 有 <<<")
    else:
        print("      BOM : 无")

    for needle in EXPECT:
        hit = needle in text
        ok &= hit
        print("      含「%s」: %s" % (needle, "是" if hit else "否 <<<"))

    if p.stderr.strip():
        err = p.stderr.decode("utf-8", "replace")
        bad = [ln for ln in err.splitlines()
               if "Traceback" in ln or "Error" in ln or "error" in ln]
        print("      stderr 行数 : %d%s" % (len(err.splitlines()),
                                          "  可疑 %d 行 <<<" % len(bad) if bad else ""))
        if bad:
            ok = False
            for ln in bad[:5]:
                print("        " + ln.strip()[:110])

    # ---------------- 4. 报告文件 ----------------
    print("\n[4/4] 产物检查")
    reports = sorted(f for f in os.listdir(HERE)
                     if f.startswith("last-fix-report-"))
    if reports:
        newest = reports[-1]
        size = os.path.getsize(os.path.join(HERE, newest))
        print("      最新报告 : %s  (%d 字节)" % (newest, size))
        if size <= 0:
            ok = False
            print("      报告为空 <<<")
    else:
        ok = False
        print("      没有生成报告 <<<")

    print("\n" + "=" * 72)
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
