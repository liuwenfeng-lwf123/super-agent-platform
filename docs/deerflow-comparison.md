# 天工流 vs DeerFlow 2.0 功能对照报告

> 生成日期：2026-04-26 | 更新：2026-04-26（Phase 1-4 实施后）| DeerFlow 2.0 = 100% 基准

## 一、总分

| 维度 | DeerFlow 2.0 (基准) | 天工流 | 对照 |
|------|:---:|:---:|:---:|
| **加权综合得分** | **100%** | **~78%** | 核心能力达标，IM/沙箱/社区差距大；独有能力 DeerFlow 没有 |

---

## 二、逐项对照（30+ 维度）

### A. Agent 核心架构

| # | 功能维度 | DeerFlow 2.0 | 天工流 | 匹配度 |
|---|---------|-------------|--------|:------:|
| 1 | **多模式推理** (flash/standard/pro/ultra) | ✅ 4 模式 | ✅ 5 模式（极速/标准/专业/旗舰/本地） | **100%** |
| 2 | **Sub-Agent 动态派生** | ✅ Lead agent 运行时 spawn，并行执行，结构化返回 | ✅ Hermes 持久化子 Agent + spawn/resume/team/worktree | **90%** |
| 3 | **内置编排角色** | ✅ 隐含在 lead agent 规划中 | ✅ planner/verifier/coder/researcher/writer/analyst/searcher 显式角色 | **100%** |
| 4 | **LangGraph 状态机** | ✅ 核心基于 LangGraph | ⚠️ 基于 LangChain 但非 LangGraph 状态图 | **60%** |

### B. 技能 & 工具系统

| # | 功能维度 | DeerFlow 2.0 | 天工流 | 匹配度 |
|---|---------|-------------|--------|:------:|
| 5 | **Skill 定义格式** | Markdown SKILL.md + frontmatter | JSON skill 定义 | **80%** |
| 6 | **技能渐进加载** (按需注入 context) | ✅ 只在需要时加载 | ✅ 按技能名合并 tools | **85%** |
| 7 | **MCP Server 集成** | ✅ HTTP/SSE + OAuth | ✅ MCP 集成（`skills/mcp.py`） | **85%** |
| 8 | **内置内容型技能** (研究/报告/PPT/网页/图片/视频生成) | ✅ 5+ 专用技能 | ⚠️ 通用技能为主，无 PPT/视频生成专用 skill | **40%** |
| 9 | **Claude Code 集成** | ✅ `claude-to-deerflow` skill | ❌ 无 | **0%** |
| 10 | **工具市场/生态** | ✅ `.skill` archives via Gateway | ✅ ClawHub / OpenClaw 导入 | **80%** |
| 11 | **InfoQuest 智能搜索** | ✅ BytePlus 独立搜索引擎 | ⚠️ 通用 web search 工具 | **50%** |

### C. 沙箱 & 文件系统

| # | 功能维度 | DeerFlow 2.0 | 天工流 | 匹配度 |
|---|---------|-------------|--------|:------:|
| 12 | **Docker 容器隔离沙箱** | ✅ AioSandboxProvider, per-thread | ⚠️ 有 Docker runtime backend 但非默认强隔离 | **55%** |
| 13 | **Kubernetes Pod 沙箱** | ✅ K8s provisioner | ❌ 无 | **0%** |
| 14 | **本地沙箱执行** (Python/JS/Bash) | ✅ LocalSandboxProvider | ✅ `sandbox/manager.py` 完整 | **90%** |
| 15 | **文件系统视图** (uploads/workspace/outputs) | ✅ 结构化目录 | ✅ WorkspacePanel + 输出下载 | **85%** |
| 16 | **SSH 远程运行时** | ❌ 无 | ✅ `ssh_runtime_backend.py` | **天工流独有** |

### D. Context Engineering

| # | 功能维度 | DeerFlow 2.0 | 天工流 | 匹配度 |
|---|---------|-------------|--------|:------:|
| 17 | **上下文压缩** | ✅ Summarization + filesystem offload | ✅ auto/micro/emergency 三级 + compact_boundary 增量 + 熔断 | **110%** ✨ |
| 18 | **子 Agent 隔离上下文** | ✅ 每个 sub-agent 独立 | ✅ 子 Agent 独立 context | **95%** |
| 19 | **Tool-call 异常恢复** | ✅ strip metadata + placeholder results | ⚠️ 有 fallback 重试，但未见 tool_call_id 修复 | **70%** |

### E. 记忆系统

| # | 功能维度 | DeerFlow 2.0 | 天工流 | 匹配度 |
|---|---------|-------------|--------|:------:|
| 20 | **长期记忆** | ✅ 跨会话持久化，profile/偏好/知识 | ✅ 4 层记忆 (Project/User/Agent/Session) | **110%** ✨ |
| 21 | **去重** | ✅ 写入时去重 | ✅ 提取 + 存储去重 | **100%** |
| 22 | **向量搜索** | ✅ 向量数据库 | ✅ TF-IDF cosine（零外部依赖） | **75%** |

### F. IM 频道 & 网关

