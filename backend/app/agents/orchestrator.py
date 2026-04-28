"""
Multi-Agent Orchestrator — real parallel agents with tools, messaging, and task management.
"""
import os
import json
import asyncio
import uuid
import logging
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
try:
    from langchain.agents import create_agent as create_react_agent
except ImportError:  # pragma: no cover - older langchain fallback
    from langgraph.prebuilt import create_react_agent
from app.models.provider import llm_provider
from app.skills.search import web_search_tool
from app.agents.task_manager import task_manager, TaskStatus
from app.agents.tool_runtime import clear_runtime_context, get_runtime_context, set_runtime_context, summarize_agent_progress
from typing import AsyncGenerator, Callable, Optional

logger = logging.getLogger(__name__)

PLANNER_SYSTEM = """You are a task planner for a multi-agent system. Analyze the user's request and break it into parallel sub-tasks.

Rules:
1. Each sub-task should be INDEPENDENT and able to run in parallel
2. Assign each sub-task a role: researcher, coder, writer, analyst, searcher
3. If the task is simple (answerable in one shot), set needs_planning=false
4. Maximum 5 sub-agents

Respond ONLY in JSON:
{
  "needs_planning": true/false,
  "reasoning": "brief explanation",
  "steps": [
    {"id": 1, "task": "specific task description", "role": "researcher|coder|writer|analyst|searcher", "tools_needed": ["web_search", "execute_python", ...]}
  ]
}"""

SYNTHESIZER_SYSTEM = """You are a synthesis specialist. You receive results from multiple parallel agents and must combine them into a single, coherent, comprehensive response.

Guidelines:
- Merge overlapping information
- Resolve any contradictions
- Organize logically with headers
- Keep the best parts from each agent's work
- Respond in the same language as the original question"""

# --- Bash Safety (hardened) ---
import re as _re

DANGEROUS_BASH_PATTERNS = [
    # Filesystem destruction
    "rm -rf /", "rm -rf ~", "rm -rf /*", "rm -rf .", "rm -rf ..",
    "mkfs.", "dd if=", "dd of=/dev",
    "> /dev/sd", "chmod -R 777 /", ":(){ :|:& };:",
    # Remote code execution
    "curl | sh", "curl | bash", "wget | sh", "wget | bash",
    "eval $(curl", "eval $(wget", "source <(curl", "source <(wget",
    "> /etc/", "shutdown", "reboot",
    # Process destruction
    "kill -9 1", "kill -9 -1", "pkill -9",
    "/dev/null >", "truncate -s 0",
    # Credential/key theft
    "cat ~/.ssh", "cat /etc/shadow", "cat /etc/passwd",
    ".bash_history", ".zsh_history",
    # Fork bomb variants
    "while true; do", "for((;;))",
    # Disk fill
    "yes >", "cat /dev/urandom >", "cat /dev/zero >",
    # Crontab manipulation
    "crontab -r", "crontab -",
]

DESTRUCTIVE_BASH_COMMANDS = {
    "rm", "rmdir", "mv", "dd", "mkfs", "fdisk", "parted",
    "kill", "pkill", "killall", "shutdown", "reboot", "halt",
    "chmod", "chown", "chgrp", "truncate", "shred",
    "ln", "unlink", "mount", "umount", "mknod",
    "useradd", "userdel", "usermod", "groupadd", "groupdel",
    "iptables", "ip6tables", "nft", "ufw",
    "systemctl", "service", "launchctl",
    "crontab", "at",
}

READ_ONLY_BASH_COMMANDS = {
    "ls", "cat", "head", "tail", "grep", "find", "wc", "du", "df",
    "echo", "pwd", "which", "whoami", "date", "env", "printenv",
    "git status", "git log", "git diff", "git branch", "git show",
    "file", "stat", "uname", "ps", "top", "free",
    "id", "groups", "hostname", "uptime", "lsof", "netstat", "ss",
    "dig", "nslookup", "host", "ping", "traceroute",
    "less", "more", "sort", "uniq", "cut", "tr", "tee",
    "md5sum", "sha256sum", "shasum", "cksum",
    "tree", "realpath", "basename", "dirname",
}

ESCALATION_COMMANDS = {"sudo", "su", "doas", "pkexec", "runas"}

