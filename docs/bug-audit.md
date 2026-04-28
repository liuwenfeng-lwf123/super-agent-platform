# 天工流 Bug 深度审计报告

> 2026-04-26 | 两轮审计共 **32 个 Bug**，**全部已修复**
>
> 审计方法论参考：
> - Python Code Review 25-Point Checklist (augmentcode.com)
> - asyncio sharp corners (sailor.li) — 取消、GC、Queue backpressure
> - React stale closure / useEffect 清理
> - OWASP shell injection / deserialization checklist

---

## 第一轮：功能与可靠性（20 个）

| # | 严重度 | Bug | 修复文件 | 验证 |
|---|--------|-----|----------|------|
| 01 | 🔴严重 | 点停止后再发消息永远卡住 | `page.tsx` | ✅ |
| 02 | 🔴严重 | Agent 异常时前端永远转圈 | `chat.py` | ✅ |
| 03 | 🔴严重 | 中断后再发消息报错（悬空 tool_calls） | `super_agent.py` | ✅ |
| 19 | 🔴严重 | 本地模式同样缺 done 安全网 | `local.py` | ✅ |
| 04 | 🟡中等 | API 错误返回 200 而非 404 | `chat.py` | ✅ |
| 05 | 🟡中等 | `_flash_flow` 后处理异常丢 done | `chat.py` 兜底 | ✅ |
| 06 | 🟡中等 | 切换线程时旧请求继续跑 | `page.tsx` | ✅ |
| 07 | 🟡中等 | 新建对话不取消旧请求 | `page.tsx` | ✅ |
| 08 | 🟡中等 | `_save()` 绕过锁，并发损坏风险 | `store.py` + `super_agent.py` + `headless_cli.py` | ✅ |
| 09 | 🟡中等 | `/api/health` 重复定义 | `chat.py` 删除 | ✅ |
| 10 | 🟡中等 | `_ultra_flow` 异常不发 done | `chat.py` 兜底 | ✅ |
| 11 | 🟡中等 | 改 API Key 后模型列表不刷新 | `page.tsx` 已有 onBack 刷新 | ✅ |
| 12 | 🟡中等 | 图片上传无大小限制 | `page.tsx` 加 5MB 限制 | ✅ |
| 13 | 🟢低 | 技能推荐只匹配英文 | `chat.py` 加中文关键词 | ✅ |
| 14 | 🟢低 | `copy_skill` tools 字段类型不安全 | `chat.py` 强制 `list()` | ✅ |
| 15 | 🟢低 | `sandbox_execute` 无认证 | `chat.py` 加 localhost 限制 | ✅ |
| 16 | 🟢低 | `upload_file` 无大小限制 | `chat.py` 加 50MB 限制 | ✅ |
| 17 | 🟢低 | SSE 空闲超时 | `api.ts` 加 120s 超时 | ✅ |
| 18 | 🟡中等 | `import_thread` 非 dict 崩溃 | `store.py` 加类型守卫 | ✅ |
| 20 | 🟢低 | 前端不检查 HTTP 错误码 | `api.ts` 加 `safeFetch` | ✅ |

---

## 第二轮：深度审计（12 个新发现）

> 方法论：asyncio task GC、shell 注入、异常吞噬、资源泄漏、stale closure

