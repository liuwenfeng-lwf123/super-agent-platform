"""
Background Task System — async task creation, status tracking, and lifecycle management.
"""
import asyncio
import json
import uuid
import logging
from datetime import datetime
from typing import Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task:
    def __init__(self, task_id: str, name: str, description: str, agent_id: str = "main"):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.agent_id = agent_id
        self.status = TaskStatus.PENDING
        self.result: Optional[str] = None
        self.error: Optional[str] = None
        self.progress: float = 0.0
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self._future: Optional[asyncio.Task] = None
        self.logs: list[str] = []
        self.summary: Optional[str] = None
        self.metadata: dict[str, Any] = {}

    def log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {message}")
        if len(self.logs) > 100:
            self.logs = self.logs[-50:]

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "agent_id": self.agent_id,
            "status": self.status.value,
            "result": self.result[:2000] if self.result else None,
            "error": self.error,
            "progress": self.progress,
            "summary": self.summary,
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "logs": self.logs[-10:],
        }


class TaskManager:
    """Manages background tasks for agents."""

    def __init__(self, max_concurrent: int = 10):
        self._tasks: dict[str, Task] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._listeners: dict[str, list[asyncio.Queue]] = {}

    def create_task(self, name: str, description: str, agent_id: str = "main") -> Task:
        task_id = str(uuid.uuid4())[:8]
        task = Task(task_id=task_id, name=name, description=description, agent_id=agent_id)
        self._tasks[task_id] = task
        logger.info(f"Task created: {task_id} - {name}")
        # Auto-cleanup every 50 task creations
        if len(self._tasks) % 50 == 0:
            self.cleanup_old(max_age_hours=1)
        return task

    async def run_task(self, task: Task, coro) -> Task:
        """Run a coroutine as a background task."""
        async def _wrapped():
            async with self._semaphore:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now()
                task.log(f"Started: {task.name}")
                await self._notify(task)
                try:
                    result = await coro
                    task.result = str(result) if result else "(completed)"
                    task.status = TaskStatus.COMPLETED
                    task.progress = 1.0
                    task.log("Completed successfully")
                except asyncio.CancelledError:
                    task.status = TaskStatus.CANCELLED
                    task.log("Cancelled")
                except Exception as e:
                    task.error = str(e)
                    task.status = TaskStatus.FAILED
                    task.log(f"Failed: {e}")
                finally:
                    task.completed_at = datetime.now()
                    await self._notify(task)
                return task

        task._future = asyncio.create_task(_wrapped())
        return task

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or not task._future:
            return False
        if task.status == TaskStatus.RUNNING:
            task._future.cancel()
            return True
        return False

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_tasks(self, agent_id: Optional[str] = None) -> list[dict]:
        tasks = self._tasks.values()
        if agent_id:
            tasks = [t for t in tasks if t.agent_id == agent_id]
        return [t.to_dict() for t in sorted(tasks, key=lambda t: t.created_at, reverse=True)]

    async def update_task(
        self,
        task_id: str,
        *,
        status: Optional[TaskStatus] = None,
        result: Optional[str] = None,
        error: Optional[str] = None,
        progress: Optional[float] = None,
        summary: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        log_message: Optional[str] = None,
    ) -> Optional[Task]:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        now = datetime.now()
        if status is not None:
            task.status = status
            if status == TaskStatus.RUNNING and task.started_at is None:
                task.started_at = now
            if status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                task.completed_at = now
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        if progress is not None:
            task.progress = max(0.0, min(1.0, float(progress)))
        if summary is not None:
            task.summary = summary
        if metadata:
            task.metadata.update(metadata)
        if log_message:
            task.log(log_message)
        await self._notify(task)
        return task

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """Subscribe to task status updates."""
        if task_id not in self._listeners:
            self._listeners[task_id] = []
        q: asyncio.Queue = asyncio.Queue()
        self._listeners[task_id].append(q)
        return q

    async def _notify(self, task: Task):
        for q in self._listeners.get(task.task_id, []):
            try:
                await q.put(task.to_dict())
            except Exception as e:
                logger.debug("Suppressed error in task_manager: %s", e)

    def cleanup_old(self, max_age_hours: int = 24):
        """Remove completed tasks older than max_age_hours."""
        cutoff = datetime.now()
        to_remove = []
        for tid, task in self._tasks.items():
            if task.completed_at:
                age = (cutoff - task.completed_at).total_seconds() / 3600
                if age > max_age_hours:
                    to_remove.append(tid)
        for tid in to_remove:
            del self._tasks[tid]
            self._listeners.pop(tid, None)


task_manager = TaskManager()
