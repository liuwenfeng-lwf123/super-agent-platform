"""Self-evolution, task evals, GEPA, plugins, cron, elicitation, soul routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter()


# --- Self-Evolution (Hermes-inspired) ---
@router.get("/evolution/stats")
async def evolution_stats():
    from app.agents.self_evolution import evolution_controller
    return evolution_controller.get_stats()


@router.get("/evolution/history")
async def evolution_history(limit: int = 20):
    from app.agents.self_evolution import evolution_controller
    history = evolution_controller.get_history(limit)
    return {"history": history, "log": history}


@router.get("/evolution/log")
async def evolution_log(limit: int = 20):
    from app.agents.self_evolution import evolution_controller
    history = evolution_controller.get_history(limit)
    return {"history": history, "log": history}


@router.get("/evolution/tools")
async def evolution_tools():
    from app.agents.tools import get_all_tools

    def fallback_tool_label(name: str) -> str:
        dictionary = {
            "local": "本地",
            "web": "网页",
            "execute": "运行",
            "read": "读取",
            "write": "写入",
            "list": "查看",
            "remove": "删除",
            "create": "创建",
            "edit": "编辑",
            "patch": "修改",
            "rollback": "回滚",
            "record": "记录",
            "view": "查看",
            "score": "评估",
            "skill": "技能",
            "tool": "工具",
            "file": "文件",
            "files": "文件",
            "feedback": "反馈",
            "history": "历史",
            "evolve": "优化",
            "custom": "自定义",
            "search": "搜索",
            "fetch": "读取",
            "bash": "终端命令",
            "python": "Python",
            "javascript": "JavaScript",
            "url": "网页链接",
            "current": "当前",
            "time": "时间",
            "memory": "记忆",
            "discovered": "发现的",
            "gepa": "自动提示词优化",
            "editor": "编辑器",
            "diagnostics": "诊断",
            "screenshot": "截屏",
            "clipboard": "剪贴板",
            "system": "系统",
            "info": "信息",
            "open": "打开",
            "app": "应用",
            "browser": "浏览器",
            "state": "状态",
            "run": "执行",
            "click": "点击",
            "fill": "填写",
            "extract": "提取",
            "text": "文本",
            "notify": "通知",
            "git": "Git",
            "command": "命令",
            "http": "HTTP",
            "request": "请求",
            "pdf": "PDF",
            "knowledge": "知识库",
            "session": "会话",
            "spawn": "启动",
            "agent": "智能体",
            "send": "发送",
            "message": "消息",
            "register": "注册",
            "hook": "钩子",
            "elicit": "询问",
            "input": "输入",
            "goto": "跳转",
            "definition": "定义",
            "find": "查找",
            "references": "引用",
            "document": "文档",
            "symbols": "符号",
            "call": "调用",
            "hierarchy": "层级",
            "ip": "IP",
        }
        translated = [dictionary.get(part, "") for part in name.split("_")]
        translated = [part for part in translated if part]
        return "".join(translated) if translated else "高级工具"

    def fallback_tool_summary(name: str, category: str) -> str:
        label = fallback_tool_label(name)
        if "skill" in name:
            return f"用于管理或优化技能：{label}。"
        if "file" in name:
            return f"用于查看、读取或修改文件：{label}。"
        if "web" in name:
            return f"用于联网搜索或读取网页内容：{label}。"
        if "local" in name:
            return f"用于操作本地电脑资源：{label}。"
        if "execute" in name:
            return f"用于运行代码或命令：{label}。"
        return f"用于扩展 AI 能力：{label}。"

    def classify_tool(name: str) -> dict:
        display_names = {
            "web_search": "网页搜索",
            "web_fetch": "读取网页",
            "summarize_url": "总结网页",
            "read_file": "读取文件",
            "list_files": "列出文件",
            "write_file": "写入文件",
            "file_history": "查看文件历史",
            "get_editor_state": "查看编辑器状态",
            "get_editor_diagnostics": "查看代码诊断",
            "execute_python": "运行 Python",
            "execute_javascript": "运行 JavaScript",
            "execute_bash": "运行终端命令",
            "execute_code": "执行工具脚本",
            "execute_tool_chain": "批量执行工具",
            "calculate": "计算",
            "get_current_time": "获取时间",
            "remember": "保存记忆",
            "create_skill": "创建技能",
            "patch_skill": "修改技能片段",
            "edit_skill": "完整编辑技能",
            "rollback_skill": "回滚技能",
            "list_custom_skills": "查看自定义技能",
            "view_evolution_log": "查看进化记录",
            "view_skill": "查看技能详情",
            "score_skill": "评估技能效果",
            "write_skill_file": "保存技能文件",
            "remove_skill_file": "删除技能文件",
            "record_skill_feedback": "记录技能反馈",
            "gepa_evolve": "自动优化技能",
            "create_tool": "创建工具",
            "list_custom_tools": "查看自定义工具",
            "remove_custom_tool": "删除自定义工具",
            "tool_search": "查找工具",
            "run_discovered_tool": "运行发现的工具",
            "screenshot": "截取屏幕",
            "clipboard_read": "读取剪贴板",
            "clipboard_write": "写入剪贴板",
            "system_info": "查看系统信息",
            "open_app": "打开应用",
            "open_url": "打开网页链接",
            "browser_open": "打开浏览器页面",
            "browser_get_state": "查看浏览器状态",
            "browser_run_javascript": "浏览器执行脚本",
            "browser_click": "点击网页元素",
            "browser_fill": "填写网页表单",
            "browser_extract_text": "提取网页文本",
            "notify": "发送系统通知",
            "git_command": "执行 Git 命令",
            "http_request": "发送网络请求",
            "pdf_extract": "读取 PDF 文本",
            "knowledge_search": "搜索知识库",
            "session_search": "搜索历史会话",
            "spawn_agent": "启动子智能体",
            "send_agent_message": "发送子智能体消息",
            "register_hook": "注册自动钩子",
            "execute_code_tool": "执行代码工具",
            "elicit_input": "向用户提问",
            "goto_definition": "跳转代码定义",
            "find_references": "查找代码引用",
            "document_symbols": "查看文件符号",
            "call_hierarchy": "查看调用层级",
            "get_ip": "查询公网 IP",
            "local_read_file": "读取本地文件",
            "local_write_file": "写入本地文件",
            "local_list_files": "查看本地文件",
            "local_execute_bash": "本地终端命令",
            "local_execute_python": "本地 Python",
            "local_open_app": "打开本地应用",
            "local_get_system_info": "本地系统信息",
        }
        summaries = {
            "web_search": "联网搜索新闻、资料、文档和实时信息。",
            "web_fetch": "读取指定网页正文，适合打开搜索结果或文档链接。",
            "summarize_url": "抓取网页并整理成摘要。",
            "read_file": "读取项目工作区里的文件内容。",
            "list_files": "查看项目目录结构和文件列表。",
            "write_file": "创建或修改项目工作区文件。",
            "file_history": "查看当前对话里文件被创建、修改或操作的记录。",
            "get_editor_state": "读取当前 IDE 打开的文件、光标位置、选区和编辑器上下文。",
            "get_editor_diagnostics": "读取当前代码文件里的错误、警告和诊断信息。",
            "execute_python": "运行 Python 代码，适合计算、数据处理和验证脚本。",
            "execute_javascript": "运行 JavaScript 代码。",
            "execute_bash": "运行终端命令，适合测试、启动服务和查看环境。",
            "execute_code": "执行一段工具脚本，用于把多个工具操作串起来。",
            "execute_tool_chain": "用脚本连续调用多个工具，减少重复来回调用。",
            "calculate": "执行简单数学计算。",
            "get_current_time": "获取当前日期和时间。",
            "remember": "把重要信息写入长期记忆。",
            "create_skill": "创建一套新的 AI 工作方式。",
            "patch_skill": "替换技能提示词中的一小段内容。",
            "edit_skill": "完整改写某个技能的提示词。",
            "rollback_skill": "把技能恢复到上一个版本。",
            "list_custom_skills": "查看已经创建的自定义技能。",
            "view_evolution_log": "查看技能和工具的进化历史。",
            "view_skill": "查看某个技能的提示词、版本、启用状态和关联工具。",
            "score_skill": "根据最近表现给技能打分，判断它是否好用。",
            "write_skill_file": "把技能内容保存成文件，用于创建或更新技能。",
            "remove_skill_file": "删除某个技能对应的文件。",
            "record_skill_feedback": "记录用户对技能效果的反馈，供后续优化使用。",
            "gepa_evolve": "自动分析反馈并尝试优化技能提示词。",
            "create_tool": "创建一个新的可复用工具。",
            "list_custom_tools": "查看已经创建的自定义工具。",
            "remove_custom_tool": "删除某个自定义工具。",
            "tool_search": "在大量工具中搜索合适能力。",
            "run_discovered_tool": "运行通过工具搜索发现的能力。",
            "screenshot": "截取当前屏幕，用于查看界面状态或排查 UI 问题。",
            "clipboard_read": "读取系统剪贴板里的文本内容。",
            "clipboard_write": "把指定内容写入系统剪贴板。",
            "system_info": "查看系统、CPU、内存、磁盘等基础信息。",
            "open_app": "打开电脑上的应用程序。",
            "open_url": "用浏览器打开指定网页链接。",
            "browser_open": "打开或跳转到一个浏览器页面。",
            "browser_get_state": "查看当前浏览器页面、地址、标题和可操作元素。",
            "browser_run_javascript": "在当前网页里执行 JavaScript 脚本。",
            "browser_click": "点击网页上的按钮、链接或其他元素。",
            "browser_fill": "填写网页表单输入框。",
            "browser_extract_text": "提取当前网页上的可见文字。",
            "notify": "发送一条系统桌面通知。",
            "git_command": "执行 Git 状态、提交、分支等版本控制命令。",
            "http_request": "向指定接口发送 GET、POST 等网络请求。",
            "pdf_extract": "从 PDF 文件中提取文本内容。",
            "knowledge_search": "在项目知识库或文档库中搜索相关内容。",
            "session_search": "在历史会话记录中搜索相关上下文。",
            "spawn_agent": "启动一个子智能体去并行处理子任务。",
            "send_agent_message": "向已经启动的子智能体发送后续消息。",
            "register_hook": "注册自动化钩子，让系统在特定事件发生时执行动作。",
            "execute_code_tool": "运行代码型工具，用于执行更复杂的工具逻辑。",
            "elicit_input": "向用户发起一个明确问题，请用户补充信息。",
            "goto_definition": "在代码中跳转到函数、变量或类型的定义位置。",
            "find_references": "查找某个函数、变量或类型在代码里的引用位置。",
            "document_symbols": "列出当前代码文件里的函数、类、变量等结构。",
            "call_hierarchy": "查看函数或方法之间的调用关系。",
            "get_ip": "查询当前网络的公网 IP 地址。",
            "local_read_file": "读取你电脑上的本地文件。",
            "local_write_file": "创建或修改你电脑上的本地文件。",
            "local_list_files": "浏览你电脑上的目录。",
            "local_execute_bash": "在你电脑上执行终端命令。",
            "local_execute_python": "在你电脑上运行 Python。",
            "local_open_app": "打开你电脑上的应用。",
            "local_get_system_info": "查看你电脑的系统信息。",
        }
        if name.startswith("local_"):
            category = "local"
        elif name.startswith("web_") or name in {"summarize_url"}:
            category = "search"
        elif "file" in name or name in {"read_file", "write_file", "list_files"}:
            category = "file"
        elif name.startswith("execute_") or name.endswith("_command"):
            category = "execution"
        elif "memory" in name or name == "remember":
            category = "memory"
        elif "skill" in name or "evolution" in name or "tool" in name:
            category = "evolution"
        else:
            category = "utility"
        if name.startswith("local_") or name in {"execute_bash", "write_file", "run_discovered_tool"}:
            risk = "high"
        elif name.startswith("execute_") or name in {"read_file", "web_fetch"}:
            risk = "medium"
        else:
            risk = "low"
        return {
            "display_name": display_names.get(name, fallback_tool_label(name)),
            "summary": summaries.get(name, fallback_tool_summary(name, category)),
            "category": category,
            "risk": risk,
        }

    tools = []
    for tool in get_all_tools(include_deferred=True, enable_tool_search=True, wrap=False):
        name = getattr(tool, "name", "unknown")
        meta = classify_tool(name)
        tools.append({
            "name": name,
            **meta,
            "description": getattr(tool, "description", ""),
            "built_in": not str(name).startswith("custom_"),
        })
    return {"tools": tools}


@router.get("/evolution/triage")
async def evolution_triage():
    from app.agents.self_evolution import evolution_controller
    return evolution_controller.auto_triage()


@router.post("/evolution/evolve")
async def evolve_skill(payload: dict):
    from app.agents.self_evolution import evolution_controller
    skill_name = payload.get("skill_name", "")
    skill_content = payload.get("skill_content", "")
    iterations = payload.get("iterations", 5)
    if not skill_name or not skill_content:
        raise HTTPException(status_code=400, detail="skill_name and skill_content required")
    result = evolution_controller.evolve_skill(skill_name, skill_content, iterations)
    return result


@router.post("/evolution/apply-candidate")
async def apply_evolution_candidate(payload: dict):
    import os
    import json
    from pathlib import Path
    from app.agents.evolution import skill_registry
    from app.agents.self_evolution import CANDIDATES_DIR

    skill_name = payload.get("skill_name", "")
    if not skill_name:
        raise HTTPException(status_code=400, detail="skill_name required")
    if not skill_registry.get_skill(skill_name):
        raise HTTPException(status_code=400, detail="Only custom skills can apply evolved candidates")
    candidate_path = os.path.join(CANDIDATES_DIR, f"{skill_name}_evolved.json")
    if not os.path.exists(candidate_path):
        raise HTTPException(status_code=404, detail="No evolved candidate found for this skill")
    candidate = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    evolved = candidate.get("evolved", "")
    if not evolved.strip():
        raise HTTPException(status_code=400, detail="Candidate has no evolved content")
    ok, message = skill_registry.edit_skill(skill_name, evolved)
    return {"ok": ok, "message": message, "candidate": candidate}


@router.get("/sessions/search")
async def search_sessions(query: str | None = None, q: str | None = None,
                          limit: int = 20, thread_id: str | None = None):
    """Full-text search over past conversations. Accepts either `query` or `q`."""
    from app.agents.learning_loop import session_search_db
    qstr = query or q or ""
    results = session_search_db.search(qstr, limit=limit, thread_id=thread_id)
    return {"query": qstr, "count": len(results), "results": results}


@router.get("/sessions/stats")
async def session_stats():
    from app.agents.learning_loop import session_search_db
    return session_search_db.get_stats()


@router.get("/nudge/state")
async def nudge_state():
    from app.agents.learning_loop import nudge_manager
    return nudge_manager.get_state()


@router.post("/evolution/trace")
async def record_trace(payload: dict):
    from app.agents.self_evolution import evolution_controller, TraceEntry
    from datetime import datetime
    trace = TraceEntry(
        timestamp=payload.get("timestamp", datetime.now().isoformat()),
        thread_id=payload.get("thread_id", ""),
        skill_name=payload.get("skill_name", "_default"),
        user_input=payload.get("user_input", ""),
        agent_output=payload.get("agent_output", ""),
        tool_calls=payload.get("tool_calls", []),
        cost_usd=payload.get("cost_usd", 0),
        success=payload.get("success"),
        user_feedback=payload.get("user_feedback"),
        score=payload.get("score"),
    )
    evolution_controller.trace_collector.record(trace)
    return {"status": "recorded"}


@router.post("/evolution/gepa")
async def run_gepa_evolution(payload: dict):
    from app.agents.self_evolution import gepa_engine
    result = gepa_engine.evolve(
        payload.get("original", ""),
        payload.get("eval_cases", []),
        payload.get("population_size", 8),
        payload.get("generations", 5),
        payload.get("failure_examples"),
    )
    best = result.get("best", {})
    return {
        "baseline_score": result.get("baseline_score"),
        "best_score": result.get("best_score"),
        "improvement": result.get("improvement"),
        "pareto_front_size": result.get("pareto_front_size"),
        "generations": result.get("generations"),
        "best_content_preview": best.get("content", "")[:300],
    }


@router.post("/evolution/semantic-check")
async def semantic_check(payload: dict):
    from app.agents.self_evolution import SemanticPreservation
    passes, score = SemanticPreservation.check(
        payload.get("original", ""), payload.get("evolved", ""),
    )
    return {"passes": passes, "score": score}


# --- Plugins ---
@router.get("/plugins")
async def list_plugins():
    from app.agents.self_evolution import plugin_registry
    return {"plugins": plugin_registry.list_plugins()}


@router.post("/plugins/discover")
async def discover_plugins(payload: dict = None):
    from app.agents.self_evolution import plugin_registry
    project_dir = (payload or {}).get("project_dir", "")
    found = plugin_registry.discover(project_dir)
    return {"found": len(found), "plugins": found}


@router.post("/plugins/{name}/enable")
async def enable_plugin(name: str):
    from app.agents.self_evolution import plugin_registry
    ok, msg = plugin_registry.enable(name)
    return {"ok": ok, "message": msg}


@router.post("/plugins/{name}/disable")
async def disable_plugin(name: str):
    from app.agents.self_evolution import plugin_registry
    ok, msg = plugin_registry.disable(name)
    return {"ok": ok, "message": msg}


@router.post("/plugins/{name}/load")
async def load_plugin(name: str):
    from app.agents.self_evolution import plugin_registry
    ok, msg = plugin_registry.load_plugin(name)
    return {"ok": ok, "message": msg}


@router.post("/plugins/load-all")
async def load_all_plugins():
    from app.agents.self_evolution import plugin_registry
    loaded = plugin_registry.load_all()
    return {"loaded": loaded, "count": len(loaded)}


@router.post("/plugins/discover-pip")
async def discover_pip_plugins():
    from app.agents.self_evolution import plugin_registry
    found = plugin_registry.discover_pip_plugins()
    return {"found": len(found), "plugins": found}


# --- Cron ---
@router.get("/cron")
async def list_cron_jobs():
    from app.agents.self_evolution import cron_manager
    return {"jobs": cron_manager.list_jobs()}


@router.post("/cron")
async def add_cron_job(payload: dict):
    from app.agents.self_evolution import cron_manager
    ok, msg = cron_manager.add_job(
        payload.get("name", ""), payload.get("schedule", ""),
        payload.get("action", ""), **{k: v for k, v in payload.items() if k not in ("name", "schedule", "action")},
    )
    return {"ok": ok, "message": msg}


@router.delete("/cron/{name}")
async def remove_cron_job(name: str):
    from app.agents.self_evolution import cron_manager
    ok, msg = cron_manager.remove_job(name)
    return {"ok": ok, "message": msg}


@router.post("/cron/{name}/run")
async def run_cron_job(name: str):
    from app.agents.self_evolution import cron_manager
    result = await cron_manager.run_job(name)
    return result


@router.post("/cron/{name}/enable")
async def enable_cron_job(name: str):
    from app.agents.self_evolution import cron_manager
    ok, msg = cron_manager.enable_job(name)
    return {"ok": ok, "message": msg}


@router.post("/cron/{name}/disable")
async def disable_cron_job(name: str):
    from app.agents.self_evolution import cron_manager
    ok, msg = cron_manager.disable_job(name)
    return {"ok": ok, "message": msg}


@router.post("/cron/scheduler/start")
async def start_cron_scheduler():
    from app.agents.self_evolution import cron_manager
    ok = cron_manager.start_scheduler()
    return {"ok": ok, "message": "Cron scheduler started"}


@router.post("/cron/scheduler/stop")
async def stop_cron_scheduler():
    from app.agents.self_evolution import cron_manager
    ok = cron_manager.stop_scheduler()
    return {"ok": ok, "message": "Cron scheduler stopped"}


# --- Elicitation ---
@router.post("/elicitation/request")
async def create_elicitation(payload: dict):
    from app.agents.self_evolution import elicitation_manager
    from dataclasses import asdict
    req = elicitation_manager.create_request(
        payload.get("title", ""), payload.get("fields", []),
        payload.get("description", ""),
    )
    return asdict(req)


@router.post("/elicitation/{elicitation_id}/submit")
async def submit_elicitation(elicitation_id: str, payload: dict):
    from app.agents.self_evolution import elicitation_manager
    ok, msg = elicitation_manager.submit_result(elicitation_id, payload.get("values", {}))
    return {"ok": ok, "message": msg}


@router.get("/elicitation/pending")
async def pending_elicitations():
    from app.agents.self_evolution import elicitation_manager
    return {"pending": elicitation_manager.get_pending()}


@router.get("/elicitation/{elicitation_id}")
async def get_elicitation_result(elicitation_id: str):
    from app.agents.self_evolution import elicitation_manager
    result = elicitation_manager.get_result(elicitation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Elicitation not found or still pending")
    return result


# --- execute-code ---
@router.post("/execute-code")
async def api_execute_code(payload: dict):
    from app.agents.self_evolution import execute_code
    result = await execute_code(
        payload.get("code", ""), payload.get("language", "python"),
        payload.get("timeout", 30),
    )
    return result


# --- SOUL.md ---
@router.get("/soul")
async def get_soul():
    from app.agents.self_evolution import load_soul
    return {"content": load_soul()}


@router.put("/soul")
async def update_soul(payload: dict):
    from app.agents.self_evolution import save_soul
    ok, msg = save_soul(payload.get("content", ""))
    return {"ok": ok, "message": msg}


# --- Prompt Cache ---
@router.post("/prompt/cache-breakpoints")
async def api_cache_breakpoints(payload: dict):
    from app.agents.self_evolution import inject_cache_breakpoints
    blocks = inject_cache_breakpoints(payload.get("prompt", ""))
    return {"blocks": blocks}


# --- Session Search rebuild ---
@router.post("/sessions/search/rebuild")
async def session_search_rebuild():
    """Rebuild the FTS5 index from the JSON thread store."""
    from app.agents.learning_loop import session_search_db
    from app.agents.store import thread_store
    threads = list(thread_store._threads.values())
    count = session_search_db.rebuild_from_threads(threads)
    return {"indexed_messages": count, "threads": len(threads)}