ENCODING_BYPASS_PATTERNS = [
    "base64 -d", "base64 --decode", "xxd -r",
    "python -c", "python3 -c", "perl -e", "ruby -e", "node -e",
    "php -r", "lua -e", "awk '", "gawk '",
]

# Sensitive path prefixes that should never be written to
SENSITIVE_PATH_PREFIXES = [
    "/etc/", "/usr/", "/var/log/", "/boot/", "/sys/", "/proc/",
    "/dev/", "/sbin/", "/bin/", "/lib/", "/root/",
    os.path.expanduser("~/.ssh/"),
    os.path.expanduser("~/.gnupg/"),
    os.path.expanduser("~/.aws/"),
    os.path.expanduser("~/.config/"),
]

# Network exfiltration patterns
EXFILTRATION_PATTERNS = [
    _re.compile(r'curl\s+.*-[dX]\s', _re.IGNORECASE),  # curl POST/PUT
    _re.compile(r'curl\s+.*--data', _re.IGNORECASE),
    _re.compile(r'nc\s+-', _re.IGNORECASE),              # netcat
    _re.compile(r'ncat\s', _re.IGNORECASE),
    _re.compile(r'scp\s', _re.IGNORECASE),                # scp upload
    _re.compile(r'rsync\s+.*@', _re.IGNORECASE),          # rsync to remote
]

SHELL_INTERPRETERS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "python", "python3", "perl", "ruby", "node"}

_VARIABLE_EXPANSION_RE = _re.compile(r'\$\{?[A-Za-z_]')
_BACKTICK_RE = _re.compile(r'`[^`]+`')
_SUBSHELL_RE = _re.compile(r'\$\([^)]+\)')


def check_bash_safety(command: str) -> dict:
    """Analyze a bash command for safety with multi-layer checks."""
    cmd_lower = command.lower().strip()
    warnings = []

    # 1. Known dangerous patterns
    for pattern in DANGEROUS_BASH_PATTERNS:
        if pattern in cmd_lower:
            warnings.append(f"Blocked dangerous pattern: {pattern}")
            return {"safe": False, "is_destructive": True, "is_read_only": False, "warnings": warnings}

    # 2. Privilege escalation
    first_cmd = cmd_lower.split()[0].split("/")[-1] if cmd_lower.split() else ""
    if first_cmd in ESCALATION_COMMANDS:
        warnings.append(f"Privilege escalation: {first_cmd}")
        return {"safe": False, "is_destructive": True, "is_read_only": False, "warnings": warnings}

    # 3. Encoding bypass detection (warn)
    for pattern in ENCODING_BYPASS_PATTERNS:
        if pattern in cmd_lower:
            warnings.append(f"Potential encoding bypass: {pattern}")

    # 4. Pipe-to-shell/interpreter
    if "|" in command:
        pipe_target = cmd_lower.split("|")[-1].strip().split()[0].split("/")[-1] if cmd_lower.split("|")[-1].strip() else ""
        if pipe_target in SHELL_INTERPRETERS:
            warnings.append(f"Piping to shell/interpreter: {pipe_target}")
            return {"safe": False, "is_destructive": True, "is_read_only": False, "warnings": warnings}

    # 5. Multi-command chaining — check ALL segments
    chain_ops = _re.split(r'[;&|]+', cmd_lower)
    is_destructive = False
    is_read_only = True
    for segment in chain_ops:
        segment = segment.strip()
        if not segment:
            continue
        seg_cmd = segment.split()[0].split("/")[-1] if segment.split() else ""
        if seg_cmd in ESCALATION_COMMANDS:
            warnings.append(f"Privilege escalation in chain: {seg_cmd}")
            return {"safe": False, "is_destructive": True, "is_read_only": False, "warnings": warnings}
        if seg_cmd in DESTRUCTIVE_BASH_COMMANDS:
            is_destructive = True
            is_read_only = False
            warnings.append(f"Destructive command in chain: {seg_cmd}")
        elif seg_cmd not in READ_ONLY_BASH_COMMANDS:
            is_read_only = False

    # 6. Variable expansion / subshell — warn
    if _VARIABLE_EXPANSION_RE.search(command):
        warnings.append("Variable expansion detected — actual command may differ")
    if _BACKTICK_RE.search(command) or _SUBSHELL_RE.search(command):
        warnings.append("Subshell/backtick detected — actual command may differ")

    # 7. Redirect to sensitive paths
    for prefix in SENSITIVE_PATH_PREFIXES:
        escaped = _re.escape(prefix)
        if _re.search(r'>\s*' + escaped, command):
            warnings.append(f"Redirect to sensitive path: {prefix}")
            return {"safe": False, "is_destructive": True, "is_read_only": False, "warnings": warnings}

    # 8. Network exfiltration detection
    for pattern in EXFILTRATION_PATTERNS:
        if pattern.search(command):
            warnings.append(f"Potential data exfiltration: {pattern.pattern}")

    # 9. sed -i (in-place edit) detection
    if _re.search(r'sed\s+(-[^\s]*)?-i', command) or _re.search(r"sed\s+.*-i\s", command):
        is_destructive = True
        is_read_only = False
        warnings.append("sed in-place edit detected")

    # 10. Hidden file write detection (dotfiles)
    if _re.search(r'>\s*\.', command) or _re.search(r'>>\s*\.', command):
        warnings.append("Writing to hidden/dotfile")

    return {"safe": not is_destructive, "is_destructive": is_destructive, "is_read_only": is_read_only, "warnings": warnings}


