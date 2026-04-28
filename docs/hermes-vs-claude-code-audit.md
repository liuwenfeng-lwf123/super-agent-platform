# 对照 Hermes Agent & Claude Code 的进度审计

**参考源**:
- Hermes Agent (Nous Research): https://hermes-agent.nousresearch.com/docs/
- Claude Code 架构分析: https://www.penligent.ai/hackinglabs/inside-claude-code-the-architecture-behind-tools-memory-hooks-and-mcp/
- Anthropic 官方: https://docs.anthropic.com/en/docs/claude-code/

**审计时间**: 2026-04-23

---

## 0、R5 代码核验结论（覆盖下文旧估算）

- **Claude Code 核心对齐**: `17.5 / 18 = 97.2%`
- **Hermes 开发者面**: `12.5 / 16 = 78.1%`
- **本轮已关闭的旧 0.5 项**: Bash 默认 env 语义、`/statusline`、`/mcp__<server>__<prompt>`、auto mode 宽规则降权、MCP `prompts/resources`、`execute_code` tool、cron 自然语法与投递、`agentskills`/`.claude/skills` 兼容、plugin bundle 自动挂载 `agents/hooks/MCP`、JS/TS/JSX/TSX 语义级 code-intel、OAuth 授权回调闭环与 provider 运行时解析
- **本轮新增的部分对齐**: macOS `sandbox-exec` 写隔离、线程级 `HOME/TMP/cache` 隔离、workspace / outputs symlink & traversal escape 阻断
- **本轮新增的开发入口**: headless CLI prompt runner（`python -m app.headless_cli`）+ `allowedTools/disallowedTools` 风格工具白名单/黑名单过滤
- **本轮新增的协议入口**: ACP-lite stdio JSON-RPC adapter（`python -m app.acp_lite`），支持 thread/workspace context、read/list/applyEdit、file history、thread-scoped editor state、基础 diagnostics、local client bind/system-info
- **本轮新增的 agent 入口**: `get_editor_state` / `get_editor_diagnostics` 工具，可让 agent 直接读取当前 thread 的活动文件、光标/选区与 editor diagnostics
- **本轮新增的自动上下文注入**: thread-scoped editor state 会自动进入 `super_agent` / `local_agent` system prompt，降低“修当前文件 / 看当前报错”类任务的显式工具调用成本
- **本轮新增的通知能力**: ACP-lite 已支持 `editor/subscribe` / `editor/unsubscribe` 与 `notifications/editorStateChanged` / `notifications/editorDiagnosticsChanged`，开始具备 push-style IDE 集成雏形
- **本轮新增的编辑动作**: ACP-lite 已支持 `editor/renameSymbol`，可基于 Python/Jedi 与 JS/TS 静态引用分析做 best-effort workspace rename-lite
- **本轮新增的交互闭环**: ACP-lite 已支持 `editor/findReferences`、`editor/codeActions`、`editor/applyCodeAction`，开始具备“发现影响面 → 给出重构动作 → 执行动作”的 code-action-lite 流程
- **本轮新增的预览握手**: ACP-lite 已支持 `editor/prepareRename` 与 `editor/resolveCodeAction`，可先判断 rename 可行性并返回 preview edits，再决定是否真正落盘
- **本轮新增的第二类动作**: ACP-lite 已支持 `editor/formatDocument`（JSON）以及对应的 `source.formatDocument` code action，开始具备非 rename 的多动作能力
- **本轮新增的结构化 edits**: rename / format 的 resolve 与 apply 结果现在会附带 `workspace_edit` payload，便于 IDE 客户端自行预览或应用 edits
- **本轮新增的 structured edit round-trip**: `workspace/applyEdit` 已可直接消费 `workspace_edit`，`editor/applyCodeAction` 也支持 `apply_mode=workspace_edit`，可先取结构化 edits 再由 IDE 侧决定何时应用
- **本轮新增的 diagnostics quick-fix**: ACP-lite 已支持面向 JSON trailing comma 诊断的 `quickfix.json.removeTrailingCommas`，贯通 codeActions / resolve / deferred apply / direct RPC
- **本轮扩展的 diagnostics quick-fix 覆盖面**: ACP-lite 进一步支持 `quickfix.json.removeComments`，现在已能处理 JSON comments 与 trailing commas 两类常见语法问题
- **本轮新增的跨语言 quick-fix**: ACP-lite 进一步支持 `quickfix.python.insertMissingColon`，开始具备 Python syntax diagnostics 的最小修复能力
- **本轮新增的 editor-diagnostics quick-fix**: ACP-lite 进一步支持 `quickfix.script.insertMissingSemicolons`，可消费编辑器侧 JS/TS missing semicolon diagnostics 并返回结构化 edits
- **本轮新增的 Python lint quick-fix**: ACP-lite 进一步支持 `quickfix.python.removeUnusedImports`，可消费编辑器侧 `F401` / unused import diagnostics 并返回结构化 edits

