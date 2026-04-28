"""Token Budget Control — toggles, presets, daily budget, history."""
import os
import threading
from fastapi import APIRouter
from pydantic import BaseModel
from app.config import settings

router = APIRouter(prefix="/api", tags=["Token Budget"])

_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
_env_lock = threading.Lock()

# Token-consuming features: id -> (env_key, settings_attr, name, description, est_tokens_per_use)
_TOKEN_FEATURES = {
    "speculation": ("ENABLE_SPECULATION", "enable_speculation", "推测执行引擎",
                     "Agent 在隔离分支生成代码并自动验证", 15000),
    "sub_agents": ("ENABLE_SUB_AGENTS", "enable_sub_agents", "多 Agent 协作",
                    "派生子 Agent 并行完成研究/编码/测试", 60000),
    "gepa_evolution": ("ENABLE_GEPA_EVOLUTION", "enable_gepa_evolution", "GEPA 自进化",
                        "对 prompt/skill 做遗传进化优化", 100000),
    "memory_extraction": ("ENABLE_MEMORY_EXTRACTION", "enable_memory_extraction", "自动记忆提取",
                           "从对话中自动提取偏好和知识", 5000),
    "agent_summaries": ("ENABLE_AGENT_SUMMARIES", "enable_agent_summaries", "对话摘要",
                         "每轮对话后自动生成摘要", 3000),
    "tool_use_summary": ("ENABLE_TOOL_USE_SUMMARY", "enable_tool_use_summary", "工具调用摘要",
                          "总结工具使用过程和结果", 2000),
    "prompt_suggestions": ("ENABLE_PROMPT_SUGGESTIONS", "enable_prompt_suggestions", "智能提示建议",
                            "根据上下文推荐后续提问", 2000),
    "intent_classify": ("ENABLE_INTENT_CLASSIFY", "enable_intent_classify", "意图分类",
                         "用 LLM 对模糊意图做二次分类", 1500),
    "magic_docs": ("ENABLE_MAGIC_DOCS", "enable_magic_docs", "智能文档",
                    "自动生成和更新项目文档", 8000),
    "reflection": ("ENABLE_REFLECTION", "enable_reflection", "自我反思纠错",
                    "Agent 输出后自动评审并修正错误（仅 Pro/Ultra）", 10000),
}

# Preset modes
_PRESETS = {
    "minimal": {
        "speculation": False, "sub_agents": False, "gepa_evolution": False,
        "memory_extraction": False, "agent_summaries": False, "tool_use_summary": False,
        "prompt_suggestions": False, "intent_classify": False, "magic_docs": False, "reflection": False,
    },
    "standard": {
        "speculation": True, "sub_agents": False, "gepa_evolution": False,
        "memory_extraction": True, "agent_summaries": True, "tool_use_summary": False,
        "prompt_suggestions": True, "intent_classify": True, "magic_docs": False, "reflection": False,
    },
    "full": {
        "speculation": True, "sub_agents": True, "gepa_evolution": True,
        "memory_extraction": True, "agent_summaries": True, "tool_use_summary": True,
        "prompt_suggestions": True, "intent_classify": True, "magic_docs": True, "reflection": True,
    },
}

_PRESET_INFO = {
    "minimal": {"name": "极省模式", "description": "仅保留基础对话，关闭所有附加 LLM 调用", "est_per_chat": "~3K"},
    "standard": {"name": "标准模式", "description": "开启推测、记忆、提示建议等常用功能", "est_per_chat": "~30K"},
    "full": {"name": "全功能模式", "description": "所有 AI 能力全开，包括多 Agent 和自进化", "est_per_chat": "~197K"},
}


def _read_env_lines() -> list[str]:
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, "r") as f:
            return f.readlines()
    return []


def _write_env_key(key: str, value: str):
    """Write or update a key in .env file. Thread-safe."""
    with _env_lock:
        lines = _read_env_lines()
        found = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
                new_lines.append(f"{key}={value}\n")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}\n")
        with open(_ENV_PATH, "w") as f:
            f.writelines(new_lines)


class TokenToggleRequest(BaseModel):
    feature_id: str
    enable: bool


class DailyBudgetRequest(BaseModel):
    daily_token_budget: int  # 0 = unlimited


# ── Endpoints ──────────────────────────────────────────────

@router.get("/features/token-budget")
async def get_token_budget():
    """Return all token-consuming features with their toggle status and estimated costs."""
    items = []
    for fid, (env_key, attr, name, desc, est_tokens) in _TOKEN_FEATURES.items():
        enabled = getattr(settings, attr, True)
        items.append({
            "id": fid,
            "name": name,
            "description": desc,
            "enabled": enabled,
            "est_tokens_per_use": est_tokens,
        })
    total_if_all = sum(i["est_tokens_per_use"] for i in items)
    active_total = sum(i["est_tokens_per_use"] for i in items if i["enabled"])
    return {
        "features": items,
        "total_est_tokens": total_if_all,
        "active_est_tokens": active_total,
        "saved_tokens": total_if_all - active_total,
    }