# --- Agent Definition System (inspired by Claude Code's AgentDefinition) ---
from dataclasses import dataclass, field


AgentHook = Optional["Callable[[str, dict], None]"]  # (agent_id, context) -> None


@dataclass
class AgentDefinition:
    """Structured agent definition with tool permissions and constraints."""
    agent_type: str
    system_prompt: str
    when_to_use: str = ""
    tools: list[str] | None = None          # whitelist (None = all tools)
    disallowed_tools: list[str] = field(default_factory=list)  # blacklist
    max_turns: int = 15                      # max LLM reasoning loops
    is_read_only: bool = False               # block all write tools
    background: bool = False
    model: str | None = None                 # None = inherit parent model
    timeout_seconds: int = 120               # per-agent execution timeout
    pre_hook: AgentHook = None               # called before agent execution
    post_hook: AgentHook = None              # called after agent execution


# Built-in specialized agents
BUILT_IN_AGENTS: dict[str, AgentDefinition] = {
    "planner": AgentDefinition(
        agent_type="Plan",
        when_to_use="Design implementation plans. Read-only — explores codebase and designs strategy.",
        system_prompt="""You are a planning specialist. Explore the problem and design a step-by-step implementation plan.

=== CRITICAL: READ-ONLY MODE ===
You are STRICTLY PROHIBITED from:
- Creating, modifying, or deleting any files
- Running write commands (git add, git commit, npm install, pip install)
- Using write_file or execute_bash for anything that modifies state

You MAY use: web_search, web_fetch, read_file, list_files, execute_bash (read-only commands like ls, cat, grep, find, git status, git log, git diff)

Provide a structured plan with:
1. Analysis of the problem
2. Step-by-step implementation approach
3. Dependencies and sequencing
4. Potential risks and mitigations""",
        tools=["web_search", "web_fetch", "read_file", "list_files", "execute_bash", "get_current_time"],
        disallowed_tools=["write_file", "execute_python", "create_tool", "create_skill"],
        max_turns=8,
        is_read_only=True,
    ),
    "verifier": AgentDefinition(
        agent_type="Verify",
        when_to_use="Verify and test implementations. Tries to break things rather than confirm they work.",
        system_prompt="""You are a verification specialist. Your job is to try to BREAK the implementation, not confirm it works.

=== CRITICAL: DO NOT MODIFY THE PROJECT ===
You are STRICTLY PROHIBITED from modifying any project files.
You MAY write ephemeral test scripts to /tmp via execute_bash/execute_python.

Verification strategy:
1. Read the code and understand the implementation
2. Run the test suite if one exists
3. Test edge cases, error handling, boundary conditions
4. Try inputs the implementer probably didn't test
5. Check for regressions in related code

Recognize your own rationalizations:
- "The code looks correct" → reading is not verification. Run it.
- "Tests already pass" → verify independently.
- "This is probably fine" → probably is not verified.""",
        tools=["web_search", "web_fetch", "read_file", "list_files", "execute_python", "execute_bash", "get_current_time"],
        disallowed_tools=["write_file", "create_tool", "create_skill"],
        max_turns=12,
        is_read_only=True,
    ),
    "coder": AgentDefinition(
        agent_type="Code",
        when_to_use="Write, test, and debug code. Has full file system and execution access.",
        system_prompt="""You are a coding specialist. Write clean, working code.

Workflow:
1. Understand the requirements fully
2. Write the code
3. Test it by executing
4. Fix any issues
5. Save the final result with write_file

Always test your code before declaring it done.""",
        disallowed_tools=["create_tool", "create_skill", "screenshot", "notify", "open_app"],
        max_turns=20,
    ),
    "researcher": AgentDefinition(
        agent_type="Research",
        when_to_use="Search the web, read sources, and provide thorough, well-cited findings.",
        system_prompt="""You are a research specialist. Search the web, read sources, and provide thorough findings.

Workflow:
1. Search for relevant sources using web_search
2. Read key sources with web_fetch / summarize_url
3. Cross-reference information from multiple sources
4. Synthesize findings with citations""",
        tools=["web_search", "web_fetch", "summarize_url", "read_file", "list_files", "get_current_time", "http_request"],
        max_turns=10,
    ),
    "writer": AgentDefinition(
        agent_type="Write",
        when_to_use="Produce well-structured, professional content. Reports, documentation, articles.",
        system_prompt="""You are a writing specialist. Produce well-structured, professional content.

Guidelines:
- Use clear headings and logical structure
- Be precise and factual
- Match the tone to the audience
- Save long outputs to files with write_file""",
        tools=["web_search", "web_fetch", "read_file", "write_file", "get_current_time"],
        max_turns=8,
    ),
    "analyst": AgentDefinition(
        agent_type="Analyze",
        when_to_use="Analyze data, compute statistics, create visualizations.",
        system_prompt="""You are a data analyst specialist. Analyze data with Python.

Workflow:
1. Understand the data and question
2. Write Python code with pandas/matplotlib/numpy
3. Execute and verify results
4. Present findings clearly with visualizations if appropriate""",
        tools=["execute_python", "read_file", "write_file", "list_files", "web_search", "get_current_time"],
        max_turns=15,
    ),
    "searcher": AgentDefinition(
        agent_type="Search",
        when_to_use="Quick web searches for current information.",
        system_prompt="You are a search agent. Find the most relevant and current information quickly.",
        tools=["web_search", "web_fetch", "summarize_url", "get_current_time"],
        max_turns=5,
    ),
}


