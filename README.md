# Super Agent Platform

多模态 AI Agent 平台，对标 Claude Code 核心能力，支持多 Agent 协作、工具沙箱、Speculation、分层记忆等。

## 架构概览

```
frontend/          Next.js 前端 (port 3001)
backend/           FastAPI 后端 (port 8001)
local_client.py    本地模式客户端 (WebSocket)
```

### 后端模块

| 模块 | 说明 |
|---|---|
| `agents/super_agent.py` | 主 Agent 逻辑，流式响应 |
| `agents/orchestrator.py` | 内置编排角色（`/api/agents`）与 Bash 安全分析 |
| `agents/subagents.py` | Hermes / Claude 风格持久化子 Agent（`/api/subagents`） |
| `agents/cost_tracker.py` | Token 计费（含 prompt cache），预算控制 |
| `agents/context.py` | 上下文管理，多级自动压缩 |
| `agents/tool_runtime.py` | 工具元数据、权限、延迟加载 |
| `agents/tools.py` | 核心工具定义 |
| `agents/system_tools.py` | 系统工具（截图、Git、记忆、文件历史） |
| `agents/evolution.py` | 自进化引擎（自建工具/技能） |
| `memory/` | 分层记忆（project/user/agent/session） |
| `sandbox/manager.py` | 沙箱执行（Python/JS/Bash）+ 文件历史 |
| `skills/` | 技能系统、MCP 集成、IM 频道 |
| `rag/` | RAG 存储 |

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- pnpm（或 npm）

### 1. 配置环境变量

```bash
cp backend/.env.example backend/.env
# 编辑 .env，填入 OPENAI_API_KEY 或兼容 API 的 key/base_url
```

关键环境变量：

| 变量 | 必填 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | LLM API Key |
| `OPENAI_BASE_URL` | 可选 | 自定义 API 端点 |
| `DEFAULT_MODEL` | 可选 | 默认模型名称 |
| `LANGSMITH_TRACING` | 可选 | 开启 LangSmith 追踪 |

### 2. 启动（开发模式）

```bash
# 一键启动前后端
bash start.sh

# 或分别启动：
# 后端
cd backend && pip install -r requirements.txt && uvicorn app.main:app --port 8001 --reload

# 前端
cd frontend && pnpm install && pnpm dev
```

访问 http://localhost:3001

后端 API 根路径：`http://localhost:8001/api`

### 3. 初始化演示数据

服务启动后，可以写入一组可重复的演示数据，方便快速展示 Memory、Knowledge 和会话轨迹能力：

```bash
python scripts/demo_seed.py
```

也可以在前端进入 `设置 → 演示`，点击“初始化演示数据”。前端会调用后端接口：

```bash
POST /api/demo/seed?clean=true&dry_run=false
```

只查看将要写入的内容，不请求后端：

```bash
python scripts/demo_seed.py --dry-run
```

默认会先清理旧的 demo 数据再写入；如果想保留旧数据：

```bash
python scripts/demo_seed.py --no-clean
```

### 4. 本地模式

本地模式允许 Agent 操作你的本地文件系统：

```bash
python local_client.py
```

然后在前端切换到"本地模式"并绑定客户端。

## Docker 部署

```bash
docker compose up -d
```

访问 http://localhost:3001

## 运行测试

推荐先跑一键本地质量闸门：

```bash
python scripts/quality_gate.py
```

快速跳过前端生产构建：

```bash
python scripts/quality_gate.py --skip-build
```

包含后端全量 pytest：

```bash
python scripts/quality_gate.py --full-backend
```

也可以拆开手动运行：

```bash
cd backend
python -m pytest tests/ -v

cd ../frontend
npx tsc --noEmit
npm run build
npm run check:tool-validation
```

## 生产验收 / 冒烟检查

服务启动后，运行冒烟检查确认前端、后端和关键只读 API 正常：

```bash
python scripts/smoke_check.py
```

如果只检查后端：

```bash
python scripts/smoke_check.py --skip-frontend
```

如果部署环境要求 `/ready` 必须完全 ready，例如必须配置可用 LLM API Key：

```bash
python scripts/smoke_check.py --strict-ready
```

可通过环境变量覆盖地址：

```bash
BACKEND_URL=http://localhost:8001 FRONTEND_URL=http://localhost:3001 python scripts/smoke_check.py
```

冒烟检查覆盖：

- 后端 `/health`
- 后端 `/ready`
- API `/api/health`
- Provider / Model / Skill 列表
- Runtime 状态
- Statusline
- 前端首页

