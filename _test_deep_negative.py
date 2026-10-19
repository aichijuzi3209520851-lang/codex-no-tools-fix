# -*- coding: utf-8 -*-
"""负向测试：确认 --deep 的深度验证在"真的没有工具"时会失败（不是假阳性）。

造一个 fake CODEX_HOME：
  - config.toml pin 到一个 **lite 版** catalog（use_responses_lite=True + tool_mode=code_mode_only）
  - 这正是用户原来坏掉时的状态
然后直接调用 fix_codex_tools._deep_verify，它必须返回 False。
"""
import json
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
NEG = os.path.join(BASE, "_neg")


def _find_codex_exe():
    """复用主脚本的定位逻辑，避免把本机用户名硬编码进来。"""
    sys.path.insert(0, BASE)
    import fix_codex_tools as F
    exe, _src = F.find_codex_exe()
    if not exe:
        raise SystemExit("找不到 codex.exe，请确认 Codex Desktop 已安装")
    return exe


CODEX_EXE = _find_codex_exe()


def build():
    shutil.rmtree(NEG, ignore_errors=True)
    os.makedirs(NEG, exist_ok=True)

    # 1) 取内置目录（空 home 下就是原始的 lite 状态）
    empty = os.path.join(NEG, "_empty")
    os.makedirs(empty, exist_ok=True)
    env = dict(os.environ, CODEX_HOME=empty.replace("\\", "/"))
    p = subprocess.run([CODEX_EXE, "debug", "models"],
                       capture_output=True, env=env, timeout=180)
    models = json.loads(p.stdout.decode("utf-8", "replace"))["models"]

    # 2) 只保留 gpt-6-sol，并强制回 lite + tool_mode
    keep = []
    for m in models:
        if m.get("slug") == "gpt-6-sol":
            m = dict(m)
            m["use_responses_lite"] = True
            m["tool_mode"] = "code_mode_only"
            keep.append(m)
    assert keep, "没找到 gpt-6-sol"
    print("构造 lite 模型:", keep[0].get("slug"),
          "use_responses_lite=", keep[0].get("use_responses_lite"),
          "tool_mode=", keep[0].get("tool_mode"))

    cat = os.path.join(NEG, "lite-catalog.json")
    with open(cat, "w", encoding="utf-8") as f:
        json.dump({"models": keep}, f)

    cfg = '\n'.join([
        'model = "gpt-6-sol"',
        'model_provider = "custom"',
        'model_reasoning_effort = "high"',
        'approval_policy = "never"',
        'sandbox_mode = "danger-full-access"',
        "model_catalog_json = '%s'" % cat.replace("\\", "/"),
        '',
        '[model_providers.custom]',
        'name = "custom"',
        'base_url = "http://127.0.0.1:15902/v1"',
        'wire_api = "responses"',
        'experimental_bearer_token = "PROXY_MANAGED"',
        'requires_openai_auth = false',
        '',
    ])
    with open(os.path.join(NEG, "config.toml"), "w", encoding="utf-8") as f:
        f.write(cfg)
    shutil.rmtree(empty, ignore_errors=True)
    return NEG


def main():
    home = build()
    print("\n=== 用 lite(坏) 环境跑 _deep_verify，应当返回 False ===")
    code = (
        "import sys, json;"
        "sys.path.insert(0, r'%s');"
        "import fix_codex_tools as F;"
        "print('CODEX_HOME=', F.CODEX_HOME);"
        "r = F._deep_verify(r'%s');"
        "print('RESULT=', r)"
    ) % (BASE, CODEX_EXE)
    env = dict(os.environ, CODEX_HOME=home.replace("\\", "/"),
               PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       env=env, timeout=420)
    out = p.stdout.decode("utf-8", "replace")
    print(out)
    if p.returncode != 0:
        print("[stderr]", p.stderr.decode("utf-8", "replace")[-800:])

    passed = "RESULT= False" in out
    print("=" * 60)
    print("负向测试:", "PASS（正确判定为没有工具）" if passed else "FAIL（出现假阳性！）")
    shutil.rmtree(NEG, ignore_errors=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
