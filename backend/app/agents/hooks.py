"""
Hooks System — Claude Code-inspired lifecycle hooks.

Supports 20+ hook events with matchers, async execution, prompt-based hooks,
and agent-based hooks. Hooks can be defined in settings, skills, or subagents.

Hook events:
  SessionStart, InstructionsLoaded, UserPromptSubmit, UserPromptExpansion,
  PreToolUse, PostToolUse, PostToolUseFailure, PermissionRequest, PermissionDenied,
  SubagentStart, SubagentStop, TaskCreated, TaskCompleted, Stop, StopFailure,
  TeammateIdle, ConfigChange, CwdChanged, FileChanged, PreCompact, PostCompact,
  SessionEnd, Notification, Elicitation, ElicitationResult
"""
import os
import re
import json
import logging
import asyncio
import subprocess
from enum import Enum
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Callable

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
HOOKS_DIR = os.path.join(DATA_DIR, "hooks")
os.makedirs(HOOKS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Hook Events
# ---------------------------------------------------------------------------
class HookEvent(str, Enum):
    SESSION_START = "SessionStart"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    USER_PROMPT_EXPANSION = "UserPromptExpansion"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    TEAMMATE_IDLE = "TeammateIdle"
    CONFIG_CHANGE = "ConfigChange"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    SESSION_END = "SessionEnd"
    NOTIFICATION = "Notification"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"


# ---------------------------------------------------------------------------
# Hook Data Structures
# ---------------------------------------------------------------------------
@dataclass
class HookMatcher:
    """Matcher group — filters when a hook fires."""
    tool_name: Optional[str] = None          # e.g. "Bash", "Write"
    tool_name_pattern: Optional[str] = None  # regex, e.g. "mcp__.*"
    file_pattern: Optional[str] = None       # glob for FileChanged
    subagent_name: Optional[str] = None      # for SubagentStart/Stop
    custom_predicate: Optional[str] = None   # Python expression

    def matches(self, context: dict) -> bool:
        """Check if this matcher applies to the given event context."""
        if self.tool_name and context.get("tool_name") != self.tool_name:
            return False
        if self.tool_name_pattern:
            tool = context.get("tool_name", "")
            if not re.match(self.tool_name_pattern, tool):
                return False
        if self.file_pattern:
            file_path = context.get("file_path", "")
            import fnmatch
            if not fnmatch.fnmatch(file_path, self.file_pattern):
                return False
        if self.subagent_name and context.get("subagent_name") != self.subagent_name:
            return False
        return True


@dataclass
class HookHandler:
    """A single handler to execute when a hook matches."""
    handler_type: str = "command"   # "command", "script", "http", "prompt", "agent"
    command: str = ""               # shell command / script path / URL / prompt text
    timeout: int = 10               # seconds
    async_mode: bool = False        # run in background
    on_error: str = "warn"          # "warn", "fail", "ignore"
    environment: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HookDefinition:
    """Complete hook definition: event + matchers + handlers."""
    event: str
    matchers: list[HookMatcher] = field(default_factory=list)
    handlers: list[HookHandler] = field(default_factory=list)
    enabled: bool = True
    source: str = "settings"  # "settings", "skill", "subagent", "plugin"
    name: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "event": self.event,
            "matchers": [asdict(m) for m in self.matchers],
            "handlers": [h.to_dict() for h in self.handlers],
            "enabled": self.enabled,
            "source": self.source,
            "name": self.name,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Hook Execution Results
# ---------------------------------------------------------------------------
@dataclass
class HookResult:
    hook_name: str
    event: str
    status: str = "success"  # "success", "error", "timeout", "skipped"
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0
    # JSON output fields (Claude Code protocol)
    decision: Optional[str] = None         # "approve", "deny", "skip"
    reason: Optional[str] = None
    modified_input: Optional[dict] = None  # can modify tool input
    suppress_output: bool = False


# ---------------------------------------------------------------------------
# Hook Executor
# ---------------------------------------------------------------------------
class HookExecutor:
    """Execute hook handlers (command, script, HTTP, prompt, agent)."""

    async def execute_handler(self, handler: HookHandler, event: str,
                              context: dict) -> HookResult:
        """Execute a single handler and return the result."""
        start = datetime.now()
        result = HookResult(hook_name="", event=event)

        try:
            if handler.handler_type == "command":
                result = await self._run_command(handler, context)
            elif handler.handler_type == "script":
                result = await self._run_script(handler, context)
            elif handler.handler_type == "http":
                result = await self._run_http(handler, context)
            elif handler.handler_type == "prompt":
                result = await self._run_prompt(handler, context)
            elif handler.handler_type == "agent":
                result = await self._run_agent(handler, context)
            else:
                result.status = "error"
                result.error = f"Unknown handler type: {handler.handler_type}"
        except asyncio.TimeoutError:
            result.status = "timeout"
            result.error = f"Handler timed out after {handler.timeout}s"
        except Exception as e:
            result.status = "error"
            result.error = str(e)

        elapsed = (datetime.now() - start).total_seconds() * 1000
        result.duration_ms = round(elapsed, 2)
        result.event = event
        return result

    async def _run_command(self, handler: HookHandler, context: dict) -> HookResult:
        """Execute a shell command hook."""
        result = HookResult(hook_name="", event="")

        # Build input JSON for the command (Claude Code protocol)
        input_json = json.dumps(context, default=str)
        env = {**os.environ, **handler.environment, "HOOK_INPUT": input_json}

        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                handler.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            ),
            timeout=handler.timeout,
        )
        stdout, stderr = await proc.communicate()

        result.output = stdout.decode().strip() if stdout else ""
        result.error = stderr.decode().strip() if stderr else ""

        # Exit code protocol (Claude Code compatible)
        if proc.returncode == 0:
            result.status = "success"
            # Try to parse JSON output
            self._parse_json_output(result)
        elif proc.returncode == 2:
            result.status = "success"
            result.decision = "deny"
            result.reason = result.output or "Denied by hook"
        else:
            result.status = "error"

        return result

    async def _run_script(self, handler: HookHandler, context: dict) -> HookResult:
        """Execute a script file hook."""
        if not os.path.isfile(handler.command):
            return HookResult(hook_name="", event="", status="error",
                              error=f"Script not found: {handler.command}")
        # Delegate to command execution
        handler_copy = HookHandler(
            handler_type="command",
            command=f"python {handler.command}" if handler.command.endswith(".py") else handler.command,
            timeout=handler.timeout,
            environment=handler.environment,
        )
        return await self._run_command(handler_copy, context)

    async def _run_http(self, handler: HookHandler, context: dict) -> HookResult:
        """Execute an HTTP webhook hook."""
        result = HookResult(hook_name="", event="")
        try:
            import urllib.request
            data = json.dumps(context, default=str).encode()
            req = urllib.request.Request(
                handler.command,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=handler.timeout)
            result.output = resp.read().decode()
            result.status = "success"
            self._parse_json_output(result)
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    async def _run_prompt(self, handler: HookHandler, context: dict) -> HookResult:
        """Prompt-based hook — uses LLM to evaluate conditions."""
        result = HookResult(hook_name="", event="")
        try:
            from app.models.provider import llm_provider
            model = llm_provider.get_chat_model(streaming=False)
            from langchain_core.messages import HumanMessage

            prompt = handler.command.replace("$CONTEXT", json.dumps(context, default=str)[:2000])
            response = await model.ainvoke([HumanMessage(content=prompt)])
            output = response.content if hasattr(response, "content") else str(response)
            result.output = output
            result.status = "success"

            # Parse structured response
            output_lower = output.lower().strip()
            if "deny" in output_lower or "block" in output_lower:
                result.decision = "deny"
                result.reason = output
            elif "approve" in output_lower or "allow" in output_lower:
                result.decision = "approve"
            else:
                result.decision = "skip"
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    async def _run_agent(self, handler: HookHandler, context: dict) -> HookResult:
        """Agent-based hook — spawns a sub-agent to evaluate."""
        result = HookResult(hook_name="", event="")
        try:
            from app.agents.evolution import skill_fork_executor
            fork_result = skill_fork_executor.execute_in_fork(
                skill_name=handler.command,
                arguments=json.dumps(context, default=str)[:2000],
            )
            result.output = json.dumps(fork_result, default=str)
            result.status = "success"
        except Exception as e:
            result.status = "error"
            result.error = str(e)
        return result

    def _parse_json_output(self, result: HookResult):
        """Parse JSON output from hook (Claude Code protocol)."""
        if not result.output:
            return
        try:
            data = json.loads(result.output)
            if isinstance(data, dict):
                result.decision = data.get("decision")
                result.reason = data.get("reason")
                result.modified_input = data.get("modifiedInput")
                result.suppress_output = data.get("suppressOutput", False)
        except (json.JSONDecodeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Hooks Registry — Central manager
# ---------------------------------------------------------------------------
HOOKS_CONFIG_PATH = os.path.join(HOOKS_DIR, "hooks_config.json")


class HooksRegistry:
    """Central registry for all hook definitions. Manages registration,
    lookup, and firing of hooks across settings, skills, and subagents."""

    def __init__(self):
        self._hooks: list[HookDefinition] = []
        self._executor = HookExecutor()
        self._history: list[dict] = []
        self._max_history = 200
        self._background_tasks: set[asyncio.Task] = set()
        self._load_config()

    def _load_config(self):
        """Load persisted hooks from config."""
        if os.path.exists(HOOKS_CONFIG_PATH):
            try:
                data = json.loads(Path(HOOKS_CONFIG_PATH).read_text())
                for h in data.get("hooks", []):
                    self._hooks.append(HookDefinition(
                        event=h["event"],
                        matchers=[HookMatcher(**m) for m in h.get("matchers", [])],
                        handlers=[HookHandler(**hh) for hh in h.get("handlers", [])],
                        enabled=h.get("enabled", True),
                        source=h.get("source", "settings"),
                        name=h.get("name", ""),
                        description=h.get("description", ""),
                    ))
            except Exception as e:
                logger.warning(f"Failed to load hooks config: {e}")

    def _save_config(self):
        """Persist hooks to config file."""
        Path(HOOKS_CONFIG_PATH).write_text(json.dumps({
            "hooks": [h.to_dict() for h in self._hooks if h.source == "settings"],
        }, ensure_ascii=False, indent=2))

    # --- Registration ---
    def register(self, hook: HookDefinition) -> tuple[bool, str]:
        """Register a new hook definition."""
        # Validate event
        valid_events = [e.value for e in HookEvent]
        if hook.event not in valid_events:
            return False, f"Invalid event '{hook.event}'. Valid: {valid_events}"
        if not hook.handlers:
            return False, "At least one handler required"
        if not hook.name:
            hook.name = f"{hook.event}_{len(self._hooks)}"

        self._hooks.append(hook)
        if hook.source == "settings":
            self._save_config()
        return True, f"Hook '{hook.name}' registered for {hook.event}"

    def unregister(self, name: str) -> tuple[bool, str]:
        """Remove a hook by name."""
        before = len(self._hooks)
        self._hooks = [h for h in self._hooks if h.name != name]
        if len(self._hooks) < before:
            self._save_config()
            return True, f"Hook '{name}' removed"
        return False, f"Hook '{name}' not found"

    def enable(self, name: str) -> tuple[bool, str]:
        for h in self._hooks:
            if h.name == name:
                h.enabled = True
                self._save_config()
                return True, f"Hook '{name}' enabled"
        return False, f"Hook '{name}' not found"

    def disable(self, name: str) -> tuple[bool, str]:
        for h in self._hooks:
            if h.name == name:
                h.enabled = False
                self._save_config()
                return True, f"Hook '{name}' disabled"
        return False, f"Hook '{name}' not found"

    def list_hooks(self) -> list[dict]:
        """List all registered hooks."""
        return [h.to_dict() for h in self._hooks]

    def get_hooks_for_event(self, event: str) -> list[HookDefinition]:
        """Get all enabled hooks for a specific event."""
        return [h for h in self._hooks if h.event == event and h.enabled]

    # --- Execution ---
    async def fire(self, event: str, context: dict = None) -> list[HookResult]:
        """Fire all matching hooks for an event. Returns results."""
        context = context or {}
        context["event"] = event
        context["timestamp"] = datetime.now().isoformat()

        hooks = self.get_hooks_for_event(event)
        results = []

        for hook in hooks:
            # Check matchers
            if hook.matchers and not any(m.matches(context) for m in hook.matchers):
                continue

            for handler in hook.handlers:
                if handler.async_mode:
                    # Fire and forget for async hooks — hold strong ref to prevent GC
                    task = asyncio.create_task(self._fire_async(hook, handler, event, context))
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                    results.append(HookResult(
                        hook_name=hook.name, event=event, status="async_started"
                    ))
                else:
                    result = await self._executor.execute_handler(handler, event, context)
                    result.hook_name = hook.name
                    results.append(result)

                    # Log to history
                    self._log_result(result)

                    # Handle deny decision — stop processing
                    if result.decision == "deny":
                        return results

        return results

    def fire_sync(self, event: str, context: dict = None) -> list[HookResult]:
        """Synchronous fire — for non-async contexts."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.fire(event, context))
                    return future.result(timeout=30)
            else:
                return loop.run_until_complete(self.fire(event, context))
        except RuntimeError:
            return asyncio.run(self.fire(event, context))
        except Exception as exc:
            logger.warning("fire_sync failed for event %s: %s", event, exc)
            return []

    async def _fire_async(self, hook: HookDefinition, handler: HookHandler,
                          event: str, context: dict):
        """Fire a hook handler asynchronously (background)."""
        try:
            result = await self._executor.execute_handler(handler, event, context)
            result.hook_name = hook.name
            self._log_result(result)
        except Exception as e:
            logger.warning(f"Async hook '{hook.name}' failed: {e}")

    def _log_result(self, result: HookResult):
        """Record hook execution in history."""
        self._history.append({
            "hook": result.hook_name,
            "event": result.event,
            "status": result.status,
            "decision": result.decision,
            "duration_ms": result.duration_ms,
            "timestamp": datetime.now().isoformat(),
        })
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def get_history(self, limit: int = 50) -> list[dict]:
        return self._history[-limit:]

    # --- Convenience: Register from skill/subagent frontmatter ---
    def register_from_skill(self, skill_name: str, hooks_config: list[dict]):
        """Register hooks defined in a skill's frontmatter."""
        for hc in hooks_config:
            hook = HookDefinition(
                event=hc.get("event", ""),
                matchers=[HookMatcher(**m) for m in hc.get("matchers", [])],
                handlers=[HookHandler(**h) for h in hc.get("handlers", [])],
                source="skill",
                name=f"skill:{skill_name}:{hc.get('event', '')}",
                description=f"Hook from skill '{skill_name}'",
            )
            self.register(hook)

    def register_from_subagent(self, agent_name: str, hooks_config: list[dict]):
        """Register hooks defined in a subagent's frontmatter."""
        for hc in hooks_config:
            event = hc.get("event", "")
            # Subagent Stop hooks auto-convert (Claude Code pattern)
            if event == "Stop":
                event = "SubagentStop"
            hook = HookDefinition(
                event=event,
                matchers=[HookMatcher(**m) for m in hc.get("matchers", [])],
                handlers=[HookHandler(**h) for h in hc.get("handlers", [])],
                source="subagent",
                name=f"agent:{agent_name}:{event}",
                description=f"Hook from subagent '{agent_name}'",
            )
            self.register(hook)

    def unregister_source(self, source_prefix: str):
        """Remove all hooks from a specific source prefix."""
        self._hooks = [h for h in self._hooks if not h.name.startswith(source_prefix)]


# Singleton
hooks_registry = HooksRegistry()
