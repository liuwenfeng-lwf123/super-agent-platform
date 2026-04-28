# API 参考文档

Base URL: `http://localhost:8001/api`

---

## 核心对话

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/chat` | 发送消息（SSE 流式响应） |
| GET | `/threads` | 列出所有线程 |
| GET | `/threads/{thread_id}` | 获取线程详情 |
| DELETE | `/threads/{thread_id}` | 删除线程 |
| GET | `/health` | 健康检查 |

### POST /chat

```json
{
  "thread_id": "optional-thread-id",
  "message": "你好",
  "model": "gpt-4o",
  "mode": "standard",
  "attachments": [],
  "context": {}
}
```

SSE 事件类型：`token`, `tool_start`, `tool_result`, `tool_approval`, `error`, `done`, `cost`

---

## 模型管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/models` | 列出可用模型 |
| POST | `/models` | 添加自定义模型 |
| DELETE | `/models/{name}` | 删除模型 |

---

## 记忆系统

### 基础记忆

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/memory` | 列出所有记忆条目 |
| POST | `/memory` | 添加记忆 `?key=&value=&category=` |
| GET | `/memory/search` | 搜索记忆 `?query=` |
| DELETE | `/memory/{entry_id}` | 删除记忆 |

### 分层记忆

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/memory/stats` | 记忆系统统计 |
| GET | `/memory/project` | 获取项目记忆 (MEMORY.md) |
| POST | `/memory/project` | 更新项目记忆 `?content=` |
| GET | `/memory/user` | 获取用户记忆 `?user_id=default` |
| POST | `/memory/user` | 设置用户记忆 `?key=&value=&category=` |
| GET | `/memory/agent/{agent_type}` | 获取 Agent 记忆 |
| POST | `/memory/agent/{agent_type}` | 添加 Agent 记忆 |
| GET | `/memory/context` | 构建上下文 `?query=&agent_type=` |

### 记忆提取 & 整理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/memory/extraction/stats` | 提取统计 |
| POST | `/memory/extract` | 手动触发记忆提取 |
| GET | `/memory/dream/state` | 整理状态 |
| POST | `/memory/dream/consolidate` | 触发记忆整理 |

---

## 成本追踪

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/cost` | 会话费用汇总 |
| GET | `/cost/today` | 今日费用 |
| GET | `/cost/history` | 历史记录 `?limit=100` |
| GET | `/cost/models` | 按模型分类 |
| GET | `/cost/budget` | 预算状态 |
| POST | `/cost/budget` | 设置预算 `?max_usd=` |

---

## 上下文管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/context/compact` | 压缩状态（boundary, compactions） |
| GET | `/context/tokens` | Token 估算 `?text=` |

---

## 工具 & 权限

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/tools` | 列出所有工具（含元数据） |
| GET | `/agents` | 列出 orchestrator 内置 Agent 角色定义 |
| GET | `/safety/bash` | Bash 安全检查 `?command=` |
| GET | `/permissions/rules` | 获取权限规则 |
| POST | `/permissions/rules` | 设置权限规则 |
| GET | `/permissions/pending` | 待审批请求 |
| POST | `/permissions/{request_id}/approve` | 批准 |
| POST | `/permissions/{request_id}/deny` | 拒绝 |
| GET | `/policy-limits` | 获取策略限制 |
| POST | `/policy-limits` | 设置策略限制 |

---

## Subagents

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/subagents` | 列出持久化子 Agent 定义 |
| POST | `/subagents/create` | 创建子 Agent |
| DELETE | `/subagents/{name}` | 删除子 Agent |
| POST | `/subagents/spawn` | 启动子 Agent |
| GET | `/subagents/instances` | 列出运行实例 |
| GET | `/subagents/instance/{agent_id}` | 获取实例详情 |
| POST | `/subagents/{agent_id}/message` | 给已完成/暂停实例追加消息并恢复 |
| POST | `/subagents/team` | 创建子 Agent 团队 |
| GET | `/subagents/teams` | 列出团队 |
| POST | `/subagents/instance/{agent_id}/cleanup-worktree` | 清理实例 worktree |
| GET | `/subagents/{agent_name}/memory` | 获取子 Agent 持久记忆 |
| POST | `/subagents/{agent_name}/memory` | 保存子 Agent 持久记忆 |

---

## 沙箱 & 文件

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sandbox/execute` | 执行代码 |
| GET | `/workspace/{thread_id}/files` | 列出工作区文件 `?path=.` |
| GET | `/workspace/{thread_id}/read` | 读取文件 `?path=` |
| GET | `/workspace/{thread_id}/download/{path}` | 下载文件 |
| GET | `/threads/{thread_id}/file-history` | 文件变更历史 `?path=&limit=50` |
| POST | `/upload` | 上传文件 |

---

## Speculation 模式

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/speculation/{thread_id}` | 获取 Speculation 状态 |
| DELETE | `/speculation/{thread_id}` | 清除 Speculation |
| GET | `/speculation/{thread_id}/changes` | 查看变更 |
| GET | `/speculation/{thread_id}/diff` | 查看 Diff（含 hunks） |
| POST | `/speculation/{thread_id}/accept` | 接受变更 |

Accept body:
```json
{
  "paths": ["file1.py"],
  "hunks": [{"file": "file1.py", "hunk_index": 0}]
}
```

---

## 知识库 (RAG)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/knowledge` | 列出文档 |
| POST | `/knowledge/upload` | 上传文档 (multipart) |
| POST | `/knowledge/text` | 添加文本 `?name=&content=` |
| DELETE | `/knowledge/{doc_id}` | 删除文档 |
| GET | `/knowledge/search` | 搜索知识库 `?query=&top_k=5` |

---

## MCP 集成

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/mcp/servers` | 列出 MCP 服务器 |
| POST | `/mcp/servers` | 注册服务器 |
| DELETE | `/mcp/servers/{name}` | 移除服务器 |
| GET | `/mcp/tools` | 列出 MCP 工具 |
| POST | `/mcp/call` | 调用 MCP 工具 |
| POST | `/mcp/discover` | 发现所有工具 |

---

## 技能系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/skills` | 列出技能 |
| POST | `/skills/recommend` | 推荐技能 `?query=` |

---

## 任务管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/tasks` | 列出活跃任务 |
| GET | `/tasks/{task_id}` | 获取任务状态 |
| DELETE | `/tasks/{task_id}` | 取消任务 |

---

## Magic Docs

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/magic-docs` | 列出文档 |
| POST | `/magic-docs/register` | 注册文档 |
| POST | `/magic-docs/sync` | 同步文档 |
| POST | `/magic-docs/auto-sync` | 设置自动同步 |

---

## 频道

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/channels` | 列出频道 |
| GET | `/channels/{channel_type}/status` | 频道状态 |

---

## 追踪 (Tracing)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/tracing/status` | LangSmith 追踪状态 |
| POST | `/tracing/configure` | 配置追踪 |

---

## 本地模式

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/local/clients` | 列出本地客户端 |
| POST | `/local/clients/{id}/auto-approve` | 设置自动批准 |
| POST | `/local/bind-thread` | 绑定线程 |
| GET | `/local/audit` | 审计日志 `?limit=100` |
| POST | `/local/chat` | 本地模式对话（SSE） |

---

**共计 60+ 个 API 端点**，覆盖对话、记忆、成本、工具、沙箱、Speculation、RAG、MCP、技能、任务、追踪等全模块。
