#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 Codex Desktop 一键修复工具  ——  "模型说我没有终端/文件工具"  BUG 修复
================================================================================

【这个 BUG 是什么】
Codex 0.163 的内置模型目录里，gpt-6-sol / gpt-6.1-sol 等模型带有两个字段：
    "use_responses_lite": true
    "tool_mode": "code_mode_only"
这会让 Codex 不把工具放在请求顶层的 tools 数组里，而是塞成 input 里的一条
    {"type": "additional_tools", ...}
第三方中转（CC-Switch / 任何 OpenAI 兼容 relay）不认识这个字段，
直接丢弃。于是模型收到的请求里一个工具都没有 —— 它只能说"我没有终端工具"。
模型看得见工具时才 100% 可用。

【怎么修】
把内置模型目录导出，逐条：
    use_responses_lite = false     ← 工具回到顶层 tools 数组
    删除 tool_mode                 ← 不再走 code-mode 精简工具集
写成 codex-catalog-classic.json，并在 config.toml 里 pin 住它。

顺带修掉第二类问题：Codex 升级后旧版本目录被清理，config.toml / 插件缓存里
残留失效的版本 hash（a5a02988... 之类）会导致 node_repl MCP 起不来。

【安全性】
- 每个被修改的文件都先备份成 xxx.bak-YYYYMMDD-HHMMSS
- 完全幂等：已经修好时再跑一遍，只会说"无需修改"
- 不联网、不重启任何程序、不弹窗