def resolve_agent_tools(definition: AgentDefinition, all_tools: list) -> list:
    """Resolve tools for an agent based on whitelist/blacklist."""
    disallowed = set(definition.disallowed_tools)

    if definition.is_read_only:
        disallowed.update({"write_file", "create_tool", "create_skill", "remove_custom_tool"})

    if definition.tools is not None:
        # Whitelist mode: only allow specified tools, minus blacklist
        allowed_names = set(definition.tools) - disallowed
        tools = [t for t in all_tools if t.name in allowed_names]
    else:
        # All tools minus blacklist
        tools = [t for t in all_tools if t.name not in disallowed]

    return tools


class MessageBus:
    """Inter-agent communication channel."""

    def __init__(self):
        self._messages: dict[str, list[dict]] = {}  # agent_id -> inbox
        self._shared_context: dict[str, str] = {}  # key -> value

    def send(self, from_agent: str, to_agent: str, content: str):
        if to_agent not in self._messages:
            self._messages[to_agent] = []
        self._messages[to_agent].append({
            "from": from_agent,
            "content": content,
            "type": "message",
        })

    def broadcast(self, from_agent: str, content: str, agents: list[str]):
        for a in agents:
            if a != from_agent:
                self.send(from_agent, a, content)

    def get_inbox(self, agent_id: str) -> list[dict]:
        return self._messages.get(agent_id, [])

    def set_shared(self, key: str, value: str):
        self._shared_context[key] = value

    def get_shared(self, key: str) -> Optional[str]:
        return self._shared_context.get(key)

    def get_all_shared(self) -> dict[str, str]:
        return dict(self._shared_context)