### Claude Code 对照（R5）

| 官方机制 | 你项目对应文件 | 完成度(1/0.5/0) | 缺口 |
|---|---|---:|---|
| 工具执行循环 + file/search/exec/web/code-intel 五类工具 | `backend/app/agents/tools.py`<br>`backend/app/agents/tool_runtime.py`<br>`backend/app/agents/super_agent.py` | 1 | — |
| 工具名同时是治理词汇（权限 / hooks / subagent 共用） | `backend/app/agents/tool_runtime.py`<br>`backend/app/agents/hooks.py`<br>`backend/app/agents/subagents.py` | 1 | — |
| Bash 语义（cwd 持久；env 默认不持久；`CLAUDE_ENV_FILE` 可桥接） | `backend/app/sandbox/manager.py`<br>`backend/tests/test_runtime_e2e.py` | 1 | — |
| Built-in commands（`/compact` `/tasks` `/statusline` `/usage` `/mcp__...`） | `frontend/src/lib/slash.ts`<br>`frontend/src/lib/api.ts`<br>`backend/app/api/chat.py` | 1 | — |
| LSP / code intelligence | `backend/app/agents/lsp_tools.py`<br>`backend/app/agents/static_code_intel.py`<br>`backend/app/agents/ts_language_service.py`<br>`backend/tests/test_runtime_e2e.py` | 1 | Python + JS/TS/JSX/TSX 已具备 definition/references/symbols/call-hierarchy；JS/TS 优先走真实 TypeScript 语义服务，失败时回退静态索引 |
| Subagents：独立上下文、只回摘要 | `backend/app/agents/subagents.py` | 1 | — |
| Subagent 配置面（prompt / tools / model / permission / memory / MCP / isolation） | `backend/app/agents/subagents.py` | 1 | — |
| Worktree isolation | `backend/app/agents/subagents.py` | 1 | — |
| Permission modes | `backend/app/agents/subagents.py`<br>`backend/app/agents/tool_runtime.py`<br>`backend/app/agents/safety_classifier.py` | 1 | — |
| Auto mode classifier hardening（宽 allow 规则自动降权） | `backend/app/agents/tool_runtime.py`<br>`backend/tests/test_runtime_e2e.py` | 1 | — |
| 4 层配置作用域 + `Tool(specifier)` 规则语法 | `backend/app/agents/permission_scopes.py`<br>`backend/app/agents/tool_runtime.py` | 1 | — |
| Hooks 生命周期 + PreToolUse 改参数 | `backend/app/agents/hooks.py`<br>`backend/app/agents/tool_runtime.py` | 1 | — |
| Hook handler types（command / script / http / prompt / agent） | `backend/app/agents/hooks.py` | 1 | — |
| MCP（stdio / http / prompts / resources） | `backend/app/skills/mcp.py`<br>`backend/app/api/chat.py` | 1 | — |
| Skills via `SKILL.md` | `backend/app/skills/agentskills_compat.py`<br>`backend/app/agents/super_agent.py`<br>`backend/app/api/chat.py` | 1 | — |
| Plugins 作为 bundle 层（skills + agents + hooks + MCP 打包发现/加载） | `backend/app/agents/self_evolution.py`<br>`backend/app/agents/hooks.py`<br>`backend/app/agents/subagents.py`<br>`backend/app/skills/mcp.py` | 1 | — |
| Prompt caching | `backend/app/agents/self_evolution.py`<br>`backend/app/agents/super_agent.py`<br>`backend/app/api/chat.py` | 1 | — |
| Sandboxing / ACP / headless CLI automation | `backend/app/sandbox/manager.py`<br>`backend/app/api/chat.py`<br>`backend/app/headless_cli.py`<br>`backend/app/acp_lite.py`<br>`backend/app/local/editor_state.py`<br>`backend/app/agents/tools.py`<br>`backend/app/agents/super_agent.py`<br>`backend/app/local/agent.py`<br>`backend/tests/test_runtime_e2e.py`<br>`backend/tests/test_api_integration.py`<br>`backend/tests/test_headless_cli.py`<br>`backend/tests/test_acp_lite.py`<br>`backend/tests/test_editor_state_tools.py`<br>`backend/tests/test_editor_context_injection.py` | 0.5 | 已有 macOS `sandbox-exec` 写隔离、线程级 HOME/TMP/cache 重定向、workspace/output symlink & traversal 阻断、headless CLI prompt runner + `allowedTools/disallowedTools` 工具过滤，以及带 thread-scoped editor state / diagnostics bridge / notification subscribe / rename-lite / code-actions-lite / preview-resolve handshake / JSON format action / structured workspace_edit payload / workspace_edit round-trip apply / diagnostics quick-fix(JSON comments + trailing commas + Python missing colon + Python unused imports + JS/TS missing semicolons) 的 ACP-lite stdio JSON-RPC、agent-facing editor tools 与自动 editor-context prompt 注入；仍未做 Claude Code 级完整 IDE-native ACP |

