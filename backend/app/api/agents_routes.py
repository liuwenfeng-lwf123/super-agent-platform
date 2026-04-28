"""Hooks, subagents, providers, permissions, monitor, credentials routes."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


# ==============================================================
# Hooks API
# ==============================================================
@router.get("/hooks")
async def list_hooks():
    from app.agents.hooks import hooks_registry
    return {"hooks": hooks_registry.list_hooks()}


@router.post("/hooks/register")
async def register_hook(payload: dict):
    from app.agents.hooks import HookDefinition, HookHandler, HookMatcher, hooks_registry
    hook = HookDefinition(
        event=payload.get("event", ""),
        name=payload.get("name", ""),
        description=payload.get("description", ""),
        matchers=[HookMatcher(**m) for m in payload.get("matchers", [])],
        handlers=[HookHandler(**h) for h in payload.get("handlers", [])],
    )
    ok, msg = hooks_registry.register(hook)
    return {"ok": ok, "message": msg}


@router.delete("/hooks/{name}")
async def unregister_hook(name: str):
    from app.agents.hooks import hooks_registry
    ok, msg = hooks_registry.unregister(name)
    return {"ok": ok, "message": msg}


@router.post("/hooks/{name}/enable")
async def enable_hook(name: str):
    from app.agents.hooks import hooks_registry
    ok, msg = hooks_registry.enable(name)
    return {"ok": ok, "message": msg}


@router.post("/hooks/{name}/disable")
async def disable_hook(name: str):
    from app.agents.hooks import hooks_registry
    ok, msg = hooks_registry.disable(name)
    return {"ok": ok, "message": msg}


@router.post("/hooks/fire")
async def fire_hook(payload: dict):
    from app.agents.hooks import hooks_registry
    event = payload.get("event", "")
    context = payload.get("context", {})
    results = await hooks_registry.fire(event, context)
    return {"results": [{"hook": r.hook_name, "status": r.status, "decision": r.decision,
                         "output": r.output[:500]} for r in results]}


@router.get("/hooks/history")
async def hook_history(limit: int = 50):
    from app.agents.hooks import hooks_registry
    return {"history": hooks_registry.get_history(limit)}


# ==============================================================
# Subagents API (Hermes/Claude Code style — separate from /agents)
# ==============================================================
@router.get("/subagents")
async def list_subagents():
    from app.agents.subagents import subagent_manager
    return {"agents": subagent_manager.list_agents()}


@router.post("/subagents/create")
async def create_subagent(payload: dict):
    from app.agents.subagents import subagent_manager
    name = payload.pop("name", "")
    ok, msg = subagent_manager.create_agent(name, **payload)
    return {"ok": ok, "message": msg}


@router.delete("/subagents/{name}")
async def remove_subagent(name: str):
    from app.agents.subagents import subagent_manager
    ok, msg = subagent_manager.remove_agent(name)
    return {"ok": ok, "message": msg}


@router.post("/subagents/spawn")
async def spawn_subagent(payload: dict):
    from app.config import settings
    if not settings.enable_sub_agents:
        return {"error": "子 Agent 协作已关闭，可在设置 → Token 预算中开启", "agent_id": None, "status": "disabled"}
    from app.agents.subagents import subagent_manager
    instance = await subagent_manager.spawn(
        payload.get("agent_name", "general-purpose"),
        payload.get("task_prompt", ""),
        payload.get("parent_session_id", ""),
        payload.get("background", False),
    )
    return {"agent_id": instance.agent_id, "status": instance.status}


@router.get("/subagents/instances")
async def list_subagent_instances(status: str = None):
    from app.agents.subagents import subagent_manager
    return {"instances": subagent_manager.list_instances(status)}


@router.get("/subagents/instance/{agent_id}")
async def get_subagent_instance(agent_id: str):
    from app.agents.subagents import subagent_manager
    instance = subagent_manager.get_instance(agent_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Subagent not found")
    return instance


@router.post("/subagents/{agent_id}/message")
async def send_subagent_message(agent_id: str, payload: dict):
    from app.agents.subagents import subagent_manager
    ok, msg = subagent_manager.send_message(agent_id, payload.get("message", ""))
    return {"ok": ok, "message": msg}


@router.post("/subagents/team")
async def create_subagent_team(payload: dict):
    from app.agents.subagents import subagent_manager
    ok, msg = subagent_manager.create_team(payload.get("name", ""), payload.get("agents", []))
    return {"ok": ok, "message": msg}


@router.post("/subagents/instance/{agent_id}/cleanup-worktree")
async def cleanup_subagent_worktree(agent_id: str, payload: dict = None):
    """Remove the git worktree for a finished subagent."""
    from app.agents.subagents import subagent_manager
    remove_branch = (payload or {}).get("remove_branch", False)
    ok, msg = subagent_manager.cleanup_worktree(agent_id, remove_branch=remove_branch)
    return {"ok": ok, "message": msg}


@router.get("/subagents/teams")
async def list_subagent_teams():
    from app.agents.subagents import subagent_manager
    return {"teams": subagent_manager.list_teams()}


@router.post("/subagents/{agent_name}/memory")
async def save_subagent_memory(agent_name: str, payload: dict):
    from app.agents.subagents import subagent_manager
    ok, msg = subagent_manager.save_agent_memory(agent_name, payload.get("content", ""))
    return {"ok": ok, "message": msg}


@router.get("/subagents/{agent_name}/memory")
async def get_subagent_memory(agent_name: str):
    from app.agents.subagents import subagent_manager
    return {"content": subagent_manager.get_agent_memory(agent_name)}


# ==============================================================
# Memory Provider & Context Engine plugins (Hermes single-select)
# ==============================================================
@router.get("/providers/memory")
async def list_memory_providers():
    from app.agents.provider_plugins import memory_provider_registry
    return {"providers": memory_provider_registry.list(),
            "active": memory_provider_registry.active_name}


@router.post("/providers/memory/activate")
async def activate_memory_provider(payload: dict):
    from app.agents.provider_plugins import memory_provider_registry
    ok, msg = memory_provider_registry.activate(payload.get("name", ""))
    return {"ok": ok, "message": msg}


@router.post("/providers/memory/deactivate")
async def deactivate_memory_provider():
    from app.agents.provider_plugins import memory_provider_registry
    ok, msg = memory_provider_registry.deactivate()
    return {"ok": ok, "message": msg}


@router.get("/providers/context-engine")
async def list_context_engines():
    from app.agents.provider_plugins import context_engine_registry
    return {"engines": context_engine_registry.list(),
            "active": context_engine_registry.active_name}


@router.post("/providers/context-engine/activate")
async def activate_context_engine(payload: dict):
    from app.agents.provider_plugins import context_engine_registry
    ok, msg = context_engine_registry.activate(payload.get("name", ""))
    return {"ok": ok, "message": msg}


@router.post("/providers/context-engine/deactivate")
async def deactivate_context_engine():
    from app.agents.provider_plugins import context_engine_registry
    ok, msg = context_engine_registry.deactivate()
    return {"ok": ok, "message": msg}


# ==============================================================
# Permission Scopes
# ==============================================================
@router.get("/permissions/scopes")
async def get_permission_scopes():
    from app.agents.permission_scopes import load_layered_rules
    merged, detail = load_layered_rules()
    return {"merged": merged, "scopes": detail}


@router.get("/monitor/tool-events")
async def get_monitor_tool_events(limit: int = 100, source: str = "", thread_id: str = ""):
    from app.agents.tool_runtime import list_tool_event_threads, list_tool_events
    normalized_limit = max(1, min(int(limit), 500))
    return {
        "events": list_tool_events(normalized_limit, source or None, thread_id or None),
        "threads": list_tool_event_threads(),
    }


@router.post("/permissions/reload")
async def reload_permission_rules():
    from app.agents.tool_runtime import permission_rules
    permission_rules.reload_scoped_rules()
    return {"ok": True, "rules": permission_rules.get_rules()}


# ---------------------------------------------------------------------------
# Credential management (OAuth + key pool)
# ---------------------------------------------------------------------------

@router.post("/credentials/oauth/register")
async def register_oauth(payload: dict):
    from app.models.credentials import credential_store
    return credential_store.register_oauth(
        provider=payload["provider"],
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        token_url=payload["token_url"],
        authorize_url=payload.get("authorize_url", ""),
        scopes=payload.get("scopes", []),
        grant_type=payload.get("grant_type", "client_credentials"),
        extra=payload.get("extra", {}),
    )


@router.post("/credentials/oauth/authorize")
async def authorize_oauth(request: Request, payload: dict):
    from app.models.credentials import credential_store
    redirect_uri = payload.get("redirect_uri") or str(request.url_for("oauth_callback"))
    return credential_store.begin_oauth_authorization(
        provider=payload["provider"],
        redirect_uri=redirect_uri,
        scopes=payload.get("scopes"),
        extra_params=payload.get("extra_params"),
    )


@router.post("/credentials/oauth/exchange")
async def exchange_oauth(payload: dict):
    from app.models.credentials import credential_store
    return credential_store.exchange_authorization_code(
        provider=payload["provider"],
        code=payload["code"],
        redirect_uri=payload.get("redirect_uri", ""),
        extra_params=payload.get("extra_params"),
    )

@router.post("/credentials/oauth/tokens")
async def set_oauth_tokens(payload: dict):
    from app.models.credentials import credential_store
    credential_store.set_oauth_tokens(
        provider=payload["provider"],
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", ""),
        expires_in=payload.get("expires_in", 3600),
    )
    return {"ok": True}

@router.get("/credentials/oauth")
async def list_oauth():
    from app.models.credentials import credential_store
    return {"providers": credential_store.list_oauth_providers()}


@router.get("/credentials/oauth/callback", name="oauth_callback")
async def oauth_callback(state: str = "", code: str = "", error: str = "", provider: str = "", redirect_uri: str = ""):
    from app.models.credentials import credential_store
    try:
        if state:
            result = credential_store.complete_oauth_callback(state=state, code=code, error=error)
        else:
            if error:
                raise ValueError(error)
            if not provider:
                raise ValueError("provider is required when state is not provided")
            result = credential_store.exchange_authorization_code(provider=provider, code=code, redirect_uri=redirect_uri)
        return HTMLResponse(
            content=(
                "<html><body><h1>OAuth authorization complete</h1>"
                f"<p>Provider: {result['provider']}</p>"
                f"<p>Status: {result['status']}</p>"
                f"<p>Expires in: {result['expires_in']} seconds</p>"
                "</body></html>"
            ),
            status_code=200,
        )
    except Exception as exc:
        return HTMLResponse(
            content=(
                "<html><body><h1>OAuth authorization failed</h1>"
                f"<p>{str(exc)}</p>"
                "</body></html>"
            ),
            status_code=400,
        )

@router.delete("/credentials/oauth/{provider}")
async def remove_oauth(provider: str):
    from app.models.credentials import credential_store
    ok = credential_store.remove_oauth(provider)
    return {"ok": ok}

@router.post("/credentials/keys/add")
async def add_api_key(payload: dict):
    from app.models.credentials import credential_store
    return credential_store.add_key(
        provider=payload["provider"],
        api_key=payload["api_key"],
        label=payload.get("label", ""),
    )

@router.get("/credentials/keys/{provider}")
async def list_key_pool(provider: str):
    from app.models.credentials import credential_store
    return {"keys": credential_store.list_key_pool(provider)}

@router.delete("/credentials/keys/{provider}/{label}")
async def remove_api_key(provider: str, label: str):
    from app.models.credentials import credential_store
    ok = credential_store.remove_key(provider, label)
    return {"ok": ok}

@router.post("/credentials/keys/{provider}/{label}/disable")
async def disable_api_key(provider: str, label: str):
    from app.models.credentials import credential_store
    ok = credential_store.disable_key(provider, label)
    return {"ok": ok}
