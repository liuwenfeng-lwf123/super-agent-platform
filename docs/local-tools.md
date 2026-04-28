# Local Tools 功能文档

> 天工流 (TianGongFlow) 本地工具集 — 让 AI 直接操作你的电脑

## 架构

```
Frontend (Next.js)  ←SSE→  Backend (FastAPI)  ←WebSocket→  Local Client (Python)
     UI 显示               工具调度 + LLM            实际执行命令
```

## 工具列表

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| `local_execute_bash` | 执行 Shell 命令 | `command`, `cwd`(工作目录) |
| `local_read_file` | 读文件（支持行范围） | `path`, `start_line`, `end_line` |
| `local_write_file` | 写文件 | `path`, `content` |
| `local_edit_file` | 精确查找替换编辑 | `path`, `old_string`, `new_string` |
| `local_search_code` | 用 ripgrep/grep 搜代码 | `pattern`, `path`, `include` |
| `local_git` | Git 操作 | `args`, `cwd` |
| `local_project_index` | 项目结构索引 | `path` |
| `local_undo_edit` | 撤销编辑 | `path` |
| `local_execute_python` | 执行 Python 代码 | `code` |
| `local_list_files` | 列出目录内容 | `path` |
| `local_open_app` | 打开应用程序 | `app_name` |
| `local_get_system_info` | 获取系统信息 | — |
| `local_read_clipboard` | 读剪贴板 | — |
| `local_write_clipboard` | 写剪贴板 | `content` |
| `local_send_notification` | 发系统通知 | `title`, `message` |
| `local_manage_window` | 管理窗口 | `action`, `app_name` |
| `local_create_schedule` | 创建定时任务 | `message`, `interval_minutes` |
| `local_list_schedules` | 列出定时任务 | — |
| `local_delete_schedule` | 删除定时任务 | `schedule_id` |
| `local_upload_to_workspace` | 上传文件到工作区 | `path` |
| `local_download_from_workspace` | 从工作区下载文件 | `path`, `data` |

## 特色功能

### 1. 实时终端输出
`execute_bash` 的输出通过 WebSocket → SSE 实时推送到前端，黑底绿字终端窗口显示。

### 2. 大文件智能摘要
读取大文件（>500行 或 >15K字符）时，自动返回：
- 前 50 行 + 后 20 行
- 结构大纲（函数/类定义）
- 提示使用 `start_line/end_line` 读取具体段落

### 3. 编辑 Diff 预览
`edit_file` 返回类似 `git diff` 的红绿对比，显示改了第几行、改前改后。

### 4. 自动备份
每次 `edit_file` 前自动备份到 `.super-agent-backups/` 目录。

### 5. 文件变更摘要
对话结束时，前端会显示"本次修改了 N 个文件"的摘要。

### 6. 搜索结构化输出
`search_code` 结果格式化为 `文件:行号  内容`，最多 30 条。

### 7. 项目索引缓存
`project_index` 结果缓存 5 分钟，避免大项目重复扫描。

### 8. 断线自动重连
客户端断线后自动指数退避重连（3s → 6s → 12s → ...，最大 60s），带随机抖动。

### 9. API 认证
在 `.env` 设置 `API_SECRET_TOKEN=your-secret` 启用。
- HTTP 请求：`Authorization: Bearer <token>` 头
- WebSocket：`?token=<token>` 查询参数
- 不设置 = 开发模式（无认证）

### 10. 撤销编辑
`local_undo_edit` 工具可以恢复文件到上一个版本，支持多次 undo。

### 11. 备份自动清理
每个文件最多保留 20 个备份，超出自动删除最旧的。

### 12. 日志
客户端运行日志写入 `~/.tiangongflow/client.log`（5MB 自动轮转）。

## 启动方式

```bash
# 启动后端
cd backend && uvicorn app.main:app --port 8001

# 启动本地客户端（另一个终端）
python local_client.py --server ws://localhost:8001/ws/local-client

# 启动前端
cd frontend && npm run dev
```

## 安全

- 所有命令执行前需用户确认（除非 `--auto-approve`）
- `edit_file` / `write_file` 有路径白名单
- `git_command` 使用 `shlex.split` 防注入
- 流式队列使用 `contextvars` 保证并发隔离