### Hermes 对照（R5）

| 官方机制 | 你项目对应文件 | 完成度(1/0.5/0) | 缺口 |
|---|---|---:|---|
| 闭环学习：技能创建 + 自改进 + learning loop | `backend/app/agents/self_evolution.py`<br>`backend/app/agents/learning_loop.py`<br>`backend/app/agents/evolution.py` | 1 | — |
| `SOUL.md` 人格文件 | `backend/app/agents/self_evolution.py`<br>`backend/app/api/chat.py` | 1 | — |
| `MEMORY.md / USER.md / AGENTS.md / .hermes.md` 上下文文件 | `backend/app/agents/super_agent.py` | 1 | — |
| FTS5 跨会话 recall + summary | `backend/app/agents/learning_loop.py`<br>`backend/app/api/chat.py` | 1 | — |
| Session lineage + compaction | `backend/app/agents/store.py`<br>`backend/app/agents/super_agent.py`<br>`backend/app/api/chat.py` | 1 | — |
| Isolated subagents / parallel workstreams | `backend/app/agents/subagents.py` | 1 | — |
| Programmatic Tool Calling（Hermes `execute_code`） | `backend/app/agents/tools.py`<br>`backend/app/api/chat.py` | 1 | — |
| Scheduled automations / cron | `backend/app/agents/self_evolution.py`<br>`backend/app/api/chat.py` | 1 | — |
| Open standard skills / `agentskills.io` / Skills Hub | `backend/app/skills/agentskills_compat.py`<br>`backend/app/api/chat.py`<br>`backend/app/agents/evolution.py` | 1 | — |
| Full web control（search / extract / browse / vision / image / TTS） | `backend/app/agents/tools.py` | 0.5 | 已有 search/fetch/基础浏览；vision / image generation / TTS 仍不完整 |
| MCP support | `backend/app/skills/mcp.py`<br>`backend/app/api/chat.py` | 1 | — |
| Provider Runtime Resolver / OAuth / credential pools | `backend/app/models/credentials.py`<br>`backend/app/models/provider.py`<br>`backend/app/api/chat.py` | 0.5 | OAuth + key pool + authorize/callback/exchange 闭环已具备；provider presets / runtime resolver 已覆盖常用 OpenAI-compatible 生态，但距离 Hermes 的 18+ provider breadth 仍有差距 |
| Replaceable memory providers / context engines | `backend/app/agents/provider_plugins.py` | 1 | — |
| Prompt caching | `backend/app/agents/self_evolution.py`<br>`backend/app/agents/super_agent.py` | 1 | — |
| Runs anywhere（6 terminal backends）+ messaging gateway + voice | `backend/app/sandbox/manager.py` | 0 | 当前仍以 local shell + Web UI 为主 |
| Research-ready（trajectory export / Atropos / RL） | `—` | 0 | 未实现 |

### 当前仍然没有被拉满的项

- **Claude 仍为 0.5**: sandboxing / ACP / headless CLI（主要剩 ACP）
- **Hermes 仍为 0.5**: full web control；provider breadth
- **Hermes 仍为 0**: multi-backend runtime / messaging / voice；trajectory export / RL

---

## 一、Claude Code 关键特性 vs 本项目

