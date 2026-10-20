# Codex Desktop 一键修复工具

修复 Codex Desktop「模型说自己没有文件/终端工具」的问题。

> 症状：模型能看到项目、权限也是 `Full access`，但它回答
> "当前会话没有可用的文件读取工具 / 没有终端入口"，一个工具都调不动。

## 怎么用

双击 **`双击运行-修复Codex工具.bat`**，看完输出后按任意键关闭。

然后**完全退出 Codex Desktop 再重新打开**（右下角托盘图标也要右键退出），开个新会话。

如果想连"模型真的能执行命令"都验证一遍（会消耗一点点额度）：

```
双击运行-修复Codex工具.bat --deep
```

只看不改、先确认它会动哪些文件：

```
双击运行-修复Codex工具.bat --dry-run
```

### 前置要求

- Windows 10 / 11
- Python 3.11 或更高（安装时记得勾选 "Add Python to PATH"）
  - 启动器会依次尝试 `py -3.11`、`python`、常见安装路径，找不到会给出提示

## 它修了什么

Codex 0.163 给新一代模型（gpt-6-sol / gpt-6.1-sol / gpt-5.6 系列等）配了一份"模型目录"，
里面有两个字段会改变工具的下发方式：

| 字段 | 作用 |
| --- | --- |
| `use_responses_lite = true` | 把工具塞进 input 里的一条 `additional_tools`，而不是顶层的 `tools` 数组 |
| `tool_mode = "code_mode_only"` | 只暴露一个 JS 代码模式的 `exec`，而不是完整的 `exec_command` 等工具集 |

这两者叠加后，请求长得像"新形态"，而很多第三方 API 中转不认识这种形态，
会**静默丢弃**整块工具定义——模型本身没变、权限也全开，只是它真的收不到任何工具。
从日志上看就是工具调用数 0、模型回答"当前会话没有可读取文件的工具"。

脚本把每条模型的 `use_responses_lite` 改成 `false` 并删掉 `tool_mode`，
让工具回到顶层的标准 `tools` 数组（中转都认这种老形态），并在 `config.toml` 里用
`model_catalog_json` 把这个修好的目录固定下来。

顺带修掉两类"版本目录 hash 失效"的残留引用（Codex 升级后旧目录被删）：
`notify`、`[mcp_servers.node_repl]`、插件缓存里的 `.mcp.json`、`chrome-native-hosts-v2.json`，
以及 CC-Switch 数据库里那段会被拼回 `config.toml` 的公共配置片段。
还会删掉新版 Codex 已不认识的 `network_access` 键（它会刷启动告警）。

> 注意：模型目录是 **app-server 启动时**装配并缓存的，改完必须重启 Codex Desktop。
> 只重开窗口不够，要整个退出（含托盘图标）。

## 手动修复（不想跑脚本）

1. 用空目录作为 `CODEX_HOME` 导出内置模型目录：

   ```bat
   set CODEX_HOME=%TEMP%\codex-empty
   codex debug models > %USERPROFILE%\.codex\catalog-raw.json
   ```

2. 编辑 `catalog-raw.json` 里 `models` 的每一条：把 `use_responses_lite` 改成 `false`，
   并删除 `tool_mode` 字段，另存为 `%USERPROFILE%\.codex\codex-catalog-classic.json`。

3. 在 `%USERPROFILE%\.codex\config.toml` 的第一张 `[table]` **之前**加一行
   （单引号、正斜杠，写反斜杠会被当转义）：

   ```toml
   model_catalog_json = 'C:/Users/<你的用户名>/.codex/codex-catalog-classic.json'
   ```

4. 完全退出并重新打开 Codex Desktop。

## 安全性

- 每个被改的文件都先备份成 `*.bak-YYYYMMDD-HHMMSS`，改回去就能回退。
- 幂等：已经是修复状态时报告"没有任何文件需要修改"，不写任何东西。
- 不联网、不重启任何程序、不弹窗口。
- 只改 `%USERPROFILE%\.codex` 和 `%USERPROFILE%\.cc-switch` 下的配置。
- CC-Switch 数据库只有在 `CODEX_HOME` 是默认位置时才动，避免测试时把临时路径写进真实配置。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `双击运行-修复Codex工具.bat` | 启动器（纯 ASCII，避免 cmd 编码问题） |
| `fix_codex_tools.py` | 主逻辑：四步修复 + 验证 |
| `assets/codex-tool-fix.ico` | 多尺寸图标（16/20/24/32/40/48/64/96/128/256） |
| `assets/codex-tool-fix.png` | 1024px 预览图 |
| `_make_icon.py` | 重新生成图标 |
| `_check_icon.py` | 图标量化验收（居中 / 体量一致 / 对比度 / 小尺寸不糊） |
| `_make_shortcut.py` | 安装图标到 `%LOCALAPPDATA%`、创建桌面快捷方式并做像素级校验 |
| `_verify_shortcut_e2e.py` | 端到端验证：快捷方式 → bat → 修复脚本 |
| `_make_bat.py` | 重新生成启动器（改了中文提示后跑一下） |
| `_make_broken_fixture.py` | 造一个"坏掉"的测试环境，用于回归测试 |
| `_test_deep_negative.py` | 负向测试：确认坏环境下 `--deep` 会判失败而不是假阳性 |