class SubAgent:
    """A sub-agent with its own LLM, tools, role, and constraints."""

    def __init__(
        self,
        agent_id: str,
        task: str,
        role: str,
        model: str | None = None,
        tools_needed: list[str] | None = None,
        message_bus: MessageBus | None = None,
        definition: AgentDefinition | None = None,
    ):
        self.agent_id = agent_id
        self.task = task
        self.role = role
        self.model = model
        self.tools_needed = tools_needed or []
        self.message_bus = message_bus
        self.definition = definition or BUILT_IN_AGENTS.get(role)
        self.status = "pending"
        self.result = ""
        self.tool_calls: list[dict] = []
        self.token_count = 0
        self.turns_used = 0
        self.summary = summarize_agent_progress(self.task, status=self.status)

    @property
    def max_turns(self) -> int:
        return self.definition.max_turns if self.definition else 15

    @property
    def timeout(self) -> int:
        return self.definition.timeout_seconds if self.definition else 120

    async def execute(self) -> dict:
        self.status = "running"
        hook_ctx = {"task": self.task, "role": self.role, "model": self.model}
        if self.definition and self.definition.pre_hook:
            try:
                self.definition.pre_hook(self.agent_id, hook_ctx)
            except Exception as e:
                logger.warning(f"Pre-hook failed for {self.agent_id}: {e}")
        try:
            self.result = await asyncio.wait_for(
                self._run_with_tools(),
                timeout=self.timeout,
            )
            self.status = "completed"
            self.summary = summarize_agent_progress(self.task, self.tool_calls, status=self.status)
        except asyncio.TimeoutError:
            self.result = f"Error: Agent timed out after {self.timeout}s"
            self.status = "failed"
            self.summary = summarize_agent_progress(self.task, self.tool_calls, status=self.status)
            logger.error(f"SubAgent {self.agent_id} timed out after {self.timeout}s")
        except Exception as e:
            self.result = f"Error: {str(e)}"
            self.status = "failed"
            self.summary = summarize_agent_progress(self.task, self.tool_calls, status=self.status)
            logger.error(f"SubAgent {self.agent_id} failed: {e}")
        if self.definition and self.definition.post_hook:
            try:
                hook_ctx.update({"status": self.status, "turns_used": self.turns_used, "tool_calls": len(self.tool_calls)})
                self.definition.post_hook(self.agent_id, hook_ctx)
            except Exception as e:
                logger.warning(f"Post-hook failed for {self.agent_id}: {e}")
        return self.to_dict()

    async def _run_with_tools(self) -> str:
        from app.agents.tools import get_all_tools, set_thread_context

        set_thread_context(f"agent-{self.agent_id}")
        runtime_token = None
        ctx = get_runtime_context()
        if ctx.thread_id != f"agent-{self.agent_id}" or ctx.agent_id != self.agent_id:
            runtime_token = set_runtime_context(thread_id=f"agent-{self.agent_id}", agent_id=self.agent_id, mode="multi-agent")

        # Build system prompt from definition or fallback
        if self.definition:
            system = self.definition.system_prompt
        else:
            system = f"You are a {self.role} agent. Complete the given task thoroughly."
        system += f"\n\nYou are agent '{self.agent_id}'. Your specific task: {self.task}"
        system += f"\n\nIMPORTANT: You have a maximum of {self.max_turns} reasoning turns. Be efficient."

        # Add shared context from message bus
        if self.message_bus:
            shared = self.message_bus.get_all_shared()
            if shared:
                system += "\n\nShared context from other agents:\n"
                for k, v in shared.items():
                    system += f"- {k}: {v[:200]}\n"

            inbox = self.message_bus.get_inbox(self.agent_id)
            if inbox:
                system += "\n\nMessages from other agents:\n"
                for msg in inbox:
                    system += f"- From {msg['from']}: {msg['content'][:200]}\n"

        # Resolve tools with whitelist/blacklist
        all_tools = get_all_tools(include_deferred=False, enable_tool_search=True)
        if self.definition:
            tools = resolve_agent_tools(self.definition, all_tools)
        elif self.tools_needed:
            tools = [t for t in all_tools if t.name in self.tools_needed]
            if not tools:
                tools = all_tools
        else:
            tools = all_tools

        lc_messages = [
            SystemMessage(content=system),
            HumanMessage(content=self.task),
        ]

        result_content = ""
        self.summary = summarize_agent_progress(self.task, self.tool_calls, status=self.status)
        attempt_models = llm_provider.get_fallback_model_names(self.model)
        try:
            for attempt_index, attempt_model in enumerate(attempt_models):
                chat_model = llm_provider.get_chat_model(attempt_model, streaming=False)
                agent = create_react_agent(chat_model, tools)
                try:
                    response = await agent.ainvoke(
                        {"messages": lc_messages},
                        config={"recursion_limit": self.max_turns * 2},
                    )
                    msgs = response.get("messages", [])
                    self.turns_used = sum(1 for m in msgs if hasattr(m, "tool_calls") and m.tool_calls)
                    for msg in reversed(msgs):
                        if hasattr(msg, "content") and isinstance(msg.content, str) and msg.content:
                            result_content = msg.content
                            break
                    existing_calls = {
                        (call.get("tool", "?"), call.get("args", ""))
                        for call in self.tool_calls
                    }
                    for msg in msgs:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tool_name = tc.get("name", "?")
                                tool_args = str(tc.get("args", ""))[:100]
                                if (tool_name, tool_args) in existing_calls:
                                    continue
                                existing_calls.add((tool_name, tool_args))
                                self.tool_calls.append({"tool": tool_name, "args": tool_args})
                    self.summary = summarize_agent_progress(self.task, self.tool_calls, status="completed")
                    break
                except Exception as e:
                    can_retry = (
                        attempt_index < len(attempt_models) - 1
                        and not self.tool_calls
                        and not result_content
                        and llm_provider.should_retry_with_fallback(e)
                    )
                    if can_retry:
                        logger.warning("SubAgent %s tool agent failed on %s, retrying with fallback model: %s", self.agent_id, attempt_model, e)
                        continue
                    logger.warning(f"SubAgent {self.agent_id} tool agent failed, falling back: {e}")
                    chat_model_simple = llm_provider.get_chat_model(attempt_model, streaming=False)
                    try:
                        response = await chat_model_simple.ainvoke(lc_messages)
                        result_content = response.content if hasattr(response, "content") else str(response)
                        self.summary = summarize_agent_progress(self.task, self.tool_calls, status=self.status)
                        break
                    except Exception as e2:
                        can_retry_simple = (
                            attempt_index < len(attempt_models) - 1
                            and not self.tool_calls
                            and not result_content
                            and llm_provider.should_retry_with_fallback(e2)
                        )
                        if can_retry_simple:
                            logger.warning("SubAgent %s no-tools fallback failed on %s, retrying with fallback model: %s", self.agent_id, attempt_model, e2)
                            continue
                        raise
                    finally:
                        await llm_provider.aclose_model(chat_model_simple)
                finally:
                    await llm_provider.aclose_model(chat_model)
        finally:
            if runtime_token is not None:
                clear_runtime_context(runtime_token)

        # Share result to message bus
        if self.message_bus:
            self.message_bus.set_shared(
                f"result_{self.agent_id}",
                result_content[:500]
            )

        return result_content

    def to_dict(self) -> dict:
        d = {
            "agent_id": self.agent_id,
            "task": self.task,
            "role": self.role,
            "status": self.status,
            "result": self.result[:3000],
            "tool_calls": self.tool_calls[-10:],
            "turns_used": self.turns_used,
            "max_turns": self.max_turns,
            "summary": self.summary,
        }
        if self.definition:
            from app.agents.tools import get_all_tools

            d["agent_type"] = self.definition.agent_type
            d["is_read_only"] = self.definition.is_read_only
            d["allowed_tools"] = len(resolve_agent_tools(self.definition, get_all_tools(include_deferred=False, enable_tool_search=True)))
        return d