| # | 功能维度 | DeerFlow 2.0 | 天工流 | 匹配度 |
|---|---------|-------------|--------|:------:|
| 23 | **IM 渠道数量** | ✅ **5 个**（飞书/微信/企微/Slack/Telegram） | ⚠️ 1 个（Telegram transport） | **20%** |
| 24 | **Gateway follow-up suggestions** | ✅ 解析 model 输出生成建议 | ⚠️ 推测模式有 suggestion，但不同范式 | **50%** |

### G. 可观测性 & 追踪

| # | 功能维度 | DeerFlow 2.0 | 天工流 | 匹配度 |
|---|---------|-------------|--------|:------:|
| 25 | **LangSmith** | ✅ | ✅ | **100%** |
| 26 | **Langfuse** | ✅ | ❌ | **0%** |
| 27 | **内置工具监控面板** | ❌ 无独立 UI | ✅ ToolMonitorPanel + runtime_observability | **天工流独有** |

### H. 安全 & 权限

| # | 功能维度 | DeerFlow 2.0 | 天工流 | 匹配度 |
|---|---------|-------------|--------|:------:|
| 28 | **安全策略** | ✅ IP allowlist / auth gateway / 网络隔离 | ✅ 10 层 Bash 分析 + 4 级风险分类 + 权限矩阵 + 交互审批 + 审计日志 | **120%** ✨ |

### I. 部署 & 生态

| # | 功能维度 | DeerFlow 2.0 | 天工流 | 匹配度 |
|---|---------|-------------|--------|:------:|
| 29 | **Docker Compose 部署** | ✅ 完整多容器编排 | ✅ 单 Dockerfile + compose | **70%** |
| 30 | **Embedded Python Client** | ✅ DeerFlowClient 内嵌库 | ⚠️ `local_client.py` WebSocket，非嵌入式 | **50%** |
| 31 | **多语言文档** | ✅ 5 语言 (EN/ZH/JA/FR/RU) | ⚠️ 中英双语 README | **40%** |
| 32 | **官方网站** | ✅ deerflow.tech | ❌ 无 | **0%** |
| 33 | **社区热度** | ✅ 41k stars, GitHub Trending #1 | ⚠️ 私有/早期阶段 | **5%** |

---

## 三、天工流独有能力（DeerFlow 2.0 不具备）

| # | 独有功能 | 说明 | 竞争优势 |
|---|---------|------|:--------:|
| A | **Speculation 模式** | Shadow workspace + hunk 级 accept/reject，类 Claude Code | ⭐⭐⭐⭐⭐ |
| B | **工具自进化** | Agent 运行时自建工具和技能（`evolution.py` + `self_evolution.py`） | ⭐⭐⭐⭐⭐ |
| C | **Prompt Cache 感知计费** | 分别追踪 cache_creation / cache_read tokens + 预算控制 | ⭐⭐⭐⭐ |
| D | **浏览器自动化** | Playwright 受控浏览器 + 截图/填表/抓取/JS执行 | ⭐⭐⭐⭐ |
| E | **文件历史** | 每次写入自动记录 unified diff | ⭐⭐⭐ |
| F | **本地电脑操控** | WebSocket local client 直接操作本机文件系统/App | ⭐⭐⭐⭐ |
| G | **LSP & 静态代码分析** | 语言服务协议 + TypeScript bridge | ⭐⭐⭐ |
| H | **SSH 远程运行时** | 远程服务器执行能力 | ⭐⭐⭐ |
| I | **学习循环** | `learning_loop.py` 自学习闭环 | ⭐⭐⭐⭐ |
| J | **工具监控仪表盘** | 前端可视化所有工具调用 | ⭐⭐⭐ |

---

## 四、按类别汇总打分

| 类别 | 权重 | 天工流匹配度 | 加权得分 |
|------|:----:|:-----------:|:-------:|
| **Agent 核心架构** | 20% | 88% | 17.5% |
| **技能 & 工具** | 15% | 55% | 8.3% |
| **沙箱 & 文件系统** | 15% | 50% | 7.5% |
| **Context & 记忆** | 15% | 95% | 14.3% |
| **IM 频道** | 10% | 20% | 2.0% |
| **可观测 & 追踪** | 5% | 65% | 3.3% |
| **安全 & 权限** | 10% | 120% | 12.0% |
| **部署 & 生态** | 10% | 33% | 3.3% |
| **总计** | **100%** | — | **68.2%** |
| **+ 独有能力加分** | — | — | **+10%** |
| **最终综合得分** | — | — | **~78%** |

---

## 五、差距最大的方向（优先补齐建议）

| 优先级 | 差距 | DeerFlow 实现 | 建议 |
|:------:|------|-------------|------|
| 🔴 P0 | **IM 频道**（20%） | 飞书/微信/企微/Slack/Telegram 5 渠道 | 补齐飞书 + 企微 + Slack |
| 🔴 P0 | **Docker 隔离沙箱**（55%） | AioSandboxProvider 容器级隔离 | 升级为默认沙箱模式 |
| 🟡 P1 | **内置内容型 Skill**（40%） | 研究/报告/PPT/网页/图片/视频 6 种 | 新建 research / report / slide Skill |
| 🟡 P1 | **Langfuse 追踪**（0%） | 双 tracing provider | 接入 Langfuse callback |
| 🟡 P2 | **官方网站 + 多语言文档**（20%） | deerflow.tech + 5 语言 | 搭建 landing page + 翻译 README |
