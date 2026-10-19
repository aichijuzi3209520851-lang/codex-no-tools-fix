# -*- coding: utf-8 -*-
"""生成双击启动器 .bat（纯 ASCII）。

编码踩坑记录（两次都真踩了）：
  1. 用 UTF-8 写 .bat → cmd.exe 按 OEM 代码页(936)解析文件字节 → 乱码、命令找不到。
  2. 用 cp936 写 .bat → bat 自身能跑，但 Python 输出的 UTF-8 中文在 936 控制台里是乱码。
  3. bat 里写中文 + 开头 chcp 65001 → 仍然不可靠，cmd 是在 chcp 生效前就按旧代码页
     切分文件字节的，多字节字符会把命令边界切坏（实测 `'（测试）' 不是内部或外部命令`）。

结论：.bat 只放 ASCII，所有中文都交给 Python 打印（python 侧已经 reconfigure 成 utf-8），
bat 开头先 chcp 65001。这样双击后中文显示正常。
"""
import os

BAT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "双击运行-修复Codex工具.bat")

LINES = [
    "@echo off",
    "chcp 65001 >nul 2>&1",
    "setlocal",
    "title Codex Tool Fix",
    "cd /d \"%~dp0\"",
    "set \"RC=0\"",
    "",
    "echo.",
    "echo ============================================================",
    "echo    Codex Desktop Tool Fix  /  One-click repair",
    "echo ============================================================",
    "echo.",
    "",
    "REM ---------- locate Python 3.11+ ----------",
    "set \"PY=\"",
    "where py >nul 2>&1",
    "if not errorlevel 1 (",
    "    py -3.11 -c \"import sys\" >nul 2>&1",
    "    if not errorlevel 1 set \"PY=py -3.11\"",
    ")",
    "if not defined PY (",
    "    where python >nul 2>&1",
    "    if not errorlevel 1 set \"PY=python\"",
    ")",
    "if not defined PY (",
    "    if exist \"D:\\python\\python.exe\" set \"PY=D:\\python\\python.exe\"",
    ")",
    "if not defined PY (",
    "    if exist \"%LOCALAPPDATA%\\Programs\\Python\\Python311\\python.exe\" (",
    "        set \"PY=%LOCALAPPDATA%\\Programs\\Python\\Python311\\python.exe\"",
    "    )",
    ")",
    "",
    "if not defined PY goto nopython",
    "",
    "echo    Python: %PY%",
    "echo.",
    "",
    "REM ---------- run ----------",
    "set PYTHONIOENCODING=utf-8",
    "%PY% \"%~dp0fix_codex_tools.py\" %*",
    "set \"RC=%ERRORLEVEL%\"",
    "goto done",
    "",
    ":nopython",
    "echo [ERROR] Python 3.11+ not found.",
    "echo.",
    "echo   Install it from https://www.python.org/downloads/",
    "echo   and tick \"Add Python to PATH\" during setup.",
    "echo.",
    "set \"RC=2\"",
    "goto done",
    "",
    ":done",
    "echo.",
    "echo ============================================================",
    "if \"%RC%\"==\"0\" goto ok",
    "if \"%RC%\"==\"1\" goto warn",
    "echo   [FAILED] exit code %RC%",
    "goto end",
    ":ok",
    "echo   [OK] done",
    "goto end",
    ":warn",
    "echo   [WARN] some checks failed - see [ERR] lines above",
    "goto end",
    "",
    ":end",
    "echo ============================================================",
    "echo.",
    "",
    "REM pause only when double-clicked (no arguments)",
    "if \"%~1\"==\"\" (",
    "    pause",
    ")",
    "exit /b %RC%",
    "",
]


def main():
    text = "\r\n".join(LINES)
    raw = text.encode("ascii")          # 纯 ASCII，任何代码页都不会被切坏
    with open(BAT, "wb") as f:
        f.write(raw)
    print("written:", BAT)
    print("  pure ASCII, CRLF, %d lines, %d bytes" % (len(LINES), len(raw)))
    back = open(BAT, "rb").read()
    assert back.count(b"\n") == back.count(b"\r\n"), "mixed line endings"
    back.decode("ascii")
    assert back.count(b"%RC%") >= 3
    print("  self-check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
