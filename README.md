# Codex Desktop 一键修复工具

修复 Codex Desktop「模型说自己没有文件/终端工具」的问题。

## 怎么用

双击 **`双击运行-修复Codex工具.bat`**，看完输出后按任意键关闭。

然后**完全退出 Codex Desktop 再重新打开**（右下角托盘图标也要右键退出），开个新会话。

如果想连"模型真的能执行命令"都验证一遍（会消耗一点点额度）：

```
双击运行-修复Codex工具.bat --deep
```

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
| `_make_bat.py` | 重新生成启动器（改了中文提示后跑一下） |
| `_make_broken_fixture.py` | 造一个"坏掉"的测试环境，用于回归测试 |
| `_test_deep_negative.py` | 负向测试：确认坏环境下 `--deep` 会判失败而不是假阳性 |

每次运行会在同目录生成 `last-fix-report-<时间戳>.txt`，记录了所有操作，可以删。

## 什么情况下需要重跑

- Codex Desktop 升级之后（新版本可能带来新的模型目录）
- 在 CC-Switch 里切换了 provider（理论上已经防住了：修好的 catalog 同时写到了
  `codex-catalog-classic.json` 和 CC-Switch 会投放的 `cc-switch-model-catalog.json`，
  两份内容完全相同）
- 又出现"模型说没有工具"的症状

## 已知无关告警

跑起来可能会看到这些，它们跟工具问题无关，可以忽略：

- `AuthRequired ... mcp.canva.com ...` / figma OAuth 失败 —— 远端 MCP 需要登录
- `app_server_connection.reconnect_failed "Sign in to ChatGPT to start a durable thread."`
  —— 桌面端连云端的 durable host 需要登录 ChatGPT 账号，本地 app-server 不受影响