每次运行会在同目录生成 `last-fix-report-<时间戳>.txt`，记录了所有操作，可以删。

## 桌面快捷方式与图标

`.bat` 文件在资源管理器里只能显示系统默认图标，这是 Windows 的固定行为，改不了。
想让它在桌面上是自定义图标，只能建一个 `.lnk` 快捷方式、把 `IconLocation`
指向一个 `.ico`。

```
python _make_shortcut.py
```

它会把图标**安装到 `%LOCALAPPDATA%\CodexToolFix\`**，再在**真实桌面**（自动识别被
重定向的桌面路径，例如 `D:\桌面文件`）创建 `Codex 工具修复.lnk`，然后做五步验收：

1. 安装图标，并读取安装后文件的**完整性标签**
2. 回读 `.lnk` 的目标 / 起始位置 / 图标路径
3. `SHGetFileInfoW` 取 Windows **实际分配**给该快捷方式的 `HICON`
4. `GetIconInfo` + `GetDIBits` 取图标像素，与 `.ico` 里同尺寸那一帧逐像素比对
   —— 只有这一步能证明系统真的用了我们的图标，而不是静默回退到默认图标
5. 断链检查

### 为什么图标不能留在脚本目录里

这是踩过的坑，值得写下来：如果**承载图标的目录**带低完整性标签（Low Mandatory
Level），Windows shell 的图标提取管道会拒绝该文件，快捷方式就退化成一张**白纸**
（通用文档图标），而 `.ico` 文件本身用任何常规手段检查都完全正常。

判断方法：`icacls <目录>` 看是否带 `(OI)(CI)(IO)(NW)(RX)` 之类的低权标签，或者
直接比较两个目录——把同一个 `.ico` 复制到 `%LOCALAPPDATA%` 能正常显示、留在脚本
目录就变白纸，那就是它。`/setintegritylevel` 通常改不动（rc=5 拒绝访问），所以
正确做法不是去改标签，而是**把图标装到用户目录**，即上面第 1 步。

图标设计见 `_make_icon.py` 顶部注释：一枚 squircle 底片 + 加粗的命令行提示符 `❯`
+ 渐变光标块，避开被用滥的齿轮/扳手套话图形。16–32px 用一套单独加粗的参数重绘
（不是把 1024 直接缩小），并由 `_check_icon.py` 量化验收：光学质心居中、
各尺寸实心面积极差 ≤ 8 个百分点、字形/底对比度 ≥ 4.5:1、小尺寸下 V 形与光标块
之间仍留有背景间隙。

## 什么情况下需要重跑

- Codex Desktop 升级之后（新版本可能带来新的模型目录）
- 在 CC-Switch 里切换了 provider（理论上已经防住了：修好的 catalog 同时写到了
  `codex-catalog-classic.json` 和 CC-Switch 会投放的 `cc-switch-model-catalog.json`，
  两份内容完全相同）
- 又出现"模型说没有工具"的症状
- **移动了仓库目录**（图标装在 `%LOCALAPPDATA%` 是自包含的，不受影响；但如果之前
  用的是老版本脚本、图标还指向仓库里，就需要重跑一次 `_make_shortcut.py`）

## 已知无关告警

跑起来可能会看到这些，它们跟工具问题无关，可以忽略：

- `AuthRequired ... mcp.canva.com ...` / figma OAuth 失败 —— 远端 MCP 需要登录
- `app_server_connection.reconnect_failed "Sign in to ChatGPT to start a durable thread."`
  —— 桌面端连云端的 durable host 需要登录 ChatGPT 账号，本地 app-server 不受影响

## 验证环境

- Windows 11 + Codex Desktop 0.163.0-alpha.10.2（codex-cli）
- 第三方 OpenAI 兼容中转（CC-Switch 转发）
- 已验证：修复后模型能真实调用 `exec_command` 执行命令并回读文件内容