用法：双击同目录下的「双击运行-修复Codex工具.bat」
================================================================================
"""

import argparse
import datetime
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid

try:
    import tomllib
except ImportError:                                     # Python < 3.11
    tomllib = None

# ------------------------------------------------------------------ 常量

STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
HOME_DIR = os.path.expanduser("~")
REAL_CODEX_HOME = os.path.join(HOME_DIR, ".codex")
CODEX_HOME = os.environ.get("CODEX_HOME") or REAL_CODEX_HOME
CODEX_HOME = os.path.abspath(CODEX_HOME).replace("\\", "/")
REAL_CODEX_HOME = os.path.abspath(REAL_CODEX_HOME).replace("\\", "/")

CC_SWITCH_DIR = os.path.join(HOME_DIR, ".cc-switch")
CC_SWITCH_DB = os.path.join(CC_SWITCH_DIR, "cc-switch.db")

CATALOG_PATH = os.path.join(CODEX_HOME, "codex-catalog-classic.json").replace("\\", "/")
CC_CATALOG_PATH = os.path.join(CODEX_HOME, "cc-switch-model-catalog.json").replace("\\", "/")
CONFIG_PATH = os.path.join(CODEX_HOME, "config.toml")

OPENAI_CODEX_ROOT = os.path.join(HOME_DIR, "AppData", "Local", "OpenAI", "Codex")
CUA_NODE_ROOT = os.path.join(OPENAI_CODEX_ROOT, "runtimes", "cua_node")
BIN_ROOT = os.path.join(OPENAI_CODEX_ROOT, "bin")

# 需要扫描失效版本号的文本文件（相对 CODEX_HOME，或绝对路径）
DEAD_PATH_TARGETS = [
    os.path.join(CODEX_HOME, "config.toml"),
    os.path.join(CODEX_HOME, "chrome-native-hosts-v2.json"),
]

REPORT = []          # (level, text)
CHANGED = []         # 被真正修改的文件


def say(level, text):
    """level: OK / FIX / WARN / INFO / ERR"""
    tag = {"OK": "[OK]  ", "FIX": "[FIX] ", "WARN": "[WARN]", "INFO": "[..]  ", "ERR": "[ERR] "}[level]
    line = "%s %s" % (tag, text)
    print(line, flush=True)
    REPORT.append(line)


def head(text):
    print("", flush=True)
    print("=" * 78, flush=True)
    print("  " + text, flush=True)
    print("=" * 78, flush=True)


def backup(path):
    """备份成 path.bak-YYYYMMDD-HHMMSS，返回备份路径；已存在同名则跳过。"""
    if not os.path.exists(path):
        return None
    dst = "%s.bak-%s" % (path, STAMP)
    if os.path.exists(dst):
        return dst
    shutil.copy2(path, dst)
    return dst


def write_text(path, text, note=""):
    """写文件前先备份。返回 True 表示内容确实变了。"""
    old = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
            old = f.read()
    if old == text:
        return False
    bak = backup(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)
    if path not in CHANGED:
        CHANGED.append(path)
    say("FIX", "%s %s" % (os.path.basename(path), note or "已更新"))
    if bak:
        say("INFO", "  备份 → %s" % os.path.basename(bak))
    return True


# ------------------------------------------------------------------ 1. 定位 codex.exe

def find_codex_exe():
    """
    找 codex.exe，优先级：
      1) 环境变量 CODEX_EXE
      2) AppData\\Local\\OpenAI\\Codex\\bin\\<hash>\\codex.exe —— 取修改时间最新的
      3) PATH 里的 codex
    """
    env = os.environ.get("CODEX_EXE")
    if env and os.path.isfile(env):
        return env, "CODEX_EXE 环境变量"

    cands = glob.glob(os.path.join(BIN_ROOT, "*", "codex.exe"))
    if cands:
        cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return cands[0], "OpenAI/Codex/bin"

    which = shutil.which("codex")
    if which:
        return which, "PATH"

    return None, None


def run(cmd, timeout=180, env_extra=None, cwd=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, cwd=cwd)
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        return -1, "", "timeout after %ss" % timeout
    except Exception as e:                                # noqa: BLE001
        return -2, "", str(e)


# ------------------------------------------------------------------ 2. 生成模型目录

BUILTIN_REQUIRED = ("supported_reasoning_levels",)


def transform_models(models, source_label):
    """
    对每条模型做修复：use_responses_lite=False，删掉 tool_mode。
    返回 (新列表, 统计 dict)
    """
    out = []
    stats = {"total": 0, "lite_on": 0, "tool_mode": 0, "missing_field": []}
    for m in models:
        if not isinstance(m, dict) or not m.get("slug"):
            continue
        m = json.loads(json.dumps(m))          # 深拷贝
        stats["total"] += 1

        if m.get("use_responses_lite") is not False:
            if m.get("use_responses_lite") is True:
                stats["lite_on"] += 1
            m["use_responses_lite"] = False

        if "tool_mode" in m:
            stats["tool_mode"] += 1
            del m["tool_mode"]

        for req in BUILTIN_REQUIRED:
            if req not in m:
                stats["missing_field"].append("%s (%s)" % (m.get("slug"), req))

        out.append(m)
    return out, stats


def build_catalog(codex_exe):
    """
    用空 CODEX_HOME 跑 `codex debug models` 拿到内置目录（11 条），
    再把现有 catalog 里的自定义模型（如 u2-flash）合并进来。
    """
    sys.stdout.write("  正在从 codex 提取内置模型目录 ... ")
    sys.stdout.flush()

    tmp_home = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_home")
    os.makedirs(tmp_home, exist_ok=True)
    rc, out, err = run([codex_exe, "debug", "models"], timeout=120,
                       env_extra={"CODEX_HOME": tmp_home.replace("\\", "/")})
    if rc != 0 or not out.strip().startswith("{"):
        print("失败", flush=True)
        say("ERR", "codex debug models 失败 (exit=%s)" % rc)
        if err.strip():
            say("ERR", err.strip().splitlines()[-1][:300])
        return None

    try:
        builtin = json.loads(out)["models"]
    except Exception as e:                                # noqa: BLE001
        print("失败", flush=True)
        say("ERR", "解析 debug models 输出失败: %s" % e)
        return None
    print("OK (%d 条)" % len(builtin), flush=True)
    say("OK", "内置模型目录：共 %d 条" % len(builtin))

    # ---- 收集自定义模型（不在内置列表里的）
    builtin_slugs = {m.get("slug") for m in builtin}
    extras = []
    seen = set(builtin_slugs)
    for path, label in ((CC_CATALOG_PATH, "CC-Switch catalog"),
                        (CATALOG_PATH, "已有修复版 catalog")):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                mods = json.load(f).get("models", [])
        except Exception:                                 # noqa: BLE001
            continue
        for m in mods:
            slug = m.get("slug") if isinstance(m, dict) else None
            if slug and slug not in seen:
                seen.add(slug)
                extras.append(m)
                say("INFO", "合并自定义模型 %s（来自 %s）" % (slug, label))

    merged = list(builtin) + extras
    models, stats = transform_models(merged, "merged")

    say("INFO", "共 %d 条模型：%d 条原为 lite，%d 条带 tool_mode"
        % (stats["total"], stats["lite_on"], stats["tool_mode"]))
    if stats["missing_field"]:
        say("WARN", "以下模型缺必需字段，Codex 可能报错：%s"
            % ", ".join(stats["missing_field"][:5]))

    # 清理临时 home
    shutil.rmtree(tmp_home, ignore_errors=True)
    return models


def write_catalogs(models):
    payload = json.dumps({"models": models}, ensure_ascii=False, indent=2) + "\n"
    ok = False

    if write_text(CATALOG_PATH, payload, "（%d 条模型，全部非 lite）" % len(models)):
        ok = True
    else:
        say("OK", "codex-catalog-classic.json 已是最新，无需修改")

    # 同步一份给 CC-Switch，防止它重新投放时把修复顶掉
    if os.path.isdir(CC_SWITCH_DIR) or os.path.exists(CC_CATALOG_PATH):
        if write_text(CC_CATALOG_PATH, payload, "（与修复版同步）"):
            ok = True
        else:
            say("OK", "cc-switch-model-catalog.json 已是修复版，无需修改")
    return ok


# ------------------------------------------------------------------ 3. 修 config.toml

PIN_LINE = "model_catalog_json = '%s'" % CATALOG_PATH


def first_table_index(lines):
    """返回第一个 [table] 行的下标；没有则返回 len(lines)。"""
    for i, ln in enumerate(lines):
        if re.match(r"^\s*\[", ln):
            return i
    return len(lines)


def patch_config_toml():
    if not os.path.isfile(CONFIG_PATH):
        say("ERR", "找不到 %s" % CONFIG_PATH)
        return False

    with open(CONFIG_PATH, "r", encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    lines = text.splitlines(keepends=True)
    boundary = first_table_index(lines)
    changed = False

    # ---- 3.1 顶层键 model_catalog_json：正确就原样保留，否则归一化到文件最前面
    boundary = first_table_index(lines)
    found_old = [ln.strip() for ln in lines[:boundary]
                 if re.match(r"^\s*model_catalog_json\s*=", ln)]

    already_ok = (len(found_old) == 1 and found_old[0] == PIN_LINE)
    if already_ok:
        say("OK", "config.toml 已 pin 到修复版 catalog")
    else:
        before, rest = lines[:boundary], lines[boundary:]
        before = [ln for ln in before
                  if not re.match(r"^\s*model_catalog_json\s*=", ln)]
        while before and before[0].strip() == "":
            before.pop(0)
        lines = [PIN_LINE + "\n"] + before + rest
        changed = True
        say("FIX", "config.toml 写入 model_catalog_json pin")
        if found_old:
            say("INFO", "  替换掉的旧值：%s" % found_old[0][:100])

    # ---- 3.2 删掉无效的 network_access（Codex 会打印忽略警告）
    boundary = first_table_index(lines)
    new_lines, removed_na = [], 0
    for i, ln in enumerate(lines):
        if i < boundary and re.match(r"^\s*network_access\s*=", ln):
            removed_na += 1
            continue
        new_lines.append(ln)
    if removed_na:
        lines = new_lines
        changed = True
        say("FIX", "config.toml 删除无效键 network_access（消除启动告警）")

    if changed:
        new_text = "".join(lines)
        write_text(CONFIG_PATH, new_text, "（已写入 catalog pin）")
    else:
        say("OK", "config.toml 无需修改")

    # ---- 3.3 校验 TOML 合法性
    with open(CONFIG_PATH, "rb") as f:
        raw = f.read()
    if tomllib is not None:
        try:
            d = tomllib.loads(raw.decode("utf-8", "surrogateescape"))
        except Exception as e:                            # noqa: BLE001
            say("ERR", "config.toml 语法校验失败：%s" % e)
            return False
        pin = d.get("model_catalog_json", "")
        say("OK", "config.toml 语法正确，pin = %s" % pin)
        if os.path.normcase(pin) != os.path.normcase(CATALOG_PATH):
            say("WARN", "pin 指向的不是预期路径，可能被其他工具改写")
    else:
        say("WARN", "Python 无 tomllib（需 3.11+），跳过语法校验")
    return True


# ------------------------------------------------------------------ 4. 修失效版本目录

HASH = r"[0-9a-f]{12,40}"
RE_CUA = re.compile(r"runtimes[\\/]+cua_node[\\/]+(" + HASH + r")")
RE_BIN = re.compile(r"[\\/]+bin[\\/]+(" + HASH + r")")
STOP = set('"\' \t\r\n,]')


def existing_hash_dirs(root):
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        if re.fullmatch(HASH, name) and os.path.isdir(os.path.join(root, name)):
            out.append(os.path.join(root, name))
    return out


def resolve_dead_ref(text, start, end, target_root):
    """
    start/end 是 hash 在 text 里的区间。
    从 hash 后面取出剩余相对路径，逐个候选目录试探，返回能真实存在的那个目录名。
    """
    # 截取 hash 之后的路径片段
    j = end
    while j < len(text) and text[j] not in STOP:
        j += 1
    rest = text[end:j]
    rest = rest.replace("\\\\", "\\").replace("/", "\\").strip(" \\")
    if rest and not rest.startswith("\\"):
        rest = "\\" + rest

    cands = existing_hash_dirs(target_root)
    if not cands:
        return None

    # 1) 完整剩余路径能对上
    if rest:
        for d in cands:
            if os.path.exists(d + rest):
                return os.path.basename(d)
        # 2) 只用第一段（比如 node.exe / codex.exe）
        first = rest.lstrip("\\").split("\\")[0]
        for d in cands:
            if os.path.exists(os.path.join(d, first)):
                return os.path.basename(d)
    return None


def repair_dead_paths_in_text(text, label):
    """
    返回 (新文本, 修改处数, 未解决列表)
    """
    fixes, unresolved = 0, []
    for regex, root, kind in ((RE_CUA, CUA_NODE_ROOT, "cua_node"),
                              (RE_BIN, BIN_ROOT, "bin")):
        pos = 0
        while True:
            m = regex.search(text, pos)
            if not m:
                break
            h = m.group(1)
            cur_dir = os.path.join(root, h)
            # 当前 hash 目录存在就算正常（不深入判断文件，避免误改）
            if os.path.isdir(cur_dir):
                pos = m.end()
                continue
            new_hash = resolve_dead_ref(text, m.start(1), m.end(1), root)
            if new_hash:
                text = text[:m.start(1)] + new_hash + text[m.end(1):]
                fixes += 1
                say("FIX", "%s: %s\\%s… → %s…" % (label, kind, h[:12], new_hash[:12]))
                pos = m.start(1) + len(new_hash)
            else:
                unresolved.append("%s (%s\\%s)" % (label, kind, h))
                pos = m.end()
    return text, fixes, unresolved


def repair_dead_paths():
    targets = list(DEAD_PATH_TARGETS)
    # 插件缓存里的 .mcp.json
    targets += glob.glob(os.path.join(
        CODEX_HOME, "plugins", "cache", "**", ".mcp.json"), recursive=True)

    total, all_unresolved = 0, []
    for path in targets:
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
            text = f.read()
        label = os.path.relpath(path, CODEX_HOME) if path.startswith(CODEX_HOME) \
            else os.path.basename(path)
        new_text, n, unres = repair_dead_paths_in_text(text, label)
        all_unresolved += unres
        if n:
            write_text(path, new_text, "（修复 %d 处失效版本目录）" % n)
            total += n
        # JSON 校验
        if path.endswith(".json") and n:
            try:
                json.loads(new_text)
                say("OK", "  %s JSON 仍然合法" % label)
            except Exception as e:                        # noqa: BLE001
                say("ERR", "  %s JSON 损坏！请用备份还原：%s" % (label, e))

    if total == 0:
        say("OK", "没有失效的版本目录引用")
    for u in all_unresolved[:5]:
        say("WARN", "无法自动修复（找不到候选目录）：%s" % u)
    return total


# ------------------------------------------------------------------ 5. 同步 CC-Switch 数据库

def patch_cc_switch_db():
    """
    CC-Switch 每次同步会把 settings.common_config_codex 这段 TOML 拼进 config.toml。
    在片段开头注入 catalog pin，保证它同步回去时也带着正确指向。
    """
    if not os.path.isfile(CC_SWITCH_DB):
        say("INFO", "未安装 CC-Switch，跳过")
        return

    # 安全阀：只有修改“真实” CODEX_HOME 时才动 CC-Switch 的数据库。
    # 否则用 CODEX_HOME=临时目录做测试时，会把临时路径写进真实配置，
    # 导致下次 CC-Switch 同步时把 config.toml 的 pin 指到不存在的地方。
    if os.path.normcase(CODEX_HOME) != os.path.normcase(REAL_CODEX_HOME):
        say("INFO", "当前 CODEX_HOME 不是默认目录，跳过 CC-Switch 数据库（避免写入临时路径）")
        return

    try:
        con = sqlite3.connect(CC_SWITCH_DB, timeout=6)
        con.execute("PRAGMA busy_timeout=6000")
        row = con.execute(
            "SELECT value FROM settings WHERE key='common_config_codex'").fetchone()
    except sqlite3.OperationalError as e:
        say("WARN", "CC-Switch 数据库被占用，跳过（关掉 CC-Switch 后重跑本脚本即可）：%s" % e)
        return
    except Exception as e:                                # noqa: BLE001
        say("WARN", "读取 CC-Switch 数据库失败，跳过：%s" % e)
        return

    if not row:
        say("INFO", "CC-Switch 里没有 codex 公共配置片段，跳过")
        con.close()
        return

    value = row[0] or ""
    lines = value.splitlines(keepends=True)
    boundary = first_table_index(lines)
    before = [ln for ln in lines[:boundary]
              if not re.match(r"^\s*model_catalog_json\s*=", ln)]
    rest = lines[boundary:]
    before = [PIN_LINE + "\n"] + before
    new_value = "".join(before + rest)

    # 顺手修片段里的失效版本号
    new_value, n, _ = repair_dead_paths_in_text(new_value, "cc-switch 片段")

    if new_value == value:
        say("OK", "CC-Switch 配置片段已含 catalog pin，无需修改")
        con.close()
        return

    if backup(CC_SWITCH_DB):
        say("INFO", "  cc-switch.db 已备份")
    try:
        con.execute("UPDATE settings SET value=? WHERE key='common_config_codex'", (new_value,))
        con.commit()
        say("FIX", "CC-Switch 配置片段已注入 catalog pin%s"
            % ("（并修复 %d 处失效路径）" % n if n else ""))
        CHANGED.append(CC_SWITCH_DB)
    except Exception as e:                                # noqa: BLE001
        say("WARN", "写入 CC-Switch 数据库失败（可能被占用）：%s" % e)
    finally:
        con.close()


# ------------------------------------------------------------------ 6. 验证

def verify(codex_exe, deep=False):
    ok = True
    say("INFO", "验证 1/3：用真实 CODEX_HOME 加载模型目录 ...")
    rc, out, err = run([codex_exe, "debug", "models"], timeout=120)
    if rc != 0:
        say("ERR", "  codex debug models 失败 (exit=%s)" % rc)
        tail = (err.strip().splitlines() or [""])[-1]
        if tail:
            say("ERR", "  " + tail[:300])
        return False

    try:
        models = json.loads(out)["models"]
    except Exception:                                     # noqa: BLE001
        say("ERR", "  无法解析 debug models 输出")
        return False

    bad = [m.get("slug") for m in models
           if m.get("use_responses_lite") is not False or "tool_mode" in m]
    if bad:
        say("ERR", "  仍有 %d 条模型是 lite/tool_mode：%s" % (len(bad), ", ".join(bad)))
        ok = False
    else:
        say("OK", "  加载了 %d 条模型，全部 use_responses_lite=false 且无 tool_mode" % len(models))
        say("INFO", "  模型：%s" % ", ".join(m.get("slug", "?") for m in models))

    say("INFO", "验证 2/3：检查启动告警 ...")
    rc2, out2, err2 = run([codex_exe, "features", "list"], timeout=120)
    warn = [ln for ln in err2.splitlines() if "ignoring" in ln or "unrecognized" in ln]
    if warn:
        say("WARN", "  仍有配置告警：%s" % warn[0][:200])
    else:
        say("OK", "  无配置告警")

    say("INFO", "验证 3/3：检查 MCP 服务器能否启动 ...")
    rc3, out3, err3 = run([codex_exe, "mcp", "list"], timeout=120)
    dead = [ln for ln in (out3 + err3).splitlines()
            if "a5a02988" in ln or "e78bad98" in ln or "03b1cdac" in ln]
    if dead:
        say("WARN", "  仍有失效版本号：%s" % dead[0][:200])
    else:
        say("OK", "  MCP 配置里无失效版本号")

    if deep:
        ok = _deep_verify(codex_exe) and ok
    return ok


def _deep_verify(codex_exe):
    """
    真验证：证明模型"确实有终端工具并且用上了"。

    为什么不能只看输出里有没有某个固定字符串：
      提示词里写了 echo FIX_VERIFY_OK，模型即使没有工具也能把这串字复述出来，
      这会产生假阳性。所以这里改成：
        1) 生成一个随机 nonce，只写进一个临时文件（不写进提示词）；
        2) 让模型去读这个文件；
        3) 只有 nonce 出现在回复里 —— 才说明它真的执行了命令读到文件。
      再加一层：解析 --json 事件流，确认真的出现过 command_execution 条目。
    """
    say("INFO", "深度验证：真实调用一次模型，确认它能执行命令（会消耗少量额度）...")

    nonce = "FIXOK" + uuid.uuid4().hex[:16].upper()
    tmpdir = tempfile.mkdtemp(prefix="codexfix-")
    probe = os.path.join(tmpdir, "probe.txt")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write(nonce + "\n")

        prompt = ("读取当前目录下的文件 probe.txt，把它里面的内容原样回复给我。"
                  "只回复文件内容，不要加任何解释。")
        rc, out, err = run(
            [codex_exe, "exec", "--skip-git-repo-check", "--json",
             "-c", "features.code_mode_host=true", prompt],
            timeout=300, cwd=tmpdir)

        # 1) 事件流里必须出现过真实的命令执行
        saw_cmd = False
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except Exception:                             # noqa: BLE001
                continue
            item = ev.get("item") or {}
            if ev.get("type") == "item.completed" and item.get("type") == "command_execution":
                saw_cmd = True
                break

        # 2) 回复里必须出现 nonce（它只存在于文件里，靠猜不可能命中）
        if nonce in out:
            say("OK", "  模型成功执行了命令并读到了文件内容，修复确认生效")
            if not saw_cmd:
                say("INFO", "  （事件流里没解析到 command_execution，但 nonce 命中已足以证明）")
            return True

        say("ERR", "  模型没能读到临时文件 —— 说明它仍然没有可用的终端/文件工具")
        if not saw_cmd:
            say("ERR", "  事件流里也没有任何 command_execution（确实没执行命令）")
        tail = (out or err).strip().splitlines()[-3:]
        for t in tail:
            say("ERR", "  " + t[:300])
        return False
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:                                 # noqa: BLE001
            pass


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="修复 Codex Desktop 模型无工具 BUG")
    ap.add_argument("--deep", action="store_true", help="额外做一次真实模型调用验证（消耗少量额度）")
    ap.add_argument("--dry-run", action="store_true", help="只检查不修改")
    args = ap.parse_args()

    head("Codex Desktop 一键修复工具")
    say("INFO", "CODEX_HOME = %s" % CODEX_HOME)

    codex_exe, src = find_codex_exe()
    if not codex_exe:
        say("ERR", "找不到 codex.exe。请确认 Codex Desktop 已安装，或设置 CODEX_EXE 环境变量。")
        return 2
    say("OK", "codex.exe = %s（来自 %s）" % (codex_exe, src))

    rc, out, _ = run([codex_exe, "--version"], timeout=60)
    if rc == 0:
        say("OK", "版本：%s" % out.strip())

    if not os.path.isdir(CODEX_HOME):
        say("ERR", "CODEX_HOME 不存在：%s" % CODEX_HOME)
        return 2

    if args.dry_run:
        say("WARN", "dry-run 模式：只检查，不写入任何文件")

    # ---- 步骤 1：生成并写入模型目录
    head("步骤 1/4  重建模型目录（把工具放回顶层 tools 数组）")
    models = build_catalog(codex_exe)
    if models is None:
        return 1
    if not args.dry_run:
        write_catalogs(models)
    else:
        say("INFO", "dry-run：跳过写入 catalog")

    # ---- 步骤 2：config.toml
    head("步骤 2/4  在 config.toml 中固定模型目录")
    if not args.dry_run:
        if not patch_config_toml():
            return 1
    else:
        with open(CONFIG_PATH, "rb") as f:
            raw = f.read()
        if tomllib:
            try:
                d = tomllib.loads(raw.decode("utf-8", "surrogateescape"))
                say("INFO", "当前 pin = %s" % d.get("model_catalog_json"))
            except Exception as e:                        # noqa: BLE001
                say("ERR", "config.toml 当前就无法解析：%s" % e)

    # ---- 步骤 3：失效版本目录
    head("步骤 3/4  修复失效的版本目录引用")
    if not args.dry_run:
        repair_dead_paths()
        patch_cc_switch_db()
    else:
        say("INFO", "dry-run：跳过")

    # ---- 步骤 4：验证
    head("步骤 4/4  验证")
    ok = verify(codex_exe, deep=args.deep and not args.dry_run)

    # ---- 汇总
    head("结果")
    if CHANGED:
        say("FIX", "共修改 %d 个文件：" % len(CHANGED))
        for p in CHANGED:
            say("INFO", "  " + p)
    else:
        say("OK", "没有任何文件需要修改 —— 当前已经是修复状态")

    if ok:
        say("OK", "全部检查通过")
    else:
        say("WARN", "有检查项未通过，请看上面的 [ERR] 行")

    print("", flush=True)
    say("INFO", "重要：Codex Desktop 在启动时缓存模型目录，需要【完全退出并重新打开 Codex Desktop】才生效。")
    say("INFO", "所有被修改的文件都已备份为 *.bak-%s，需要回退时改回原名即可。" % STAMP)

    # 写报告
    try:
        rpt = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "last-fix-report-%s.txt" % STAMP)
        with open(rpt, "w", encoding="utf-8") as f:
            f.write("\n".join(REPORT) + "\n")
        say("INFO", "报告已保存：%s" % rpt)
    except Exception:                                     # noqa: BLE001
        pass

    return 0 if ok else 1


def _ensure_utf8_console():
    """
    把控制台输出代码页切到 UTF-8，并让 Python 用 UTF-8 输出。

    中文 Windows 的控制台默认是 936(GBK)，而脚本里的提示语是中文。
    只 reconfigure 流不够——控制台的“显示代码页”还是 936，会显示成乱码。
    所以这里同时改内核代码页。全部包在 try 里：重定向到文件/管道时没有控制台，
    调用会失败，这是正常的，忽略即可。
    """
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:                                 # noqa: BLE001
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                 # noqa: BLE001
            pass


if __name__ == "__main__":
    _ensure_utf8_console()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
    except Exception:                                     # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(1)
