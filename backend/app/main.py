from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api.chat import router
from app.api.local import local_router, local_ws_router
import os
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.memory_dir, exist_ok=True)
    os.makedirs(settings.threads_dir, exist_ok=True)

    try:
        from app.skills.mcp import mcp_registry
        await mcp_registry.discover_all()
        tools = mcp_registry.list_all_tools()
        if tools:
            logger.info(f"Discovered {len(tools)} MCP tools")
    except Exception as e:
        logger.warning(f"MCP discovery failed: {e}")

    from app.local.gateway import local_gateway
    local_gateway.start_heartbeat()

    # Start local mode scheduled tasks
    try:
        from app.local.scheduler import start_all_schedules
        start_all_schedules()
        logger.info("Local scheduler started")
    except Exception as e:
        logger.warning(f"Local scheduler failed to start: {e}")

    # Start cron scheduler (background thread, checks jobs every 60s)
    try:
        from app.agents.self_evolution import cron_manager
        cron_manager.start_scheduler()
        logger.info("Cron scheduler started")
    except Exception as e:
        logger.warning(f"Cron scheduler failed to start: {e}")

    # Discover and auto-load plugins
    try:
        from app.agents.self_evolution import plugin_registry
        plugin_registry.discover()
        plugin_registry.discover_pip_plugins()
        loaded = plugin_registry.load_all()
        if loaded:
            logger.info(f"Loaded {len(loaded)} plugins: {loaded}")
    except Exception as e:
        logger.warning(f"Plugin discovery/load failed: {e}")

    # Start configured IM channel transports (Feishu, WeCom, Slack)
    try:
        from app.skills.channels import transport_registry
        await transport_registry.start_configured()
        active = [t for t in transport_registry.list_all() if t["running"]]
        if active:
            logger.info(f"IM transports started: {[t['channel_type'] for t in active]}")
    except Exception as e:
        logger.warning(f"IM transport startup failed: {e}")

    # Backfill FTS5 session index from JSON thread store if empty
    # (one-time migration — lets /sessions/search find historical messages)
    try:
        from app.agents.learning_loop import session_search_db
        from app.agents.store import thread_store
        stats = session_search_db.get_stats()
        if stats.get("total_messages", 0) == 0:
            threads = list(thread_store._threads.values())
            if threads:
                count = session_search_db.rebuild_from_threads(threads)
                logger.info(f"Backfilled FTS5 session index with {count} messages from {len(threads)} threads")
    except Exception as e:
        logger.warning(f"FTS5 session backfill skipped: {e}")

    yield

    # Shutdown
    try:
        from app.skills.channels import transport_registry
        await transport_registry.stop_all()
    except Exception as e:
        logger.debug("Suppressed error in main: %s", e)
    try:
        from app.agents.self_evolution import cron_manager
        cron_manager.stop_scheduler()
    except Exception as e:
        logger.debug("Suppressed error in main: %s", e)


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.security.auth import TokenAuthMiddleware
app.add_middleware(TokenAuthMiddleware)

from app.security.rbac import RBACMiddleware
app.add_middleware(RBACMiddleware)

from app.api.features import router as features_router
app.include_router(router)
app.include_router(features_router)
app.include_router(local_router)
app.include_router(local_router, prefix="/api")
app.include_router(local_ws_router)
# NOTE: agents_routes.py, evolution_routes.py, skills_routes.py are DEAD CODE —
# all their routes have been re-implemented in chat.py. Do not register them
# here or you will get duplicate-path errors.


def _has_llm_api_key() -> bool:
    try:
        from app.models.provider import llm_provider
        return llm_provider.has_any_api_key()
    except Exception as e:
        logger.debug("Suppressed error in main: %s", e)
        return bool(settings.openai_api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


@app.get("/health")
@app.get("/api/health")
async def health_check():
    """Health check for load balancers and container orchestration."""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/ready")
async def readiness_check():
    """Readiness probe — checks critical subsystems."""
    checks = {}
    # Check data directories
    checks["data_dir"] = os.path.isdir(settings.data_dir)
    checks["memory_dir"] = os.path.isdir(settings.memory_dir)
    # Check API key availability
    checks["api_key"] = _has_llm_api_key()
    all_ok = all(checks.values())
    return {"ready": all_ok, "checks": checks}