@router.post("/features/token-budget/toggle")
async def toggle_token_feature(req: TokenToggleRequest):
    """Toggle a token-consuming feature on/off."""
    spec = _TOKEN_FEATURES.get(req.feature_id)
    if not spec:
        return {"success": False, "error": f"Unknown feature '{req.feature_id}'"}
    env_key, attr, name, _desc, est = spec
    new_val = "true" if req.enable else "false"
    _write_env_key(env_key, new_val)
    setattr(settings, attr, req.enable)
    action = "已开启" if req.enable else "已关闭"
    return {
        "success": True,
        "message": f"{name} {action}，每次约{'消耗' if req.enable else '节省'} {est:,} tokens",
        "feature_id": req.feature_id,
        "enabled": req.enable,
    }


@router.get("/features/token-budget/presets")
async def get_presets():
    """Return available preset modes."""
    current = {}
    for fid, (_, attr, *_rest) in _TOKEN_FEATURES.items():
        current[fid] = getattr(settings, attr, True)
    active_preset = None
    for preset_key, preset_vals in _PRESETS.items():
        if all(current.get(k) == v for k, v in preset_vals.items()):
            active_preset = preset_key
            break
    return {
        "presets": {k: {**_PRESET_INFO[k], "features": v} for k, v in _PRESETS.items()},
        "active_preset": active_preset,
    }


@router.post("/features/token-budget/preset/{preset_name}")
async def apply_preset(preset_name: str):
    """Apply a preset mode — batch toggle all features."""
    preset = _PRESETS.get(preset_name)
    if not preset:
        return {"success": False, "error": f"Unknown preset '{preset_name}'"}
    for fid, enabled in preset.items():
        spec = _TOKEN_FEATURES.get(fid)
        if not spec:
            continue
        env_key, attr, *_ = spec
        _write_env_key(env_key, "true" if enabled else "false")
        setattr(settings, attr, enabled)
    info = _PRESET_INFO[preset_name]
    return {"success": True, "message": f"已切换到{info['name']}，预计每次对话 {info['est_per_chat']} tokens", "preset": preset_name}


@router.get("/features/token-budget/daily")
async def get_daily_usage():
    """Return today's token usage vs budget limit."""
    from app.agents.cost_tracker import cost_tracker
    daily = cost_tracker.get_daily_summary()
    budget = settings.daily_token_budget
    today_tokens = daily.get("total_input_tokens", 0) + daily.get("total_output_tokens", 0)
    return {
        "date": daily.get("date"),
        "requests": daily.get("requests", 0),
        "input_tokens": daily.get("total_input_tokens", 0),
        "output_tokens": daily.get("total_output_tokens", 0),
        "total_tokens": today_tokens,
        "cost_usd": daily.get("total_cost_usd", 0),
        "budget": budget,
        "budget_used_pct": round(today_tokens / budget * 100, 1) if budget > 0 else 0,
        "is_over_budget": today_tokens >= budget > 0,
        "remaining": max(0, budget - today_tokens) if budget > 0 else -1,
    }


@router.post("/features/token-budget/daily")
async def set_daily_budget(req: DailyBudgetRequest):
    """Set daily token budget. 0 = unlimited."""
    settings.daily_token_budget = req.daily_token_budget
    _write_env_key("DAILY_TOKEN_BUDGET", str(req.daily_token_budget))
    if req.daily_token_budget == 0:
        return {"success": True, "message": "每日预算已取消限制"}
    return {"success": True, "message": f"每日预算已设为 {req.daily_token_budget:,} tokens"}


@router.get("/features/token-budget/history")
async def get_token_history(days: int = 7):
    """Return per-day token usage for the last N days."""
    from app.agents.cost_tracker import cost_tracker
    from datetime import date, timedelta
    logs = cost_tracker._load_logs()
    day_map: dict[str, dict] = {}
    for i in range(days):
        d = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        day_map[d] = {"date": d, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "requests": 0}
    for log in logs:
        ts = log.get("timestamp", "")[:10]
        if ts in day_map:
            day_map[ts]["input_tokens"] += log.get("input_tokens", 0)
            day_map[ts]["output_tokens"] += log.get("output_tokens", 0)
            day_map[ts]["cost_usd"] += log.get("cost_usd", 0)
            day_map[ts]["requests"] += 1
    for d in day_map.values():
        d["total_tokens"] = d["input_tokens"] + d["output_tokens"]
        d["cost_usd"] = round(d["cost_usd"], 4)
    return {"days": list(day_map.values())}


