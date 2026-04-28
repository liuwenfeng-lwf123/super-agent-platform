"""
Simple in-process task scheduler for local mode.
Stores scheduled tasks in data/local_schedules.json.
Each task triggers a message to the local agent at the scheduled time.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEDULES_PATH = Path("data/local_schedules.json")

_schedules: dict[str, dict] = {}
_running_tasks: dict[str, asyncio.Task] = {}


def _load():
    global _schedules
    if SCHEDULES_PATH.exists():
        try:
            _schedules = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Suppressed error in scheduler: %s", e)
            _schedules = {}


def _save():
    SCHEDULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULES_PATH.write_text(json.dumps(_schedules, ensure_ascii=False, indent=2), encoding="utf-8")


_load()


def list_schedules() -> list[dict]:
    return [{"id": k, **v} for k, v in _schedules.items()]


def add_schedule(
    message: str,
    run_at: str | None = None,
    interval_minutes: int | None = None,
    description: str = "",
    thread_id: str = "",
) -> dict:
    """
    Add a scheduled task.
    - run_at: ISO format datetime for one-time execution
    - interval_minutes: repeat every N minutes
    """
    schedule_id = uuid.uuid4().hex[:8]
    entry = {
        "message": message,
        "description": description or message[:50],
        "thread_id": thread_id,
        "run_at": run_at,
        "interval_minutes": interval_minutes,
        "created_at": datetime.now().isoformat(),
        "last_run": None,
        "enabled": True,
    }
    _schedules[schedule_id] = entry
    _save()
    _ensure_task(schedule_id, entry)
    return {"id": schedule_id, **entry}


def remove_schedule(schedule_id: str) -> bool:
    if schedule_id in _schedules:
        del _schedules[schedule_id]
        _save()
        task = _running_tasks.pop(schedule_id, None)
        if task:
            task.cancel()
        return True
    return False


def toggle_schedule(schedule_id: str, enabled: bool) -> bool:
    if schedule_id in _schedules:
        _schedules[schedule_id]["enabled"] = enabled
        _save()
        if enabled:
            _ensure_task(schedule_id, _schedules[schedule_id])
        else:
            task = _running_tasks.pop(schedule_id, None)
            if task:
                task.cancel()
        return True
    return False


async def _run_scheduled_message(schedule_id: str, entry: dict):
    """Execute a scheduled task by sending a notification to the local client."""
    try:
        from app.local.gateway import local_gateway

        # Find any connected client
        clients = local_gateway.list_clients()
        if not clients:
            logger.warning("Scheduled task %s: no local client connected", schedule_id)
            return

        client = local_gateway.get_client(clients[0]["client_id"])
        if not client:
            return

        msg = entry.get("message", "")
        # Send a notification about the scheduled task
        await client.send_request(
            "send_notification",
            {"title": "定时任务", "message": msg[:100]},
            timeout=10,
            force_auto_approve=True,
        )
        logger.info("Scheduled task %s executed: %s", schedule_id, msg[:60])
        entry["last_run"] = datetime.now().isoformat()
        _save()
    except Exception as exc:
        logger.error("Scheduled task %s failed: %s", schedule_id, exc)


async def _scheduler_loop(schedule_id: str, entry: dict):
    """Background loop for a single scheduled task."""
    try:
        if entry.get("run_at"):
            # One-time: wait until run_at
            target = datetime.fromisoformat(entry["run_at"])
            delay = (target - datetime.now()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            await _run_scheduled_message(schedule_id, entry)
            # After one-time execution, disable it
            if schedule_id in _schedules:
                _schedules[schedule_id]["enabled"] = False
                _save()
        elif entry.get("interval_minutes"):
            # Repeating
            interval = entry["interval_minutes"] * 60
            while True:
                await asyncio.sleep(interval)
                if not _schedules.get(schedule_id, {}).get("enabled"):
                    break
                await _run_scheduled_message(schedule_id, entry)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Scheduler loop %s error: %s", schedule_id, exc)


def _ensure_task(schedule_id: str, entry: dict):
    """Start the background task if not already running."""
    if not entry.get("enabled"):
        return
    existing = _running_tasks.get(schedule_id)
    if existing and not existing.done():
        return
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_scheduler_loop(schedule_id, entry))
        _running_tasks[schedule_id] = task
    except RuntimeError:
        # No event loop yet; will be started when server boots
        pass


def start_all_schedules():
    """Called at server startup to resume all enabled schedules."""
    for sid, entry in _schedules.items():
        if entry.get("enabled"):
            _ensure_task(sid, entry)