## 生产安全策略

后端提供统一权限矩阵：

```bash
GET /api/security/policy
```

工具风险分为：

```text
safe                只读或低风险工具
approval_required   写入、网络、浏览器、插件、Agent 编排等需要审批
dangerous           shell、代码执行、本地执行等高风险工具
disabled            生产环境默认禁用工具
```

生产模式通过环境变量启用：

```bash
APP_ENV=production
```

生产模式下：

```text
只读工具默认允许
写入/网络/浏览器类工具默认需要审批
shell/代码执行/插件演化等高危工具默认拒绝
bypassPermissions 也不能绕过生产安全策略的 deny
```

可以追加禁用工具：

```bash
SECURITY_DISABLED_TOOLS=write_file,http_request
```

权限决策会写入安全审计日志，可查询最近事件：

```bash
GET /api/security/audit?limit=100
GET /api/security/audit?thread_id=<thread_id>
GET /api/security/audit?tool=execute_bash
GET /api/security/audit?decision=deny
```

也可以在前端进入 `设置 → 安全`，查看当前权限矩阵和最近审计记录。

## Live 联调测试

默认测试不会强制调用真实外部服务。需要真实环境时，按需开启：

```bash
cd backend

REAL_LLM_E2E=1 python -m pytest tests/test_real_llm_e2e.py -v
RUN_DOCKER_LIVE=1 python -m pytest tests/test_live_dependency_matrix.py -v
RUN_SSH_LIVE=1 python -m pytest tests/test_live_dependency_matrix.py -v
RUN_DASHSCOPE=1 python -m pytest tests/test_dashscope.py -v
RUN_GLM5_E2E=1 python -m pytest tests/test_glm5_e2e.py -v
RUN_PROMPT_CACHE_E2E=1 python -m pytest tests/test_live_dependency_matrix.py -v
```

## Agent 入口说明

| 入口 | 作用 | 主要文件 | API / UI |
|---|---|---|---|
| **主聊天 Agent** | 默认聊天、多模式推理、skills/memory/RAG/speculation 注入 | `backend/app/agents/super_agent.py` | `/api/chat` + 主页面 |
| **内置编排角色** | planner / verifier / coder / researcher / writer / analyst / searcher 这类临时协作角色 | `backend/app/agents/orchestrator.py` | `/api/agents` |
| **持久化 Subagents** | `explore / plan / general-purpose` 及用户自定义子 Agent，支持 spawn/resume/team/worktree | `backend/app/agents/subagents.py` | `/api/subagents` + `/hermes` |

## 核心能力

- **双层 Agent 体系** — 主聊天 Agent + 内置编排角色（`/api/agents`）+ 持久化 Subagents（`/api/subagents`）
- **10 层 Bash 安全分析** — 危险命令、权限提升、编码绕过、数据泄露检测
- **Prompt Cache 感知计费** — 分别追踪 cache_creation / cache_read tokens
- **多级上下文压缩** — auto/micro/emergency 三级 + compact_boundary 增量摘要 + 熔断机制
- **分层记忆** — Project / User / Agent / Session 四层，含大小限制
- **向量记忆搜索** — TF-IDF cosine similarity（零外部依赖）
- **Speculation 模式** — Shadow workspace + hunk 级 accept/reject
- **工具自进化** — Agent 可运行时创建工具和技能
- **文件历史追踪** — 每次写入自动记录 unified diff
- **权限规则系统** — always_allow / always_deny / always_ask + 通配符
- **交互式权限审批** — 工具执行前请求用户确认
- **预算控制** — `maxBudgetUsd` 超限自动停止

## 项目结构

```
super-agent-platform/
├── backend/
│   ├── app/
│   │   ├── agents/          # Agent 核心逻辑
│   │   ├── api/             # FastAPI 路由
│   │   ├── local/           # 本地模式
│   │   ├── memory/          # 记忆系统
│   │   ├── models/          # 数据模型
│   │   ├── rag/             # RAG
│   │   ├── sandbox/         # 代码沙箱
│   │   └── skills/          # 技能/MCP
│   ├── tests/               # 单元测试
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── app/             # Next.js 页面
│       ├── components/      # React 组件
│       ├── lib/             # API 客户端
│       └── types/           # TypeScript 类型
├── docs/                    # 文档
├── local_client.py          # 本地客户端
├── start.sh                 # 一键启动脚本
└── docker-compose.yml       # Docker 部署
```

## License

MIT
