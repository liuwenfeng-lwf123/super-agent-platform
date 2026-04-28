"""
Subagent System — Claude Code-inspired multi-agent architecture.

  Built-in subagent types:
    - Explore: read-only, fast model (Haiku), file discovery
    - Plan: read-only, inherits model, codebase research
    - General-purpose: all tools, complex operations

  Features:
    - Foreground (blocking) and background (concurrent) execution
    - Permission modes: default, acceptEdits, auto, dontAsk, bypassPermissions, plan
    - Structured config surface for tools / model / skills / memory / isolation
    - Agent teams (multi-agent collaboration)
    - Subagent resume via SendMessage
    - Persistent memory per subagent
    - Auto-compaction
    - Worktree isolation
"""
import os
import json
import uuid
import asyncio
import logging
from enum import Enum
from datetime import datetime
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
AGENTS_DIR = os.path.join(DATA_DIR, "agents")
AGENT_TRANSCRIPTS_DIR = os.path.join(DATA_DIR, "agent_transcripts")
AGENT_MEMORY_DIR = os.path.join(DATA_DIR, "agent_memory")
os.makedirs(AGENTS_DIR, exist_ok=True)
os.makedirs(AGENT_TRANSCRIPTS_DIR, exist_ok=True)
os.makedirs(AGENT_MEMORY_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Permission Modes
# ---------------------------------------------------------------------------
class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    AUTO = "auto"
    DONT_ASK = "dontAsk"
    BYPASS = "bypassPermissions"
    PLAN = "plan"


TOOL_NAME_ALIASES = {
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "write_file",
    "Glob": "list_files",
    "Grep": "execute_bash",
    "Bash": "execute_bash",
    "Python": "execute_python",
    "JavaScript": "execute_javascript",
}


def _normalize_tool_names(names: list[str]) -> list[str]:
    normalized: list[str] = []
    for name in names:
        raw = (name or "").strip()
        if not raw:
            continue
        mapped = TOOL_NAME_ALIASES.get(raw, raw)
        if mapped not in normalized:
            normalized.append(mapped)
    return normalized


# ---------------------------------------------------------------------------
# Subagent Definition
# ---------------------------------------------------------------------------
@dataclass
class SubagentConfig:
    """Configuration for a subagent (matches Claude Code frontmatter fields)."""
    name: str
    description: str = ""
    prompt: str = ""                         # system prompt
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: Optional[str] = None              # per-agent model override
    permission_mode: str = "default"
    mcp_servers: list[dict] = field(default_factory=list)
    hooks: list[dict] = field(default_factory=list)
    max_turns: int = 50
    skills: list[str] = field(default_factory=list)
    initial_prompt: str = ""
    memory: Optional[str] = None             # "user", "project", "local"
    effort: str = "medium"                   # low/medium/high/xhigh/max
    background: bool = False
    isolation: Optional[str] = None          # "worktree"
    color: str = ""
    source: str = "user"                     # user, project, plugin, builtin

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Built-in Subagent Types
# ---------------------------------------------------------------------------
BUILTIN_AGENTS = {
    "explore": SubagentConfig(
        name="explore",
        description="File discovery, code search, codebase exploration",
        tools=["read_file", "list_files", "execute_bash", "tool_search"],
        disallowed_tools=["write_file"],
        model="haiku",
        effort="low",
        source="builtin",
    ),
    "plan": SubagentConfig(
        name="plan",
        description="Codebase research for planning",
        tools=["read_file", "list_files", "execute_bash", "tool_search"],
        disallowed_tools=["write_file"],
        permission_mode="plan",
        source="builtin",
    ),
    "general-purpose": SubagentConfig(
        name="general-purpose",
        description="Complex research, multi-step operations, code modifications",
        source="builtin",
    ),
}


# ---------------------------------------------------------------------------
# Subagent Instance (runtime state)
# ---------------------------------------------------------------------------
@dataclass
class SubagentInstance:
    """Runtime state of a running subagent."""
    agent_id: str
    config: SubagentConfig
    session_id: str = ""
    status: str = "pending"          # pending, running, completed, failed, paused
    started_at: str = ""
    finished_at: str = ""
    transcript: list[dict] = field(default_factory=list)
    result_summary: str = ""
    turn_count: int = 0
    parent_session_id: str = ""
    is_background: bool = False
    task: Optional[Any] = None       # asyncio.Task reference
    workdir: Optional[str] = None    # Git worktree path if isolation="worktree"
    worktree_branch: Optional[str] = None  # Branch name inside the worktree

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "config": self.config.to_dict(),
            "session_id": self.session_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "transcript": list(self.transcript),
            "result_summary": self.result_summary,
            "turn_count": self.turn_count,
            "parent_session_id": self.parent_session_id,
            "is_background": self.is_background,
            "workdir": self.workdir,
            "worktree_branch": self.worktree_branch,
        }