| 特性 | Claude Code 规范 | 本项目状态 | 缺口说明 |
|---|---|---|---|
| **工具系统** | 47+ 工具分 5 类 (file/search/exec/web/code-intel) | ✅ 49 工具 (含 code-intel) | — |
| **工具命名为治理词汇** | 权限/hook/subagent 都引用工具名 | ✅ 已接入 (subagent `tools`/`disallowed_tools` + hook matcher) | — |
| **Bash 状态语义** | cwd 持久化, env 不持久 | ✅ cwd 跨调用持久化 (`_thread_cwd` + sentinel) + 4 回归测试 | ⚠️ env 不持久 (低优先) |
| **Built-in commands** | `/compact` `/tasks` `/statusline` `/usage` `/mcp__*` | ✅ `slash.ts` 7 命令 + `page.tsx` 拦截派发 + 自动补全下拉 | — |
| **Subagent 隔离上下文** | 独立 context window, 只返回摘要 | ✅ 已实现 | — |
| **Subagent 配置面** | 提示词/工具白黑/模型/permission_mode/isolation/mcp_servers | ✅ **SubagentConfig 完整覆盖** | — |
| **Worktree 隔离** | `isolation: worktree` + `EnterWorktree` SDK | ✅ **已实现** (`_setup_worktree` + `_cleanup_worktree`) + 真测试 PASSED | — |
| **Permission modes** | default/acceptEdits/plan/auto/bypassPermissions/dontAsk | ✅ 6 值 + `safety_classifier.py` 接入 `evaluate_tool_permission` (auto mode) + 3 测试 | — |
| **配置作用域** | Managed/User/Project/Local (`~/.claude/`, `.claude/`) | ✅ `permission_scopes.py` 4 层 + 已接入 `tool_runtime.py` | — |
| **权限规则语法** | `allow/ask/deny` + `Tool(specifier)`, first-match-wins | ✅ `_parse_tool_rule` + `_pattern_matches` + fnmatch 双层匹配 (deny>ask>allow) | — |
| **Sandboxing** | seatbelt/bubblewrap | ⚠️ 已有 macOS `sandbox-exec` 写隔离 + 线程级 HOME/TMP/cache 重定向 + workspace/output path hardening | 仍不是 Claude Code 级完整 cross-platform sandbox |
| **Hooks — 事件** | PreToolUse/PermissionRequest/SessionStart/PreCompact 等 | ✅ **25 事件 enum** 完全对齐 | — |
| **Hooks — 决策动作** | allow/deny/ask/defer, 修改 input, append context | ✅ `HookResult` + executor 支持 | — |
| **Hooks — 处理器类型** | shell command / HTTP / LLM prompt / agent | ✅ 四种 (`command`/`http`/`prompt`/`agent`) + `script` | — |
| **Hooks — 前置拦截修改 tool input** | PreToolUse 可改 params | ✅ `_fire_pre_tool_use_hooks` 应用 modified_input + 缓冲到 hook_events 供 SSE 展示 | — |
| **Skills 系统** | 模型自主调用 | ✅ 已实现 (active_skills + check_env) | — |
| **Plugins — 三来源** | `~/.hermes/` + `.hermes/` + pip entry_points | ✅ **三来源全有** | — |
| **Memory providers (可替换)** | 单选插件, 只能 1 个激活 | ✅ `_SingleSelectRegistry` + 示例 keyword_memory 插件 | — |
| **Context engines (可替换)** | 单选插件 | ✅ `_SingleSelectRegistry` + 示例 lastn_context 插件 + 主循环接入 | — |
| **MCP 集成** | stdio/SSE/HTTP 三协议 + 工具自动注册 | ✅ `MCPStdioClient` 完整实现 (initialize/tools/list/tools/call) + HTTP + LangChain 工具注册 | — |
| **Cron** | 可挂 skill + script, 多调度格式, 任意平台投递 | ✅ 已实现 (command/prompt/skill) | ⚠️ 仅 cron 表达式, 无人类友好格式 |
| **ACP (VS Code/Zed stdio)** | JSON-RPC IDE 集成 | ⚠️ 已有 ACP-lite stdio JSON-RPC（thread/workspace context、applyEdit、thread-scoped editor state、基础 diagnostics、state/diagnostics notifications、prepareRename、findReferences、rename-lite、code-actions-lite、resolveCodeAction preview、formatDocument(JSON)、structured workspace_edit payload、workspace_edit round-trip apply、diagnostics quick-fix(JSON comments + trailing commas + Python missing colon + Python unused imports + JS/TS missing semicolons)、local client bind）+ agent-facing editor tools + 自动 editor-context prompt 注入 | 仍未做 Claude Code 级 IDE-native live editor protocol / full code actions / full rename / diagnostics push 集成 |