@router.get("/features/token-budget/model-breakdown")
async def get_model_breakdown():
    """Return cost breakdown by model."""
    from app.agents.cost_tracker import cost_tracker
    logs = cost_tracker._load_logs()
    session_records = [r.to_dict() for r in cost_tracker._session_records]
    all_records = logs + session_records

    model_data: dict[str, dict] = {}
    for rec in all_records:
        model = rec.get("model", "unknown") or "unknown"
        if model not in model_data:
            model_data[model] = {"model": model, "requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
        model_data[model]["requests"] += 1
        model_data[model]["input_tokens"] += rec.get("input_tokens", 0)
        model_data[model]["output_tokens"] += rec.get("output_tokens", 0)
        model_data[model]["cost_usd"] += rec.get("cost_usd", 0)
    for d in model_data.values():
        d["total_tokens"] = d["input_tokens"] + d["output_tokens"]
        d["cost_usd"] = round(d["cost_usd"], 4)

    rows = sorted(model_data.values(), key=lambda x: x["cost_usd"], reverse=True)
    return {"models": rows}


@router.get("/features/token-budget/cache-stats")
async def get_cache_stats():
    """Return prompt cache hit stats from session and persisted logs."""
    from app.agents.cost_tracker import cost_tracker
    logs = cost_tracker._load_logs()
    session_records = [r.to_dict() for r in cost_tracker._session_records]
    all_records = logs + session_records

    total_input = 0
    total_cache_creation = 0
    total_cache_read = 0
    total_cost = 0.0
    records_with_cache = 0

    for rec in all_records:
        inp = rec.get("input_tokens", 0)
        cc = rec.get("cache_creation_tokens", 0)
        cr = rec.get("cache_read_tokens", 0)
        total_input += inp
        total_cache_creation += cc
        total_cache_read += cr
        total_cost += rec.get("cost_usd", 0)
        if cc or cr:
            records_with_cache += 1

    total_cache = total_cache_creation + total_cache_read
    # Estimate savings: cache_read tokens cost ~1/10 of normal input
    # So savings = cache_read_tokens * (normal_price - cache_price) / 1M
    # Use default pricing for estimation
    from app.agents.cost_tracker import MODEL_PRICING
    default_pricing = MODEL_PRICING["default"]
    normal_input_price = default_pricing["input"]
    cache_read_price = default_pricing.get("cache_read", normal_input_price * 0.1)
    saved_usd = (total_cache_read / 1_000_000) * (normal_input_price - cache_read_price)

    hit_rate = round(total_cache_read / total_input * 100, 1) if total_input > 0 else 0

    return {
        "total_input_tokens": total_input,
        "cache_creation_tokens": total_cache_creation,
        "cache_read_tokens": total_cache_read,
        "total_cache_tokens": total_cache,
        "cache_hit_rate": hit_rate,
        "saved_usd": round(saved_usd, 4),
        "total_cost_usd": round(total_cost, 4),
        "records_with_cache": records_with_cache,
        "total_records": len(all_records),
    }


@router.get("/features/token-budget/export")
async def export_monthly_csv(month: str = ""):
    """Export monthly token usage as CSV. month format: YYYY-MM, defaults to current."""
    from app.agents.cost_tracker import cost_tracker
    from datetime import date, timedelta
    from fastapi.responses import StreamingResponse
    import io, csv

    if not month:
        month = date.today().strftime("%Y-%m")

    logs = cost_tracker._load_logs()
    # Filter logs for the given month
    month_logs = [l for l in logs if l.get("timestamp", "")[:7] == month]

    # Aggregate by day
    day_data: dict[str, dict] = {}
    for log in month_logs:
        day = log.get("timestamp", "")[:10]
        if day not in day_data:
            day_data[day] = {"date": day, "requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
        day_data[day]["requests"] += 1
        day_data[day]["input_tokens"] += log.get("input_tokens", 0)
        day_data[day]["output_tokens"] += log.get("output_tokens", 0)
        day_data[day]["cost_usd"] += log.get("cost_usd", 0)
    for d in day_data.values():
        d["total_tokens"] = d["input_tokens"] + d["output_tokens"]
        d["cost_usd"] = round(d["cost_usd"], 4)

    rows = sorted(day_data.values(), key=lambda x: x["date"])

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["日期", "请求次数", "输入 Tokens", "输出 Tokens", "合计 Tokens", "费用 (USD)"])
    for r in rows:
        writer.writerow([r["date"], r["requests"], r["input_tokens"], r["output_tokens"], r["total_tokens"], r["cost_usd"]])
    # Summary row
    writer.writerow([])
    writer.writerow(["合计", sum(r["requests"] for r in rows), sum(r["input_tokens"] for r in rows),
                     sum(r["output_tokens"] for r in rows), sum(r["total_tokens"] for r in rows),
                     round(sum(r["cost_usd"] for r in rows), 4)])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=token_report_{month}.csv"},
    )
