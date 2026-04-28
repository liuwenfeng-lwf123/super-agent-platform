"""
Dynamic Sub-task Spawning — allows agents to spawn child agents mid-execution.

When an agent discovers a sub-problem during execution that would benefit from
a specialized agent, it can dynamically spawn a child agent to handle it,
then incorporate the results back into its own workflow.
"""
import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator, Optional
from dataclasses import dataclass, field
from datetime import datetime

from app.agents.cost_tracker import cost_tracker

logger = logging.getLogger(__name__)

MAX_SPAWN_DEPTH = 3
MAX_CHILDREN_PER_AGENT = 4


@dataclass
class SpawnRequest:
    """Request from a parent agent to spawn a child agent."""
    task: str
    role: str = "researcher"
    reason: str = ""
    priority: str = "normal"  # normal, high, low
    timeout: int = 90
    tools_needed: list[str] = field(default_factory=list)


@dataclass
class SpawnResult:
    """Result from a spawned child agent."""
    agent_id: str
    task: str
    role: str
    status: str  # completed, failed, timeout
    result: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0


class DynamicSpawnManager:
    """Manages dynamic child agent spawning with depth limits and budget checks."""

    def __init__(self):
        self._active_spawns: dict[str, list[str]] = {}  # parent_id -> [child_ids]
        self._spawn_depth: dict[str, int] = {}  # agent_id -> depth
        self._results: dict[str, SpawnResult] = {}  # child_id -> result

    def can_spawn(self, parent_id: str) -> tuple[bool, str]:
        """Check if a parent agent is allowed to spawn more children."""
        depth = self._spawn_depth.get(parent_id, 0)
        if depth >= MAX_SPAWN_DEPTH:
            return False, f"Maximum spawn depth ({MAX_SPAWN_DEPTH}) reached"

        children = self._active_spawns.get(parent_id, [])
        if len(children) >= MAX_CHILDREN_PER_AGENT:
            return False, f"Maximum children per agent ({MAX_CHILDREN_PER_AGENT}) reached"

        if cost_tracker.is_over_budget():
            return False, "Token budget exceeded"

        return True, ""

    def register_parent(self, parent_id: str, depth: int = 0):
        """Register a parent agent with its depth level."""
        self._spawn_depth[parent_id] = depth
        if parent_id not in self._active_spawns:
            self._active_spawns[parent_id] = []

    async def spawn_child(
        self,
        parent_id: str,
        request: SpawnRequest,
        model: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Spawn a child agent and stream status events.
        Yields events:
          {"type": "spawn_started", "parent_id": ..., "child_id": ..., "task": ...}
          {"type": "spawn_progress", "child_id": ..., "tool": ..., "status": ...}
          {"type": "spawn_completed", "child_id": ..., "result": ..., "status": ...}
        """
        can, reason = self.can_spawn(parent_id)
        if not can:
            yield {
                "type": "spawn_denied",
                "parent_id": parent_id,
                "reason": reason,
                "task": request.task,
            }
            return

        child_id = f"child-{uuid.uuid4().hex[:6]}"
        parent_depth = self._spawn_depth.get(parent_id, 0)
        self._spawn_depth[child_id] = parent_depth + 1
        self._active_spawns[parent_id].append(child_id)

        yield {
            "type": "spawn_started",
            "parent_id": parent_id,
            "child_id": child_id,
            "task": request.task,
            "role": request.role,
            "depth": parent_depth + 1,
        }

        start_time = asyncio.get_event_loop().time()
        try:
            result = await asyncio.wait_for(
                self._execute_child(child_id, request, model),
                timeout=request.timeout,
            )
            elapsed = asyncio.get_event_loop().time() - start_time
            result.elapsed_seconds = elapsed
            self._results[child_id] = result

            yield {
                "type": "spawn_completed",
                "parent_id": parent_id,
                "child_id": child_id,
                "task": request.task,
                "role": request.role,
                "status": result.status,
                "result": result.result[:2000],
                "tool_calls": result.tool_calls[-5:],
                "elapsed_seconds": round(elapsed, 1),
            }
        except asyncio.TimeoutError:
            elapsed = asyncio.get_event_loop().time() - start_time
            timeout_result = SpawnResult(
                agent_id=child_id,
                task=request.task,
                role=request.role,
                status="timeout",
                result=f"Child agent timed out after {request.timeout}s",
                elapsed_seconds=elapsed,
            )
            self._results[child_id] = timeout_result
            yield {
                "type": "spawn_completed",
                "parent_id": parent_id,
                "child_id": child_id,
                "task": request.task,
                "status": "timeout",
                "result": timeout_result.result,
                "elapsed_seconds": round(elapsed, 1),
            }
        except Exception as e:
            elapsed = asyncio.get_event_loop().time() - start_time
            error_result = SpawnResult(
                agent_id=child_id,
                task=request.task,
                role=request.role,
                status="failed",
                result=f"Error: {str(e)}",
                elapsed_seconds=elapsed,
            )
            self._results[child_id] = error_result
            yield {
                "type": "spawn_completed",
                "parent_id": parent_id,
                "child_id": child_id,
                "task": request.task,
                "status": "failed",
                "result": error_result.result,
                "elapsed_seconds": round(elapsed, 1),
            }

    async def _execute_child(
        self, child_id: str, request: SpawnRequest, model: str | None
    ) -> SpawnResult:
        """Execute a child agent using the orchestrator's SubAgent."""
        from app.agents.orchestrator import SubAgent, BUILT_IN_AGENTS, MessageBus

        definition = BUILT_IN_AGENTS.get(request.role)
        message_bus = MessageBus()

        agent = SubAgent(
            agent_id=child_id,
            task=request.task,
            role=request.role,
            model=model,
            tools_needed=request.tools_needed,
            message_bus=message_bus,
            definition=definition,
        )

        await agent.execute()

        return SpawnResult(
            agent_id=child_id,
            task=request.task,
            role=request.role,
            status=agent.status,
            result=agent.result,
            tool_calls=agent.tool_calls,
        )

    async def spawn_multiple(
        self,
        parent_id: str,
        requests: list[SpawnRequest],
        model: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Spawn multiple children in parallel."""
        valid_requests = []
        for req in requests:
            can, reason = self.can_spawn(parent_id)
            if can:
                valid_requests.append(req)
            else:
                yield {
                    "type": "spawn_denied",
                    "parent_id": parent_id,
                    "reason": reason,
                    "task": req.task,
                }

        if not valid_requests:
            return

        # Execute all valid children in parallel
        queue: asyncio.Queue = asyncio.Queue()
        child_ids = []

        async def run_one(req: SpawnRequest):
            async for event in self.spawn_child(parent_id, req, model):
                await queue.put(event)
                if event["type"] == "spawn_started":
                    child_ids.append(event["child_id"])

        gather = asyncio.ensure_future(
            asyncio.gather(*[run_one(r) for r in valid_requests], return_exceptions=True)
        )

        while not gather.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.3)
                yield event
            except asyncio.TimeoutError:
                continue

        # Drain remaining
        while not queue.empty():
            yield await queue.get()

        # Summary
        completed = sum(1 for cid in child_ids if self._results.get(cid, SpawnResult("", "", "", "")).status == "completed")
        yield {
            "type": "spawn_batch_done",
            "parent_id": parent_id,
            "total": len(valid_requests),
            "completed": completed,
            "child_ids": child_ids,
        }

    def get_child_result(self, child_id: str) -> Optional[SpawnResult]:
        """Get the result of a previously spawned child."""
        return self._results.get(child_id)

    def get_all_children_results(self, parent_id: str) -> list[SpawnResult]:
        """Get all results for a parent's children."""
        child_ids = self._active_spawns.get(parent_id, [])
        return [self._results[cid] for cid in child_ids if cid in self._results]

    def cleanup(self, parent_id: str):
        """Clean up tracking data for a parent agent."""
        children = self._active_spawns.pop(parent_id, [])
        for cid in children:
            self._results.pop(cid, None)
            self._spawn_depth.pop(cid, None)
        self._spawn_depth.pop(parent_id, None)


# Singleton
spawn_manager = DynamicSpawnManager()