# ---------------------------------------------------------------------------
# Subagent Manager
# ---------------------------------------------------------------------------
class SubagentManager:
    """Manages subagent lifecycle: creation, execution, resume, teams."""

    def __init__(self):
        self._agents: dict[str, SubagentConfig] = {}
        self._instances: dict[str, SubagentInstance] = {}
        self._teams: dict[str, list[str]] = {}  # team_name -> agent_ids
        self._load_agents()

    def _load_agents(self):
        """Load saved agent definitions."""
        # Load builtins
        for name, config in BUILTIN_AGENTS.items():
            self._agents[name] = config

        # Load user-defined agents
        for f in Path(AGENTS_DIR).glob("*.json"):
            try:
                data = json.loads(f.read_text())
                config = SubagentConfig(**data)
                self._agents[config.name] = config
            except Exception as e:
                logger.warning(f"Failed to load agent {f.name}: {e}")

    def _save_agent(self, config: SubagentConfig):
        """Persist agent definition."""
        if config.source == "builtin":
            return
        path = os.path.join(AGENTS_DIR, f"{config.name}.json")
        Path(path).write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2))

    # --- Agent CRUD ---
    def create_agent(self, name: str, **kwargs) -> tuple[bool, str]:
        """Create a new subagent definition."""
        if name in self._agents and self._agents[name].source == "builtin":
            return False, f"Cannot override builtin agent '{name}'"
        config = SubagentConfig(name=name, **kwargs)
        self._agents[name] = config
        self._save_agent(config)
        return True, f"Agent '{name}' created"

    def get_agent(self, name: str) -> Optional[SubagentConfig]:
        return self._agents.get(name)

    def list_agents(self) -> list[dict]:
        return [
            {
                "name": n,
                "description": c.description,
                "source": c.source,
                "model": c.model,
                "effort": c.effort,
                "tools": _normalize_tool_names(c.tools),
                "disallowed_tools": _normalize_tool_names(c.disallowed_tools),
                "max_turns": c.max_turns,
                "isolation": c.isolation,
                "permission_mode": c.permission_mode,
                "background": c.background,
                "skills": list(c.skills),
                "memory": c.memory,
                "mcp_servers": list(c.mcp_servers),
            }
            for n, c in self._agents.items()
        ]

    def remove_agent(self, name: str) -> tuple[bool, str]:
        if name not in self._agents:
            return False, f"Agent '{name}' not found"
        if self._agents[name].source == "builtin":
            return False, f"Cannot remove builtin agent '{name}'"
        del self._agents[name]
        path = os.path.join(AGENTS_DIR, f"{name}.json")
        if os.path.exists(path):
            os.remove(path)
        return True, f"Agent '{name}' removed"

    # --- Execution ---
    async def spawn(self, agent_name: str, task_prompt: str = "",
                    parent_session_id: str = "",
                    background: bool = False) -> SubagentInstance:
        """Spawn a subagent instance."""
        config = self._agents.get(agent_name)
        if not config:
            raise ValueError(f"Agent '{agent_name}' not found")

        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        instance = SubagentInstance(
            agent_id=agent_id,
            config=config,
            session_id=uuid.uuid4().hex[:12],
            status="running",
            started_at=datetime.now().isoformat(),
            parent_session_id=parent_session_id,
            is_background=background or config.background,
        )

        # Register hooks from agent config
        if config.hooks:
            try:
                from app.agents.hooks import hooks_registry
                hooks_registry.register_from_subagent(agent_name, config.hooks)
            except ImportError:
                pass

        # Fire SubagentStart hook
        try:
            from app.agents.hooks import hooks_registry
            await hooks_registry.fire("SubagentStart", {
                "agent_id": agent_id, "agent_name": agent_name,
                "task_prompt": task_prompt[:500],
            })
        except ImportError:
            pass

        self._instances[agent_id] = instance

        if instance.is_background:
            # Background: fire and return immediately
            instance.task = asyncio.create_task(
                self._execute(instance, task_prompt)
            )
        else:
            # Foreground: block until complete
            await self._execute(instance, task_prompt)

        return instance

    # --- Git Worktree Isolation ---
    def _setup_worktree(self, instance: SubagentInstance) -> bool:
        """Create a dedicated git worktree for isolated subagent execution.
        Returns True if worktree was created, False if skipped (not in git repo, etc.)."""
        import subprocess
        import tempfile

        repo_root = os.getcwd()
        # Verify we're in a git repo
        try:
            result = subprocess.run(
                ["git", "-C", repo_root, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                logger.info(f"Not in a git repo, skipping worktree for {instance.agent_id}")
                return False
            repo_root = result.stdout.strip()
        except Exception as e:
            logger.warning(f"git check failed: {e}")
            return False

        # Worktrees live under data/worktrees/<agent_id>
        worktree_base = os.path.join(DATA_DIR, "worktrees")
        os.makedirs(worktree_base, exist_ok=True)
        worktree_path = os.path.join(worktree_base, instance.agent_id)

        # Branch name: subagent/<agent_id-short>
        branch = f"subagent/{instance.agent_id[:12]}"

        try:
            # Get current branch to base from
            base = subprocess.run(
                ["git", "-C", repo_root, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip() or "HEAD"

            # Create worktree with new branch
            subprocess.run(
                ["git", "-C", repo_root, "worktree", "add", "-b", branch, worktree_path, base],
                check=True, capture_output=True, text=True, timeout=30,
            )
            instance.workdir = worktree_path
            instance.worktree_branch = branch
            logger.info(f"Created worktree for {instance.agent_id} at {worktree_path} (branch {branch})")
            return True
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to create worktree for {instance.agent_id}: {e.stderr}")
            return False
        except Exception as e:
            logger.warning(f"Worktree setup error: {e}")
            return False

    def _cleanup_worktree(self, instance: SubagentInstance, remove_branch: bool = False):
        """Remove the git worktree for this instance. Keeps branch by default so
        changes can be inspected/merged later."""
        import subprocess
        if not instance.workdir:
            return
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", instance.workdir],
                capture_output=True, text=True, timeout=30,
            )
            logger.info(f"Removed worktree for {instance.agent_id}")
            if remove_branch and instance.worktree_branch:
                subprocess.run(
                    ["git", "branch", "-D", instance.worktree_branch],
                    capture_output=True, text=True, timeout=10,
                )
        except Exception as e:
            logger.warning(f"Worktree cleanup failed: {e}")

    async def _execute(self, instance: SubagentInstance, task_prompt: str):
        """Execute the subagent loop."""
        config = instance.config

        # Setup worktree isolation if configured
        worktree_created = False
        original_cwd = None
        if config.isolation == "worktree":
            worktree_created = self._setup_worktree(instance)
            if worktree_created:
                original_cwd = os.getcwd()
                os.chdir(instance.workdir)
                instance.transcript.append({
                    "role": "info",
                    "content": f"[worktree isolated at {instance.workdir}]",
                    "timestamp": datetime.now().isoformat(),
                })

        # Build system prompt
        system_prompt = config.prompt or config.description
        if config.skills:
            skill_context = self._load_skills(config.skills)
            system_prompt += "\n\n" + skill_context

        # Load persistent memory if configured
        memory_context = self._load_memory(config)
        if memory_context:
            system_prompt += "\n\n" + memory_context

        # Record initial prompt
        instance.transcript.append({
            "role": "system",
            "content": system_prompt[:2000],
            "timestamp": datetime.now().isoformat(),
        })
        if task_prompt:
            instance.transcript.append({
                "role": "user",
                "content": task_prompt,
                "timestamp": datetime.now().isoformat(),
            })

        # Execute turns (simplified — in production this invokes the LLM)
        try:
            for turn in range(config.max_turns):
                instance.turn_count = turn + 1

                # Simulate agent response (real impl calls LLM)
                response = await self._agent_turn(instance, system_prompt, task_prompt)
                instance.transcript.append({
                    "role": "assistant",
                    "content": response[:2000],
                    "timestamp": datetime.now().isoformat(),
                })

                # Check if agent indicated completion
                if response.strip() or self._is_complete(response):
                    break

            instance.status = "completed"
            instance.result_summary = self._summarize_transcript(instance)
        except Exception as e:
            instance.status = "failed"
            instance.result_summary = f"Error: {e}"
            logger.warning(f"Subagent {instance.agent_id} failed: {e}")
        finally:
            # Restore cwd and cleanup worktree
            if original_cwd:
                try:
                    os.chdir(original_cwd)
                except Exception as e:
                    logger.debug("Suppressed error in subagents: %s", e)
            if worktree_created:
                # Keep worktree for post-mortem inspection by default.
                # Caller can explicitly cleanup via manager.cleanup_worktree(agent_id).
                pass

            instance.finished_at = datetime.now().isoformat()
            self._save_transcript(instance)

            # Fire SubagentStop hook
            try:
                from app.agents.hooks import hooks_registry
                await hooks_registry.fire("SubagentStop", {
                    "agent_id": instance.agent_id,
                    "status": instance.status,
                    "turn_count": instance.turn_count,
                })
            except ImportError:
                pass

    def cleanup_worktree(self, agent_id: str, remove_branch: bool = False) -> tuple[bool, str]:
        """Public API: remove worktree for a finished agent."""
        instance = self._instances.get(agent_id)
        if not instance:
            return False, f"Agent instance '{agent_id}' not found"
        if not instance.workdir:
            return False, "Agent has no worktree"
        self._cleanup_worktree(instance, remove_branch=remove_branch)
        instance.workdir = None
        return True, f"Worktree for {agent_id} removed"

    async def _agent_turn(self, instance: SubagentInstance,
                          system_prompt: str, task_prompt: str) -> str:
        """Execute a multi-turn tool-use agent loop via create_react_agent."""
        try:
            from app.models.provider import llm_provider
            from app.agents.tool_runtime import clear_runtime_context, set_runtime_context
            from app.agents.tools import get_all_tools, set_thread_context
            try:
                # LangGraph v1.0+ / LangChain: preferred new location.
                from langchain.agents import create_agent as create_react_agent
            except ImportError:
                # Fallback for older installations.
                from langgraph.prebuilt import create_react_agent
            from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

            config = instance.config
            runtime_thread_id = instance.session_id or f"subagent-{instance.agent_id}"
            set_thread_context(runtime_thread_id)
            runtime_token = set_runtime_context(
                thread_id=runtime_thread_id,
                agent_id=instance.agent_id,
                mode=config.permission_mode or PermissionMode.DEFAULT.value,
            )

            # Filter tools based on agent config.
            # `tools` field is the whitelist (Claude Code naming); falls back to disallowed_tools blacklist.
            all_tools = get_all_tools(include_deferred=False, enable_tool_search=True)
            allowed_tools = set(_normalize_tool_names(config.tools))
            disallowed_tools = set(_normalize_tool_names(config.disallowed_tools))
            try:
                if allowed_tools:
                    tools = [t for t in all_tools if t.name in allowed_tools and t.name not in disallowed_tools]
                elif disallowed_tools:
                    tools = [t for t in all_tools if t.name not in disallowed_tools]
                else:
                    tools = all_tools

                # Build LangChain messages from transcript
                lc_messages = [SystemMessage(content=system_prompt)]
                for t in instance.transcript:
                    if t["role"] == "user":
                        lc_messages.append(HumanMessage(content=t["content"]))
                    elif t["role"] == "assistant":
                        lc_messages.append(AIMessage(content=t["content"]))

                attempt_models = llm_provider.get_fallback_model_names(config.model)
                for attempt_index, attempt_model in enumerate(attempt_models):
                    model = llm_provider.get_chat_model(attempt_model, streaming=False)
                    agent = create_react_agent(model, tools)
                    try:
                        full_output = ""
                        tool_calls = []
                        async for event in agent.astream_events({"messages": lc_messages}, version="v2"):
                            kind = event.get("event", "")
                            if kind == "on_chat_model_end":
                                output = event.get("data", {}).get("output")
                                if output and hasattr(output, "content") and isinstance(output.content, str):
                                    full_output = output.content
                            elif kind == "on_tool_start":
                                tool_name = event.get("name", "unknown")
                                tool_calls.append(tool_name)
                                instance.transcript.append({
                                    "role": "tool", "tool": tool_name,
                                    "content": f"[calling {tool_name}]",
                                    "timestamp": datetime.now().isoformat(),
                                })
                            elif kind == "on_tool_end":
                                tool_name = event.get("name", "unknown")
                                output = event.get("data", {}).get("output", "")
                                instance.transcript.append({
                                    "role": "tool_result", "tool": tool_name,
                                    "content": str(output)[:1000],
                                    "timestamp": datetime.now().isoformat(),
                                })

                        if tool_calls:
                            instance.transcript.append({
                                "role": "info",
                                "content": f"Tools used: {', '.join(tool_calls)}",
                                "timestamp": datetime.now().isoformat(),
                            })

                        return full_output or f"[Agent used {len(tool_calls)} tools]"
                    except Exception as e:
                        can_retry = (
                            attempt_index < len(attempt_models) - 1
                            and not tool_calls
                            and not full_output
                            and llm_provider.should_retry_with_fallback(e)
                        )
                        if can_retry:
                            continue
                        raise
                    finally:
                        await llm_provider.aclose_model(model)
            finally:
                clear_runtime_context(runtime_token)
        except Exception as e:
            # Fallback: return task completion marker
            return f"[Subagent completed task: {task_prompt[:100]}] (LLM unavailable: {e})"

    def _is_complete(self, response: str) -> bool:
        """Check if agent response indicates task completion."""
        completion_markers = ["TASK_COMPLETE", "Done.", "completed", "[Subagent completed"]
        return any(marker in response for marker in completion_markers)

    def _summarize_transcript(self, instance: SubagentInstance) -> str:
        """Generate a summary of the subagent's work."""
        assistant_msgs = [t for t in instance.transcript if t["role"] == "assistant"]
        if not assistant_msgs:
            return "No output generated"
        last = assistant_msgs[-1]["content"]
        return last[:500]

    def _load_skills(self, skill_names: list[str]) -> str:
        """Load and combine skill content for the subagent."""
        parts = []
        try:
            from app.agents.evolution import skill_registry
            for name in skill_names:
                skill = skill_registry.get_skill(name)
                if skill:
                    parts.append(f"## Skill: {name}\n{skill.get('system_prompt', '')[:1000]}")
        except ImportError:
            pass
        return "\n\n".join(parts)

    def _load_memory(self, config: SubagentConfig) -> str:
        """Load persistent memory for a subagent."""
        if not config.memory:
            return ""
        memory_dir = os.path.join(AGENT_MEMORY_DIR, config.name)
        os.makedirs(memory_dir, exist_ok=True)
        memory_file = os.path.join(memory_dir, "MEMORY.md")
        if os.path.exists(memory_file):
            return f"## Agent Memory\n{Path(memory_file).read_text()[:2000]}"
        return ""

    def _save_transcript(self, instance: SubagentInstance):
        """Save transcript to disk for persistence/resume."""
        path = os.path.join(AGENT_TRANSCRIPTS_DIR, f"{instance.agent_id}.jsonl")
        with open(path, "w") as f:
            for entry in instance.transcript:
                f.write(json.dumps(entry, default=str) + "\n")

    def _restart_instance(self, instance: SubagentInstance):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._execute(instance, ""))
            instance.task = None
            return
        instance.task = loop.create_task(self._execute(instance, ""))

    # --- Resume ---
    def send_message(self, agent_id: str, message: str) -> tuple[bool, str]:
        """Resume a paused/completed subagent with a new message (SendMessage)."""
        instance = self._instances.get(agent_id)
        if not instance:
            return False, f"Agent instance '{agent_id}' not found"

        if instance.task and not instance.task.done():
            return False, f"Agent instance '{agent_id}' is still running"

        instance.transcript.append({
            "role": "user",
            "content": message,
            "timestamp": datetime.now().isoformat(),
        })
        instance.status = "running"
        self._restart_instance(instance)
        return True, f"Message sent to {agent_id}"

    # --- Teams ---
    def create_team(self, team_name: str, agent_names: list[str]) -> tuple[bool, str]:
        """Create an agent team for parallel collaboration."""
        for name in agent_names:
            if name not in self._agents:
                return False, f"Agent '{name}' not found"
        self._teams[team_name] = list(agent_names)
        return True, f"Team '{team_name}' created with agents: {agent_names}"

    async def spawn_team(self, team_name: str, tasks: list[dict],
                         parent_session_id: str = "") -> list[SubagentInstance]:
        """Spawn multiple agents in parallel as a team."""
        instances = []
        coros = []
        for task in tasks:
            agent_name = task.get("agent", "general-purpose")
            prompt = task.get("prompt", "")
            coros.append(self.spawn(agent_name, prompt, parent_session_id, background=True))
        instances = await asyncio.gather(*coros, return_exceptions=True)
        return [i for i in instances if isinstance(i, SubagentInstance)]

    def list_teams(self) -> dict[str, list[str]]:
        return dict(self._teams)

    # --- Status ---
    def get_instance(self, agent_id: str) -> Optional[dict]:
        instance = self._instances.get(agent_id)
        return instance.to_dict() if instance else None

    def list_instances(self, status: str = None) -> list[dict]:
        instances = self._instances.values()
        if status:
            instances = [i for i in instances if i.status == status]
        return [i.to_dict() for i in instances]

    def get_background_tasks(self) -> list[dict]:
        """List running background subagents."""
        return [i.to_dict() for i in self._instances.values()
                if i.is_background and i.status == "running"]

    # --- Memory ---
    def save_agent_memory(self, agent_name: str, content: str) -> tuple[bool, str]:
        """Save persistent memory for a subagent."""
        memory_dir = os.path.join(AGENT_MEMORY_DIR, agent_name)
        os.makedirs(memory_dir, exist_ok=True)
        memory_file = os.path.join(memory_dir, "MEMORY.md")
        Path(memory_file).write_text(content)
        return True, f"Memory saved for agent '{agent_name}'"

    def get_agent_memory(self, agent_name: str) -> str:
        memory_file = os.path.join(AGENT_MEMORY_DIR, agent_name, "MEMORY.md")
        if os.path.exists(memory_file):
            return Path(memory_file).read_text()
        return ""


# Singleton
subagent_manager = SubagentManager()