---

## 二、Hermes Agent 关键特性 vs 本项目

| 特性 | Hermes 规范 | 本项目状态 | 缺口说明 |
|---|---|---|---|
| **闭环学习 (自创技能 + 自改进)** | 核心卖点 | ✅ 已实现 (`self_evolution.py` + `learning_loop.py` + GEPA 真跑通) | — |
| **SOUL.md 人格文件** | 系统 prompt 首位 | ✅ 已实现 (多路径 fallback) | — |
| **MEMORY.md / USER.md / AGENTS.md / .hermes.md** | 多文件分层 | ✅ `_load_context_files()` 读取 4 文件并注入 system_prompt (both flows) | — |
| **FTS5 全文索引 session** | SQLite + FTS5 | ✅ `SessionSearchDB` + 启动自动 rebuild + snippet/bm25 | — |
| **Session lineage (压缩父子)** | parent/child tracking | ✅ Thread.parent_id + compact_summary + fork/lineage/children API + 5 回归测试 | — |
| **6 terminal backends** | local/Docker/SSH/Daytona/Modal/Singularity | ❌ 只有本地 shell | 大缺口 |
| **18 messaging 平台** | TG/Discord/Slack/WA/Signal/Matrix/Email/... | ❌ 仅 Web UI | 非目标 |
| **Provider Runtime Resolver** | 18+ provider + OAuth + credential pools | ⚠️ 已有 provider presets/runtime resolver + OAuth register/authorize/callback/exchange + key pools | 仍未覆盖 Hermes 级 18+ provider breadth |
| **Programmatic Tool Calling** | `execute_code` 把多步串成一次推理 | ✅ `execute_tool_chain` — tools 命名空间 + await + results 收集 + 4 个回归测试 | — |
| **Memory providers 单选插件** | 可替换 | ✅ 已实现 + 示例插件 | — |
| **Context engines 单选插件** | 可替换 | ✅ 已实现 + 示例插件 + 主循环接入 | — |
| **Atropos/RL/trajectories** | RL 训练数据导出 | ❌ 未实现 | 非用户需求 |
| **agentskills.io 兼容** | 开放 skill 格式 | ⚠️ 自家 skill 格式, 未检查 agentskills.io schema | 待验证 |
| **Voice mode** | 语音接入 | ❌ 未实现 | — |
| **Context compressor** | 中段摘要 | ✅ 已实现 (`compact_state`) | — |
| **Prompt caching (Anthropic breakpoints)** | `cache_control` | ✅ 已实现 (`inject_cache_breakpoints`) | — |

---

## 三、本项目领先于两者的能力

| 特性 | 说明 |
|---|---|
| **GEPA 遗传进化 prompt** | 真 LLM 驱动 + rule-based fallback, baseline 0.34→0.58 (+24%) 真实验证. Claude Code 无, Hermes 未披露 |
| **前端统一控制台 `/hermes`** | 5 tab (Subagent/Hooks/Cron/Plugins/GEPA) 单页管理. Claude Code 是 CLI, Hermes 主要 CLI |
| **Elicitation 结构化输入** | `text/select/boolean` 字段收集 |
| **ModelScope + Qwen 真 provider 集成** | 国内用户友好 |
| **SuperAgent + 多层 Mode (standard/speculation/research)** | 本项目独有的推理模式切换 |

---

## 四、优先级修复清单 (按 ROI 排序)

### 高优先级 (短期应做)

1. **集成 hook 的 `modified_params` 回 agent 主循环**
   - 现状: `HookResult.modified_params` 能生成但 super_agent 未读
   - 做法: 在 `_execute_tool` 前读取 hook 返回的 modified_params 覆盖原始参数
   - ROI: 高 — 这是 Claude Code PreToolUse 最核心的能力

2. **实现权限规则语法 (allow/ask/deny + Tool specifier)**
   - 现状: 只有 hook 级 deny, 无声明式规则
   - 做法: 新 `PermissionRegistry` 解析 `Bash(git *)` 等模式
   - ROI: 高 — Claude Code 生产环境基石

3. **MCP 真客户端连接 (stdio 协议最小实现)**
   - 现状: 配置存得了, 但连不上真 MCP server
   - 做法: 用 `mcp` python 包实现 stdio transport
   - ROI: 高 — 生态接入关键

### 中优先级

