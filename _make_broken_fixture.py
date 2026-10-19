#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""造一个"坏掉"的 Codex 环境，用来回归测试 fix_codex_tools.py 是否真能修好。
仅用于测试，不参与修复流程。

坏环境包含三类典型问题：
  1. config.toml 没有 model_catalog_json（模型走内置 lite 目录 → 没有工具）
  2. config.toml 带无效键 network_access（启动刷告警）
  3. config.toml / 插件缓存里残留已失效的版本目录 hash（node_repl 起不来）
"""
import json
import os
import shutil
import sys
import tomllib

BASE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(BASE, "_tab")
BS = "\\"

DEAD_CUA = "a5a02988362ca169"
DEAD_BIN = "e78bad9887fa50cf"
GOOD_CUA = "78fb63cda1a9e2b2"
GOOD_BIN = "896a0166b774037a"

CODEX_ROOT = os.path.join(os.path.expanduser("~"), "AppData", "Local", "OpenAI", "Codex")


def toml_esc(s):
    """把 Windows 路径塞进 TOML 双引号基本字符串：反斜杠要双写。"""
    return s.replace(BS, BS + BS)


def main():
    shutil.rmtree(HOME, ignore_errors=True)
    os.makedirs(os.path.join(HOME, "plugins", "cache", "t", "_x"), exist_ok=True)

    # 失效的 cua_node 目录（真实存在的目录是 78fb63cda1a9e2b2）
    dead_cua_dir = os.path.join(CODEX_ROOT, "runtimes", "cua_node", DEAD_CUA)
    notify_exe = os.path.join(dead_cua_dir, "bin", "node_modules", "@oai", "sky",
                              "bin", "windows", "codex-computer-use.exe")
    dead_cli = os.path.join(CODEX_ROOT, "bin", DEAD_BIN, "codex.exe")
    dead_node = os.path.join(dead_cua_dir, "bin", "node.exe")

    cfg = (
        'model = "gpt-6-sol"\n'
        'model_provider = "custom"\n'
        'model_reasoning_effort = "high"\n'
        'approval_policy = "never"\n'
        'sandbox_mode = "danger-full-access"\n'
        'network_access = "enabled"\n'
        'notify = [ "' + toml_esc(notify_exe) + '", "turn-ended" ]\n'
        '\n'
        '[mcp_servers.node_repl]\n'
        "command = '" + os.path.join(dead_cua_dir, "bin", "node_repl.exe") + "'\n"
        '\n'
        '[mcp_servers.node_repl.env]\n'
        "NODE_REPL_NODE_PATH = '" + dead_node + "'\n"
        "CODEX_CLI_PATH = '" + dead_cli + "'\n"
        '\n'
        '[model_providers.custom]\n'
        'name = "custom"\n'
        'base_url = "http://127.0.0.1:15902/v1"\n'
        'wire_api = "responses"\n'
        'experimental_bearer_token = "PROXY_MANAGED"\n'
        'requires_openai_auth = false\n'
    )
    cfg_path = os.path.join(HOME, "config.toml")
    with open(cfg_path, "w", encoding="utf-8", newline="") as f:
        f.write(cfg)

    mcp_obj = {
        "mcpServers": {
            "node_repl": {
                "command": dead_node,
                "args": ["node_repl.js"],
                "env": {"CODEX_CLI_PATH": dead_cli},
            }
        }
    }
    mcp_path = os.path.join(HOME, "plugins", "cache", "t", "_x", ".mcp.json")
    with open(mcp_path, "w", encoding="utf-8", newline="") as f:
        json.dump(mcp_obj, f, indent=2)

    # ---- 自检
    with open(cfg_path, "rb") as f:
        d = tomllib.load(f)
    json.loads(open(mcp_path, encoding="utf-8").read())

    print("已生成坏环境:", HOME)
    print("  TOML 合法，顶层键      :", list(d.keys()))
    print("  network_access 存在    :", "network_access" in d, "（期望 True）")
    print("  model_catalog_json 存在:", "model_catalog_json" in d, "（期望 False）")
    print("  notify                 :", d.get("notify"))
    cfg_txt = open(cfg_path, encoding="utf-8").read()
    mcp_txt = open(mcp_path, encoding="utf-8").read()
    print("  失效 cua hash 出现     :", cfg_txt.count(DEAD_CUA), "+", mcp_txt.count(DEAD_CUA),
          "（期望都 > 0）")
    print("  失效 bin hash 出现     :", cfg_txt.count(DEAD_BIN), "+", mcp_txt.count(DEAD_BIN))
    print("  catalog 文件存在       :",
          os.path.exists(os.path.join(HOME, "codex-catalog-classic.json")), "（期望 False）")
    print("  应被修成               :", GOOD_CUA, "/", GOOD_BIN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