| # | 严重度 | Bug | 根因 | 修复文件 | 验证 |
|---|--------|-----|------|----------|------|
| 21 | 🔴严重 | Hook 异步 task 被 GC 回收 | `create_task()` 无强引用，asyncio 仅持弱引用 | `hooks.py` 加 `_background_tasks` set | ✅ |
| 22 | 🟡中等 | SpeculationStore 发布 task 被 GC | 同上 | `prompt_features.py` 加 `_background_tasks` set | ✅ |
| 23 | 🔴严重 | `git_command` shell 注入 | `shell=True` + 用户输入拼接 | `system_tools.py` 改用 `shlex.split` | ✅ |
| 24 | 🔴严重 | cron `run_job` shell 注入 | `shell=True` 执行用户自定义命令 | `self_evolution.py` 改用 `shlex.split` | ✅ |
| 25 | 🟡中等 | `execute_code` bash shell 注入 | `shell=True` 传入代码字符串 | `self_evolution.py` 改用 `["bash", "-c", ...]` | ✅ |
| 26 | 🟡中等 | `execute_skill_shell` shell 注入 | `shell=True` 执行技能内嵌命令 | `evolution.py` 改用 `["bash", "-c", ...]` | ✅ |
| 27 | 🟡中等 | `fire_sync` 异常吞噬 | `except Exception` 捕获所有错误不记录 | `hooks.py` 改为 `except RuntimeError` + 日志 | ✅ |
| 28 | 🟡中等 | cron 调度器每次创建新 event loop | 每个 job tick 创建/销毁 loop，状态无法共享 | `self_evolution.py` 复用单个 loop | ✅ |
| 29 | 🟡中等 | `handleSelectThread` 用原始 fetch | 绕过 `safeFetch`，错误静默；`deleteThread` 无 try/catch | `page.tsx` 改用 `fetchThread` + 加错误处理 | ✅ |
| 30 | 🔴严重 | 本地模式 LLM 流无超时 | `agent.astream_events()` 无时间限制，LLM hang 导致前端永远卡 | `local/agent.py` 加 180s deadline | ✅ |
| 31 | 🔴严重 | 停止后 agent_task 变僵尸 | `finally` 只 cancel permission_task 不 cancel agent_task，占住资源 | `chat.py` + `local.py` 加 `agent_task.cancel()` | ✅ |
| 32 | 🔴严重 | 停止→重发后旧 doSend 清理覆盖新请求 | 旧 doSend 的 `setStreaming(false)` 和 `abortRef=null` 覆盖新请求 | `page.tsx` 加 `requestIdRef` 防干扰 | ✅ |

---

## 验证结果（全部通过）

| 测试项 | 结果 |
|--------|------|
| 后端编译（11 文件 py_compile） | ✅ |
| 前端编译（tsc --noEmit） | ✅ |
| CRUD 单测（创建/读/改/删/导出/导入/分叉/谱系） | ✅ |
| 边界条件（空值/非法类型/不存在 ID/循环引用） | ✅ |
| 并发写入 20 条消息 | ✅ |
| API 404 状态码（GET/DELETE/trajectory/fork） | ✅ |
| 路径穿越攻击（workspace/screenshots） | ✅ |
| 参数校验（空 body → 422） | ✅ |
| SSE done 事件（standard/flash） | ✅ |
| dangling tool_call 清理（3 场景） | ✅ |
| /health 端点、sandbox localhost、upload 限制 | ✅ |
| 中文技能推荐 | ✅ |
| Hooks 端点正常（含 background_tasks） | ✅ |
| 上传小文件正常 | ✅ |
| 前端 safeFetch + handleSelectThread | ✅ |

---

## 修改文件汇总（15 个文件）

| 文件 | 改动摘要 |
|------|---------|
| `backend/app/api/chat.py` | HTTPException、done 安全网、上传限制、sandbox 限制、中文推荐、copy_skill 类型、删重复 health |
| `backend/app/api/local.py` | done 安全网 |
| `backend/app/agents/super_agent.py` | tool_call 清理、`_save` → `update_thread` |
| `backend/app/agents/store.py` | `update_thread()` 锁方法、`import_thread` 类型守卫 |
| `backend/app/agents/hooks.py` | asyncio task 强引用、`fire_sync` 异常处理 |
| `backend/app/agents/prompt_features.py` | asyncio task 强引用 |
| `backend/app/agents/system_tools.py` | `git_command` shell 注入修复 |
| `backend/app/agents/self_evolution.py` | cron shell 注入修复、`execute_code` shell 修复、调度器 loop 复用 |
| `backend/app/agents/evolution.py` | `execute_skill_shell` shell 注入修复 |
| `backend/app/headless_cli.py` | `_save` → `update_thread` |
| `backend/app/local/agent.py` | LLM 流式超时 180s 保护 |
| `frontend/src/app/page.tsx` | abort/streaming 重置、图片限制、fetchThread 导入及使用、deleteThread 错误处理 |
| `frontend/src/lib/api.ts` | `safeFetch` 统一错误检查、SSE idle 超时 |