4. **MEMORY.md / USER.md 接入 prompt builder**
   - 现状: 有文件读 API, 但 system_prompt 未拼
   - 做法: `super_agent._build_system_prompt` 在 SOUL.md 后追加

5. **FTS5 SQLite session 存储**
   - 现状: JSON + 线性搜索
   - 做法: 新 `sqlite_session_store.py`, 迁移工具把 JSON 导入

6. **前端 slash 命令解析** (`/compact` `/tasks` `/usage` `/model`)
   - 现状: 后端已有, 前端未拦截
   - 做法: 输入框 `onSubmit` 检查 `/` 开头, 派发到对应 API

### 低优先级 / 非目标

- ACP IDE 集成 (Windsurf 用户无需)
- Docker/SSH/Modal 远程 terminal backends
- 18 平台 messaging gateway
- Atropos RL 训练 pipeline
- sandboxing (seatbelt/bubblewrap)

---

## 五、整体完成度对照 (2026-04-23 最终)

| 对照目标 | 初始 | R1 | R2 | R3 | **R4** | 备注 |
|---|---:|---:|---:|---:|---:|---|
| Claude Code 核心平台 (排除非目标 20 项) | ~75% | ~88% | ~91% | ~91% | **~95%** | +bash cwd +slash接入 +auto classifier |
| Hermes Agent 开发者面 (排除非目标 12 项) | ~55% | ~65% | ~80% | ~86% | **~88%** | +session lineage, 仅剩 OAuth pool + agentskills.io |
| 本项目独有 | + | + | + | + | **+** | GEPA 真进化, 统一 Hermes 控制台 (8 tabs), ModelScope provider |

**测试全景** (2026-04-23 R4):
- 后端 unittest + pytest: **534 passed / 10 skipped / 0 failed**
- 含 5 个真实 LLM E2E (ModelScope Qwen3-Coder-30B)
- 含 2 个真实 MCP stdio 子进程测试
- 前端 TypeScript: 0 errors
- 路由: 187 条, 0 冲突

## 六、本轮第二阶段新增实现 (Round 2)

| 功能 | 实现 | 测试 |
|---|---|---|
| **FTS5 SQLite session 搜索** | `session_search.py`: SQLite + FTS5 virtual table + snippet() + bm25 ranking; 自动镜像 `ThreadStore.add_message` 和 `delete` | **9 个真 SQL 测试** (索引/短语/过滤/去重重建/统计/高亮/空查询/rebuild) |
| **前端 slash 命令** | `slash.ts`: `/compact` `/tasks` `/usage` `/models` `/search` `/clear` `/help`; 自动补全下拉; 箭头/Tab/Enter 导航; `slash:clear` CustomEvent | TS 0 error + 路由 200 |
| **Memory Provider 插件化** | `provider_plugins.py`: Hermes 单选模式; Protocol-based duck typing; 自动发现 `~/.hermes/plugins/<name>/memory_provider.json`; importlib 动态加载 | **3 个真 importlib 测试** (发现/激活使用/未知拒绝) |
| **Context Engine 插件化** | 同上架构, `context_engine.json` 元数据 | **1 个真 importlib 测试** (发现 + build_context) |
| **权限作用域层级** | `permission_scopes.py`: Managed / User / Project / Local 四层; 兼容 `.hermes/` + `.claude/` 路径; 去重合并; Managed 优先 | **4 个真文件系统测试** (单层/多层去重/顺序/Claude 兼容) |
| **MCP 前端 UI** | Hermes 控制台新增 3 tab: MCP (stdio + HTTP 配置)、记忆/上下文 (provider 激活)、权限作用域 (分层可视化) | 路由 200 + TS 0 error |

## 七、仍未覆盖 (诚实)

**真非目标 (不会做)**:
- 18 平台 messaging gateway
- 6 terminal backends (Docker / SSH / Daytona / Modal / Singularity)
- ACP IDE 集成
- Atropos RL 训练管道
- sandboxing (seatbelt/bubblewrap)

**可做但优先级低**:
- agentskills.io schema 兼容
- LSP 工具组 (jump-to-def, references, call hierarchy)
- Bash env 持久化 (CLAUDE_ENV_FILE 等价物)
- Provider OAuth/credential pool

**外部依赖 (无法单测)**:
- Anthropic prompt cache 真命中率 (需 Anthropic 订阅)
- 真 MCP server 生态 (需部署 `@modelcontextprotocol/server-filesystem` 等)