class MultiAgentOrchestrator:
    def __init__(self, max_parallel: int = 5):
        self.max_parallel = max_parallel

    async def plan(self, task: str, model: str | None = None) -> dict:
        chat_model = llm_provider.get_chat_model(model, streaming=False)
        response = await chat_model.ainvoke([
            SystemMessage(content=PLANNER_SYSTEM),
            HumanMessage(content=task),
        ])
        content = response.content if hasattr(response, "content") else str(response)
        try:
            if "{" in content:
                start = content.index("{")
                end = content.rindex("}") + 1
                return json.loads(content[start:end])
        except (json.JSONDecodeError, ValueError):
            pass
        return {"needs_planning": False, "reasoning": "Failed to parse plan", "steps": []}

    async def execute_parallel(
        self,
        steps: list[dict],
        model: str | None = None,
        original_task: str = "",
    ) -> AsyncGenerator[dict, None]:
        # Budget check
        from app.agents.cost_tracker import cost_tracker
        if cost_tracker.is_over_budget():
            yield {"type": "error", "content": f"Budget exceeded (${cost_tracker.get_budget_status()['spent']:.4f} / ${cost_tracker.get_budget_status()['limit']:.4f})"}
            return

        message_bus = MessageBus()

        agents = []
        for i, step in enumerate(steps):
            role = step.get("role", step.get("skill", "researcher"))
            definition = BUILT_IN_AGENTS.get(role)
            agents.append(SubAgent(
                agent_id=f"agent-{i+1}",
                task=step["task"],
                role=role,
                model=model,
                tools_needed=step.get("tools_needed", []),
                message_bus=message_bus,
                definition=definition,
            ))

        yield {
            "type": "plan",
            "data": {
                "total": len(agents),
                "steps": [{"id": a.agent_id, "task": a.task, "role": a.role} for a in agents],
            },
        }

        # Create background tasks
        semaphore = asyncio.Semaphore(self.max_parallel)
        status_queue: asyncio.Queue = asyncio.Queue()

        async def run_agent(agent: SubAgent):
            async with semaphore:
                await status_queue.put({
                    "type": "agent_status",
                    "data": {"agent_id": agent.agent_id, "status": "running", "task": agent.task, "role": agent.role},
                })
                await status_queue.put({
                    "type": "agent_summary",
                    "data": {"agent_id": agent.agent_id, "summary": summarize_agent_progress(agent.task, status="running")},
                })
                runtime_token = set_runtime_context(thread_id=f"agent-{agent.agent_id}", agent_id=agent.agent_id, mode="multi-agent")
                # Register as background task
                bg_task = task_manager.create_task(
                    name=f"{agent.role}: {agent.task[:50]}",
                    description=agent.task,
                    agent_id=agent.agent_id,
                )
                await task_manager.update_task(
                    bg_task.task_id,
                    status=TaskStatus.RUNNING,
                    progress=0.05,
                    summary=agent.summary,
                    metadata={"role": agent.role, "task": agent.task, "tool_calls": []},
                    log_message="Agent started",
                )
                stop_summary = asyncio.Event()

                async def pump_summary():
                    last_count = 0
                    while not stop_summary.is_set():
                        try:
                            await asyncio.wait_for(stop_summary.wait(), timeout=8.0)
                        except asyncio.TimeoutError:
                            ctx = get_runtime_context()
                            latest_calls = ctx.tool_calls[last_count:]
                            if latest_calls:
                                for call in latest_calls:
                                    agent.tool_calls.append({
                                        "tool": call.get("tool", "?"),
                                        "args": str(call.get("input", ""))[:100],
                                    })
                                last_count = len(ctx.tool_calls)
                            agent.summary = summarize_agent_progress(agent.task, agent.tool_calls, status=agent.status)
                            estimated_progress = min(0.9, 0.15 + (0.12 * len(agent.tool_calls)) + (0.08 * agent.turns_used))
                            await task_manager.update_task(
                                bg_task.task_id,
                                progress=estimated_progress,
                                summary=agent.summary,
                                metadata={
                                    "role": agent.role,
                                    "task": agent.task,
                                    "tool_calls": agent.tool_calls[-10:],
                                    "status": agent.status,
                                },
                                log_message=f"Summary tick: {agent.summary}",
                            )
                            await status_queue.put({
                                "type": "agent_summary",
                                "data": {
                                    "agent_id": agent.agent_id,
                                    "summary": agent.summary,
                                    "tool_calls": agent.tool_calls[-5:],
                                    "progress": estimated_progress,
                                },
                            })

                summary_task = asyncio.create_task(pump_summary())
                try:
                    result = await agent.execute()
                    stop_summary.set()
                    await summary_task
                    await task_manager.update_task(
                        bg_task.task_id,
                        status=TaskStatus.COMPLETED,
                        result=agent.result[:500],
                        progress=1.0,
                        summary=agent.summary,
                        metadata={"role": agent.role, "task": agent.task, "tool_calls": agent.tool_calls[-10:], "status": agent.status},
                        log_message="Agent completed",
                    )
                    await status_queue.put({
                        "type": "agent_status",
                        "data": {
                            "agent_id": agent.agent_id,
                            "status": "completed",
                            "tool_calls": agent.tool_calls[-5:],
                            "result_preview": agent.result[:200],
                        },
                    })
                    await status_queue.put({
                        "type": "agent_summary",
                        "data": {"agent_id": agent.agent_id, "summary": agent.summary, "tool_calls": agent.tool_calls[-5:], "progress": 1.0},
                    })
                    return agent
                except Exception as e:
                    agent.status = "failed"
                    agent.result = str(e)
                    agent.summary = summarize_agent_progress(agent.task, agent.tool_calls, status="failed")
                    stop_summary.set()
                    await summary_task
                    await task_manager.update_task(
                        bg_task.task_id,
                        status=TaskStatus.FAILED,
                        error=str(e),
                        progress=1.0,
                        summary=agent.summary,
                        metadata={"role": agent.role, "task": agent.task, "tool_calls": agent.tool_calls[-10:], "status": agent.status},
                        log_message=f"Agent failed: {str(e)[:120]}",
                    )
                    await status_queue.put({
                        "type": "agent_status",
                        "data": {"agent_id": agent.agent_id, "status": "failed", "error": str(e)[:100]},
                    })
                    await status_queue.put({
                        "type": "agent_summary",
                        "data": {"agent_id": agent.agent_id, "summary": agent.summary, "tool_calls": agent.tool_calls[-5:], "progress": 1.0},
                    })
                    return agent
                finally:
                    clear_runtime_context(runtime_token)

        # Launch all agents in parallel
        gather_future = asyncio.ensure_future(
            asyncio.gather(*[run_agent(a) for a in agents], return_exceptions=True)
        )

        # Stream status updates as they come
        while not gather_future.done():
            try:
                event = await asyncio.wait_for(status_queue.get(), timeout=0.3)
                yield event
            except asyncio.TimeoutError:
                continue

        # Drain remaining events
        while not status_queue.empty():
            yield await status_queue.get()

        # Compile results
        completed = [a for a in agents if a.status == "completed"]
        failed = [a for a in agents if a.status == "failed"]

        yield {
            "type": "agents_completed",
            "data": {
                "completed": len(completed),
                "failed": len(failed),
                "total": len(agents),
                "results": [a.to_dict() for a in agents],
            },
        }

    async def synthesize(
        self, original_task: str, agent_results: list[dict], model: str | None = None
    ) -> AsyncGenerator[str, None]:
        """Stream the final synthesis of all agent results."""
        parts = []
        for r in agent_results:
            status_icon = "✅" if r["status"] == "completed" else "❌"
            tools_used = ", ".join(tc["tool"] for tc in r.get("tool_calls", []))
            parts.append(
                f"{status_icon} **{r['agent_id']}** ({r['role']})"
                + (f" [tools: {tools_used}]" if tools_used else "")
                + f"\n{r['result'][:1500]}"
            )

        combined = "\n\n---\n\n".join(parts)
        chat_model = llm_provider.get_chat_model(model, streaming=True)
        lc_messages = [
            SystemMessage(content=SYNTHESIZER_SYSTEM),
            HumanMessage(content=f"Original task: {original_task}\n\n## Agent Results:\n\n{combined}\n\nPlease synthesize a comprehensive final answer."),
        ]

        try:
            async for chunk in chat_model.astream(lc_messages):
                if hasattr(chunk, "content") and chunk.content:
                    yield chunk.content
        except Exception as e:
            yield f"\n\n**Synthesis error:** {str(e)}"


orchestrator = MultiAgentOrchestrator()
