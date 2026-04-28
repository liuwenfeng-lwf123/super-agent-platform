"""
Self-Evolution Engine — Agent can create, register, and manage custom tools & skills at runtime.
Inspired by Hermes self-improvement and OpenClaw's skill-creator.
"""
import os
import json
import importlib
import importlib.util
import logging
import ast
import re
import subprocess
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional
from langchain_core.tools import tool, StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
CUSTOM_TOOLS_DIR = os.path.join(DATA_DIR, "custom_tools")
CUSTOM_SKILLS_DIR = os.path.join(DATA_DIR, "custom_skills")
EVOLUTION_LOG = os.path.join(DATA_DIR, "evolution_log.json")

os.makedirs(CUSTOM_TOOLS_DIR, exist_ok=True)
os.makedirs(CUSTOM_SKILLS_DIR, exist_ok=True)


class ToolRegistry:
    """Dynamic registry for custom tools created by the agent."""

    def __init__(self):
        self._custom_tools: dict[str, StructuredTool] = {}
        self._tool_metadata: dict[str, dict] = {}
        self._load_persisted_tools()

    def _load_persisted_tools(self):
        """Load previously created tools from disk on startup."""
        for f in Path(CUSTOM_TOOLS_DIR).glob("*.py"):
            try:
                meta_path = f.with_suffix(".json")
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text())
                    code = f.read_text()
                    self._register_from_code(meta["name"], meta["description"], code, persist=False)
                    logger.info(f"Loaded custom tool: {meta['name']}")
            except Exception as e:
                logger.warning(f"Failed to load custom tool {f.name}: {e}")

    def _validate_code(self, code: str) -> tuple[bool, str]:
        """Validate tool code is safe and well-formed."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Syntax error: {e}"

        # Check for dangerous imports
        dangerous = {"subprocess", "shutil.rmtree", "os.system", "os.remove", "os.rmdir"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in dangerous:
                        return False, f"Forbidden import: {alias.name}"
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module in dangerous:
                    return False, f"Forbidden import: {node.module}"

        # Must have a function called 'run'
        has_run = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "run":
                    has_run = True
                    break
        if not has_run:
            return False, "Tool code must define a function called 'run(...)' that implements the tool logic."

        return True, "OK"

    def _register_from_code(self, name: str, description: str, code: str, persist: bool = True) -> tuple[bool, str]:
        """Register a tool from Python code string."""
        valid, msg = self._validate_code(code)
        if not valid:
            return False, msg

        try:
            # Execute code in isolated namespace
            namespace = {"__builtins__": __builtins__}
            exec(code, namespace)

            run_fn = namespace.get("run")
            if not run_fn:
                return False, "No 'run' function found in code."

            # Create LangChain tool
            lc_tool = StructuredTool.from_function(
                func=run_fn,
                name=name,
                description=description,
                return_direct=False,
            )

            self._custom_tools[name] = lc_tool
            self._tool_metadata[name] = {
                "name": name,
                "description": description,
                "code": code,
                "created_by": "agent",
            }

            if persist:
                # Save to disk
                tool_path = os.path.join(CUSTOM_TOOLS_DIR, f"{name}.py")
                meta_path = os.path.join(CUSTOM_TOOLS_DIR, f"{name}.json")
                Path(tool_path).write_text(code)
                Path(meta_path).write_text(json.dumps(self._tool_metadata[name], ensure_ascii=False, indent=2))
                self._log_evolution("create_tool", name, description)

            return True, f"Tool '{name}' registered successfully."
        except Exception as e:
            return False, f"Failed to register tool: {e}"

    def remove_tool(self, name: str) -> tuple[bool, str]:
        if name not in self._custom_tools:
            return False, f"Tool '{name}' not found."
        del self._custom_tools[name]
        del self._tool_metadata[name]
        # Remove from disk
        for ext in [".py", ".json"]:
            p = Path(CUSTOM_TOOLS_DIR) / f"{name}{ext}"
            if p.exists():
                p.unlink()
        self._log_evolution("remove_tool", name, "")
        return True, f"Tool '{name}' removed."

    def get_custom_tools(self) -> list:
        return list(self._custom_tools.values())

    def list_custom_tools(self) -> list[dict]:
        return [
            {"name": m["name"], "description": m["description"]}
            for m in self._tool_metadata.values()
        ]

    def _log_evolution(self, action: str, name: str, description: str):
        """Log evolution events."""
        log = []
        if os.path.exists(EVOLUTION_LOG):
            try:
                log = json.loads(Path(EVOLUTION_LOG).read_text())
            except Exception as e:
                logger.debug("Suppressed error in evolution: %s", e)
        log.append({
            "action": action,
            "name": name,
            "description": description,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep last 200 entries
        log = log[-200:]
        Path(EVOLUTION_LOG).write_text(json.dumps(log, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Skill Security Scanner
# ---------------------------------------------------------------------------
SKILL_THREAT_PATTERNS = [
    # Prompt injection
    r"ignore.*previous.*instructions",
    r"you\s+are\s+now",
    r"system\s*:\s*",
    r"<\|im_start\|>",
    r"\[INST\]",
    # Credential exfiltration
    r"(api[_-]?key|password|secret|token)\s*[:=]",
    r"curl.*\|\s*bash",
    r"wget.*\|\s*sh",
    # SSH/system backdoors
    r"authorized_keys",
    r"crontab",
    r"\.ssh/",
    # Invisible unicode
    r"[\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]",
]


def _scan_skill_security(content: str) -> tuple[bool, list[str]]:
    """Scan skill content for injection/exfiltration threats. Returns (safe, threats)."""
    import re
    threats = []
    for pattern in SKILL_THREAT_PATTERNS:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            threats.append(f"Pattern matched: {pattern[:50]}")
    return len(threats) == 0, threats


def _scan_memory_security(content: str) -> tuple[bool, list[str]]:
    """Scan memory content for injection/exfiltration threats."""
    return _scan_skill_security(content)  # Same patterns apply


class SkillRegistry:
    """Registry for custom skills (prompt templates + tool combos).
    Supports create/patch/edit/delete with security scanning,
    version history for rollback, and progressive disclosure."""

    MAX_VERSIONS = 20
    DESCRIPTION_BUDGET = 1024

    @property
    def SKILL_VERSION_DIR(self) -> str:
        """Computed dynamically so test patches of CUSTOM_SKILLS_DIR are honored."""
        import app.agents.evolution as _mod
        return os.path.join(_mod.CUSTOM_SKILLS_DIR, "_versions")

    def __init__(self):
        self._custom_skills: dict[str, dict] = {}
        os.makedirs(self.SKILL_VERSION_DIR, exist_ok=True)
        self._load_persisted_skills()

    def _load_persisted_skills(self):
        import app.agents.evolution as _mod
        skills_dir = _mod.CUSTOM_SKILLS_DIR
        for f in Path(skills_dir).glob("*.json"):
            if f.parent.name == "_versions":
                continue
            try:
                skill = json.loads(f.read_text())
                self._custom_skills[skill["name"]] = skill
                logger.info(f"Loaded custom skill: {skill['name']}")
            except Exception as e:
                logger.warning(f"Failed to load skill {f.name}: {e}")

    def _save_skill(self, skill: dict):
        import app.agents.evolution as _mod
        path = os.path.join(_mod.CUSTOM_SKILLS_DIR, f"{skill['name']}.json")
        Path(path).write_text(json.dumps(skill, ensure_ascii=False, indent=2))

    def _save_version(self, name: str, skill: dict):
        """Save a version snapshot for rollback."""
        os.makedirs(self.SKILL_VERSION_DIR, exist_ok=True)
        version_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        version_path = os.path.join(self.SKILL_VERSION_DIR, f"{name}_{version_id}.json")
        Path(version_path).write_text(json.dumps(skill, ensure_ascii=False, indent=2))
        # Trim old versions
        versions = sorted(Path(self.SKILL_VERSION_DIR).glob(f"{name}_*.json"))
        for old in versions[:-self.MAX_VERSIONS]:
            old.unlink()

    # --- Maturity levels (singularity-claude pattern) ---
    MATURITY_LEVELS = ["draft", "tested", "hardened", "crystallized"]
    MATURITY_THRESHOLDS = {
        "tested": {"min_runs": 3, "min_avg_score": 60},
        "hardened": {"min_runs": 5, "min_avg_score": 80},
        "crystallized": {"min_runs": 5, "min_avg_score": 90},
    }

    def create_skill(self, name: str, display_name: str, description: str,
                     system_prompt: str, tools: list[str] | None = None,
                     allowed_tools: list[str] | None = None,
                     config: dict | None = None,
                     model: str | None = None,
                     effort: str | None = None,
                     disable_model_invocation: bool = False,
                     user_invocable: bool = True,
                     argument_hint: str = "",
                     paths: str | None = None,
                     context: str | None = None,
                     required_environment_variables: list[dict] | None = None,
                     category: str = "general",
                     ) -> tuple[bool, str]:
        # Security scan
        safe, threats = _scan_skill_security(system_prompt)
        if not safe:
            return False, f"Skill blocked by security scan: {'; '.join(threats)}"
        # Description budget
        if len(description) > self.DESCRIPTION_BUDGET:
            description = description[:self.DESCRIPTION_BUDGET]
        skill = {
            "name": name,
            "display_name": display_name,
            "description": description,
            "system_prompt": system_prompt,
            "tools": tools or [],
            "allowed_tools": allowed_tools,  # None = all tools allowed
            "config": config or {},
            "enabled": True,
            "built_in": False,
            "version": 1,
            "maturity": "draft",
            "run_count": 0,
            "scores": [],
            "files": {},  # attached files: {relative_path: content}
            "trust_level": "local",  # local / official / trusted / community
            "model": model,          # per-skill model override (e.g. "haiku", "sonnet")
            "effort": effort,        # per-skill effort (low/medium/high/xhigh/max)
            "disable_model_invocation": disable_model_invocation,
            "user_invocable": user_invocable,
            "argument_hint": argument_hint,
            "paths": paths,          # path-scoped activation
            "context": context,      # "fork" for subagent execution
            "required_environment_variables": required_environment_variables or [],
            "category": category,
        }
        self._custom_skills[name] = skill
        self._save_skill(skill)
        self._save_version(name, skill)
        self._log_evolution("create_skill", name, description[:60])
        return True, f"Skill '{display_name}' created (v1, maturity=draft)."

    def patch_skill(self, name: str, old_string: str, new_string: str) -> tuple[bool, str]:
        """Patch a skill's system_prompt by replacing old_string with new_string (incremental edit)."""
        skill = self._custom_skills.get(name)
        if not skill:
            return False, f"Skill '{name}' not found."
        if old_string not in skill["system_prompt"]:
            return False, f"old_string not found in skill '{name}' system_prompt."
        # Security scan new content
        new_prompt = skill["system_prompt"].replace(old_string, new_string, 1)
        safe, threats = _scan_skill_security(new_prompt)
        if not safe:
            return False, f"Patch blocked by security scan: {'; '.join(threats)}"
        self._save_version(name, skill)  # backup before patch
        skill["system_prompt"] = new_prompt
        skill["version"] = skill.get("version", 1) + 1
        self._save_skill(skill)
        self._log_evolution("patch_skill", name, f"v{skill['version']}")
        return True, f"Skill '{name}' patched (v{skill['version']})."

    def edit_skill(self, name: str, new_content: str) -> tuple[bool, str]:
        """Full replace of a skill's system_prompt."""
        skill = self._custom_skills.get(name)
        if not skill:
            return False, f"Skill '{name}' not found."
        safe, threats = _scan_skill_security(new_content)
        if not safe:
            return False, f"Edit blocked by security scan: {'; '.join(threats)}"
        self._save_version(name, skill)
        skill["system_prompt"] = new_content
        skill["version"] = skill.get("version", 1) + 1
        self._save_skill(skill)
        self._log_evolution("edit_skill", name, f"v{skill['version']}")
        return True, f"Skill '{name}' updated (v{skill['version']})."

    def rollback_skill(self, name: str) -> tuple[bool, str]:
        """Rollback skill to previous version."""
        versions = sorted(Path(self.SKILL_VERSION_DIR).glob(f"{name}_*.json"))
        if len(versions) < 1:
            return False, f"No versions found for skill '{name}'."
        prev = versions[-1]
        try:
            skill = json.loads(prev.read_text())
            self._custom_skills[name] = skill
            self._save_skill(skill)
            prev.unlink()  # remove the used version
            self._log_evolution("rollback_skill", name, f"rolled back")
            return True, f"Skill '{name}' rolled back to v{skill.get('version', '?')}."
        except Exception as e:
            return False, f"Rollback failed: {e}"

    def remove_skill(self, name: str) -> tuple[bool, str]:
        if name not in self._custom_skills:
            return False, f"Skill '{name}' not found."
        self._save_version(name, self._custom_skills[name])  # backup before delete
        del self._custom_skills[name]
        import app.agents.evolution as _mod
        path = Path(_mod.CUSTOM_SKILLS_DIR) / f"{name}.json"
        if path.exists():
            path.unlink()
        self._log_evolution("remove_skill", name, "")
        return True, f"Skill '{name}' removed."

    # --- Progressive Disclosure (3-level loading) ---
    def list_skills_level0(self) -> list[dict]:
        """Level 0: name + description only (low token cost)."""
        return [
            {"name": s["name"], "display_name": s.get("display_name", s["name"]),
             "description": s.get("description", "")[:200], "category": s.get("category", "general")}
            for s in self._custom_skills.values()
        ]

    def view_skill_level1(self, name: str) -> Optional[dict]:
        """Level 1: full content + metadata."""
        return self._custom_skills.get(name)

    def view_skill_level2(self, name: str, section: str = "system_prompt") -> Optional[str]:
        """Level 2: specific section only."""
        skill = self._custom_skills.get(name)
        if skill:
            return skill.get(section)
        return None

    def list_skills(self) -> list[dict]:
        return list(self._custom_skills.values())

    def get_skill(self, name: str) -> Optional[dict]:
        return self._custom_skills.get(name)

    def get_versions(self, name: str) -> list[str]:
        """List available versions for rollback."""
        versions = sorted(Path(self.SKILL_VERSION_DIR).glob(f"{name}_*.json"))
        return [v.stem.replace(f"{name}_", "") for v in versions]

    # --- Skill Maturity: scoring, promotion, crystallize ---
    def record_score(self, name: str, score: int) -> dict:
        """Record a score (0-100) for a skill execution and auto-promote maturity."""
        skill = self._custom_skills.get(name)
        if not skill:
            return {"error": f"Skill '{name}' not found"}
        skill.setdefault("scores", [])
        skill.setdefault("run_count", 0)
        skill["scores"].append(score)
        skill["run_count"] = skill.get("run_count", 0) + 1
        # Keep last 50 scores
        if len(skill["scores"]) > 50:
            skill["scores"] = skill["scores"][-50:]
        # Auto-promote maturity
        old_maturity = skill.get("maturity", "draft")
        new_maturity = self._compute_maturity(skill)
        skill["maturity"] = new_maturity
        self._save_skill(skill)
        promoted = new_maturity != old_maturity
        if promoted:
            self._log_evolution("maturity_promote", name, f"{old_maturity}→{new_maturity}")
        return {
            "score": score,
            "avg_score": self._avg_score(skill),
            "run_count": skill["run_count"],
            "maturity": new_maturity,
            "promoted": promoted,
        }

    def _avg_score(self, skill: dict) -> float:
        scores = skill.get("scores", [])
        return round(sum(scores) / max(1, len(scores)), 1) if scores else 0

    def _compute_maturity(self, skill: dict) -> str:
        """Compute maturity level based on runs and avg score."""
        if skill.get("maturity") == "crystallized":
            return "crystallized"  # immutable once crystallized
        runs = skill.get("run_count", 0)
        avg = self._avg_score(skill)
        for level in ["crystallized", "hardened", "tested"]:
            thresh = self.MATURITY_THRESHOLDS.get(level, {})
            if runs >= thresh.get("min_runs", 999) and avg >= thresh.get("min_avg_score", 999):
                return level
        return "draft"

    def crystallize_skill(self, name: str) -> tuple[bool, str]:
        """Lock a skill as immutable production-grade version (git-tag equivalent)."""
        skill = self._custom_skills.get(name)
        if not skill:
            return False, f"Skill '{name}' not found"
        if self._avg_score(skill) < 90:
            return False, f"Cannot crystallize: avg score {self._avg_score(skill)} < 90"
        if skill.get("run_count", 0) < 5:
            return False, f"Cannot crystallize: only {skill.get('run_count', 0)} runs (need 5+)"
        self._save_version(name, skill)
        skill["maturity"] = "crystallized"
        self._save_skill(skill)
        self._log_evolution("crystallize_skill", name, f"v{skill.get('version', 1)} locked")
        return True, f"Skill '{name}' crystallized (immutable)."

    def needs_repair(self, name: str) -> bool:
        """Check if a skill needs auto-repair (avg score < 50, 2+ runs)."""
        skill = self._custom_skills.get(name)
        if not skill:
            return False
        return skill.get("run_count", 0) >= 2 and self._avg_score(skill) < 50

    def get_maturity_report(self) -> list[dict]:
        """Get maturity status of all skills."""
        return [
            {
                "name": s["name"],
                "maturity": s.get("maturity", "draft"),
                "run_count": s.get("run_count", 0),
                "avg_score": self._avg_score(s),
                "trust_level": s.get("trust_level", "local"),
            }
            for s in self._custom_skills.values()
        ]

    # --- Skill Files: write_file / remove_file ---
    def write_skill_file(self, name: str, file_path: str, file_content: str) -> tuple[bool, str]:
        """Attach a supporting file to a skill (Hermes pattern)."""
        skill = self._custom_skills.get(name)
        if not skill:
            return False, f"Skill '{name}' not found"
        if skill.get("maturity") == "crystallized":
            return False, f"Skill '{name}' is crystallized (immutable)"
        safe, threats = _scan_skill_security(file_content)
        if not safe:
            return False, f"File blocked by security scan: {'; '.join(threats)}"
        skill.setdefault("files", {})
        self._save_version(name, skill)
        skill["files"][file_path] = file_content
        self._save_skill(skill)
        self._log_evolution("write_skill_file", name, f"file: {file_path}")
        return True, f"File '{file_path}' attached to skill '{name}'."

    def remove_skill_file(self, name: str, file_path: str) -> tuple[bool, str]:
        """Remove an attached file from a skill."""
        skill = self._custom_skills.get(name)
        if not skill:
            return False, f"Skill '{name}' not found"
        if skill.get("maturity") == "crystallized":
            return False, f"Skill '{name}' is crystallized (immutable)"
        files = skill.get("files", {})
        if file_path not in files:
            return False, f"File '{file_path}' not found in skill '{name}'"
        self._save_version(name, skill)
        del files[file_path]
        self._save_skill(skill)
        self._log_evolution("remove_skill_file", name, f"removed: {file_path}")
        return True, f"File '{file_path}' removed from skill '{name}'."

    def list_skill_files(self, name: str) -> list[str]:
        """List all attached files for a skill."""
        skill = self._custom_skills.get(name)
        if not skill:
            return []
        return list(skill.get("files", {}).keys())

    # --- Trust Levels (Hermes 5-level) ---
    TRUST_LEVELS = ["builtin", "official", "trusted", "community", "dangerous"]

    def set_trust_level(self, name: str, level: str) -> tuple[bool, str]:
        """Set trust level for a skill."""
        skill = self._custom_skills.get(name)
        if not skill:
            return False, f"Skill '{name}' not found"
        if level not in self.TRUST_LEVELS:
            return False, f"Invalid trust level. Must be one of: {', '.join(self.TRUST_LEVELS)}"
        skill["trust_level"] = level
        self._save_skill(skill)
        return True, f"Trust level for '{name}' set to '{level}'."

    # --- Allowed Tools enforcement ---
    def get_allowed_tools(self, name: str) -> list[str] | None:
        """Get allowed tools list for a skill. None = all tools allowed."""
        skill = self._custom_skills.get(name)
        if not skill:
            return None
        return skill.get("allowed_tools")

    # --- Skill Config ---
    def get_skill_config(self, name: str) -> dict:
        """Get config settings for a skill."""
        skill = self._custom_skills.get(name)
        if not skill:
            return {}
        return skill.get("config", {})

    def set_skill_config(self, name: str, key: str, value: str) -> tuple[bool, str]:
        """Set a config value for a skill."""
        skill = self._custom_skills.get(name)
        if not skill:
            return False, f"Skill '{name}' not found"
        skill.setdefault("config", {})[key] = value
        self._save_skill(skill)
        return True, f"Config '{key}' set for skill '{name}'."

    def _log_evolution(self, action: str, name: str, description: str):
        log = []
        if os.path.exists(EVOLUTION_LOG):
            try:
                log = json.loads(Path(EVOLUTION_LOG).read_text())
            except Exception as e:
                logger.debug("Suppressed error in evolution: %s", e)
        log.append({"action": action, "name": name, "description": description,
                    "timestamp": datetime.now().isoformat()})
        log = log[-200:]
        Path(EVOLUTION_LOG).write_text(json.dumps(log, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Skill Shell Execution (Claude Code !`command` pattern)
# ---------------------------------------------------------------------------
SHELL_PATTERN = re.compile(r'!`([^`]+)`')
SHELL_BLOCK_PATTERN = re.compile(r'```!\n(.*?)```', re.DOTALL)


def execute_skill_shell(content: str, timeout: int = 10) -> str:
    """Process a skill's content, executing !`command` and ```! blocks.
    Returns the content with command outputs substituted in."""
    def _run_cmd(cmd: str) -> str:
        try:
            result = subprocess.run(
                ["bash", "-c", cmd], shell=False, capture_output=True, text=True, timeout=timeout
            )
            return result.stdout.strip() or result.stderr.strip()
        except subprocess.TimeoutExpired:
            return f"[command timed out: {cmd[:50]}]"
        except Exception as e:
            return f"[command failed: {e}]"

    # Replace !`command` inline
    def _replace_inline(match):
        return _run_cmd(match.group(1))
    content = SHELL_PATTERN.sub(_replace_inline, content)

    # Replace ```! blocks
    def _replace_block(match):
        commands = match.group(1).strip().split("\n")
        outputs = [_run_cmd(cmd.strip()) for cmd in commands if cmd.strip()]
        return "\n".join(outputs)
    content = SHELL_BLOCK_PATTERN.sub(_replace_block, content)

    return content


# ---------------------------------------------------------------------------
# Skill context:fork — Sub-Agent Execution (Claude Code pattern)
# ---------------------------------------------------------------------------
class SkillForkExecutor:
    """Execute a skill in an isolated sub-agent context.
    Results are summarized and returned to the main conversation."""

    def execute_in_fork(self, skill_name: str, arguments: str = "",
                        agent_type: str = "general-purpose") -> dict:
        """Run a skill in a forked sub-agent context."""
        skill = skill_registry.get_skill(skill_name) if skill_registry else None
        if not skill:
            return {"error": f"Skill '{skill_name}' not found"}

        # Build the forked context
        prompt = skill.get("system_prompt", "")
        if arguments:
            prompt = prompt.replace("$ARGUMENTS", arguments)

        # Execute shell commands if present
        prompt = execute_skill_shell(prompt)

        # Inject learnings
        try:
            from app.agents.learning_loop import learnings_loop
            learnings_ctx = learnings_loop.get_skill_context(skill_name)
            if learnings_ctx:
                prompt += learnings_ctx
        except ImportError:
            pass

        return {
            "skill": skill_name,
            "agent_type": agent_type,
            "rendered_prompt": prompt,
            "allowed_tools": skill.get("allowed_tools"),
            "forked": True,
        }


# ---------------------------------------------------------------------------
# Skills Hub — Online Registry (Hermes pattern)
# ---------------------------------------------------------------------------
SKILLS_HUB_DIR = os.path.join(DATA_DIR, "skills_hub")
os.makedirs(SKILLS_HUB_DIR, exist_ok=True)

SKILLS_HUB_LOCK = os.path.join(SKILLS_HUB_DIR, "lock.json")


class SkillsHub:
    """Manage remote skill installation, updates, and trust scanning (Hermes pattern)."""

    KNOWN_SOURCES = {
        "official": "https://github.com/official/skills",
        "community": "https://skills.sh",
    }

    def __init__(self):
        self._lock = self._load_lock()

    def _load_lock(self) -> dict:
        if os.path.exists(SKILLS_HUB_LOCK):
            try:
                return json.loads(Path(SKILLS_HUB_LOCK).read_text())
            except Exception as e:
                logger.debug("Suppressed error in evolution: %s", e)
        return {"installed": {}, "quarantine": []}

    def _save_lock(self):
        Path(SKILLS_HUB_LOCK).write_text(json.dumps(self._lock, ensure_ascii=False, indent=2))

    def install_from_json(self, skill_data: dict, source: str = "community",
                          force: bool = False) -> tuple[bool, str]:
        """Install a skill from parsed JSON data."""
        name = skill_data.get("name")
        if not name:
            return False, "Skill data missing 'name'"

        content = skill_data.get("system_prompt", "")
        safe, threats = _scan_skill_security(content)

        # Trust level assignment
        trust = "official" if source == "official" else "community"
        if not safe and not force:
            self._lock.setdefault("quarantine", []).append({
                "name": name, "source": source, "threats": threats,
                "timestamp": datetime.now().isoformat(),
            })
            self._save_lock()
            return False, f"Skill quarantined: {'; '.join(threats)}. Use force=True to override."

        # Install via skill_registry
        ok, msg = skill_registry.create_skill(
            name=name,
            display_name=skill_data.get("display_name", name),
            description=skill_data.get("description", ""),
            system_prompt=content,
            tools=skill_data.get("tools", []),
            allowed_tools=skill_data.get("allowed_tools"),
            config=skill_data.get("config"),
            model=skill_data.get("model"),
            effort=skill_data.get("effort"),
            disable_model_invocation=skill_data.get("disable_model_invocation", False),
            user_invocable=skill_data.get("user_invocable", True),
            required_environment_variables=skill_data.get("required_environment_variables", []),
            category=skill_data.get("category", "general"),
        )
        if ok:
            skill = skill_registry.get_skill(name)
            if skill:
                skill["trust_level"] = trust
                skill["source"] = skill_data.get("source", source)
                skill["source_url"] = skill_data.get("source_url", "")
                skill["tags"] = skill_data.get("tags", [])
                skill["license"] = skill_data.get("license", "")
                skill["homepage"] = skill_data.get("homepage", "")
                skill["repository"] = skill_data.get("repository", "")
                skill["required_binaries"] = skill_data.get("required_binaries", [])
                skill["clawhub_slug"] = skill_data.get("clawhub_slug", "")
                skill["clawhub_version"] = skill_data.get("clawhub_version", "")
                if isinstance(skill_data.get("files"), dict):
                    skill["files"] = skill_data.get("files", {})
                skill_registry._save_skill(skill)

            self._lock["installed"][name] = {
                "source": source,
                "trust": trust,
                "installed_at": datetime.now().isoformat(),
                "content_hash": hashlib.md5(content.encode()).hexdigest(),
            }
            self._save_lock()
        return ok, msg

    def check_updates(self) -> list[dict]:
        """Check which installed hub skills have upstream changes."""
        # In a real implementation this would check GitHub API
        # For now, return installed skills info
        return [
            {"name": name, "source": info.get("source", ""), "installed_at": info.get("installed_at", "")}
            for name, info in self._lock.get("installed", {}).items()
        ]

    def get_quarantined(self) -> list[dict]:
        """List quarantined skills that failed security scan."""
        return self._lock.get("quarantine", [])

    def list_installed(self) -> list[dict]:
        """List all hub-installed skills."""
        return [
            {"name": name, **info}
            for name, info in self._lock.get("installed", {}).items()
        ]


# ---------------------------------------------------------------------------
# Skill Category Directory Support (Hermes pattern)
# ---------------------------------------------------------------------------
SKILL_CATEGORIES_DIR = os.path.join(CUSTOM_SKILLS_DIR, "_categories")
os.makedirs(SKILL_CATEGORIES_DIR, exist_ok=True)


def organize_skill_by_category(skill: dict):
    """Create a category symlink/index for organized skill browsing."""
    category = skill.get("category", "general")
    cat_dir = os.path.join(SKILL_CATEGORIES_DIR, category)
    os.makedirs(cat_dir, exist_ok=True)
    # Write a lightweight index file
    index_path = os.path.join(cat_dir, f"{skill['name']}.json")
    Path(index_path).write_text(json.dumps({
        "name": skill["name"],
        "display_name": skill.get("display_name", skill["name"]),
        "description": skill.get("description", ""),
    }, ensure_ascii=False, indent=2))


def list_skill_categories() -> dict[str, list[str]]:
    """List all skills organized by category."""
    categories = {}
    if not os.path.isdir(SKILL_CATEGORIES_DIR):
        return categories
    for cat_dir in Path(SKILL_CATEGORIES_DIR).iterdir():
        if cat_dir.is_dir():
            skills = [f.stem for f in cat_dir.glob("*.json")]
            if skills:
                categories[cat_dir.name] = skills
    return categories


# Singletons
tool_registry = ToolRegistry()
skill_registry = SkillRegistry()
skill_fork_executor = SkillForkExecutor()
skills_hub = SkillsHub()


# ===== Meta-tools: tools that create other tools =====

@tool
def create_tool(name: str, description: str, code: str) -> str:
    """Create a new custom tool that the agent can use in future conversations.
    The code must define a function called 'run(...)' with typed parameters and a docstring.

    Example code:
    ```
    import requests

    def run(city: str) -> str:
        \"\"\"Get weather for a city.\"\"\"
        resp = requests.get(f"https://wttr.in/{city}?format=3")
        return resp.text
    ```

    Args:
        name: Tool name (snake_case, e.g. 'get_weather')
        description: What the tool does (shown to the AI)
        code: Python code with a 'run(...)' function
    """
    ok, msg = tool_registry._register_from_code(name, description, code)
    return msg


@tool
def list_custom_tools() -> str:
    """List all custom tools that have been created by the agent."""
    tools = tool_registry.list_custom_tools()
    if not tools:
        return "No custom tools have been created yet."
    lines = ["Custom tools:"]
    for t in tools:
        lines.append(f"  - {t['name']}: {t['description']}")
    return "\n".join(lines)


@tool
def remove_custom_tool(name: str) -> str:
    """Remove a custom tool by name."""
    ok, msg = tool_registry.remove_tool(name)
    return msg


@tool
def create_skill(name: str, display_name: str, description: str, system_prompt: str) -> str:
    """Create a new skill template that defines a specialized behavior mode.
    Skills are like personas with specific instructions.

    Args:
        name: Skill ID (snake_case, e.g. 'code_reviewer')
        display_name: Display name (e.g. 'Code Reviewer')
        description: When to use this skill (be "pushy" — list trigger conditions, edge cases, synonyms)
        system_prompt: Detailed instructions for the AI when this skill is active
    """
    ok, msg = skill_registry.create_skill(name, display_name, description, system_prompt)
    return msg


@tool
def patch_skill(name: str, old_string: str, new_string: str) -> str:
    """Patch a skill's system_prompt by replacing old_string with new_string.
    This is the preferred way to incrementally improve skills — more token-efficient than full edit.

    Args:
        name: Skill ID to patch
        old_string: Text to find in the skill's system_prompt
        new_string: Replacement text
    """
    ok, msg = skill_registry.patch_skill(name, old_string, new_string)
    return msg


@tool
def edit_skill(name: str, new_content: str) -> str:
    """Fully replace a skill's system_prompt with new content.
    Use patch_skill for small changes; use this for major rewrites.

    Args:
        name: Skill ID to edit
        new_content: New system_prompt content
    """
    ok, msg = skill_registry.edit_skill(name, new_content)
    return msg


@tool
def rollback_skill(name: str) -> str:
    """Rollback a skill to its previous version.

    Args:
        name: Skill ID to rollback
    """
    ok, msg = skill_registry.rollback_skill(name)
    return msg


@tool
def list_custom_skills() -> str:
    """List all custom skills created by the agent (Level 0: names + descriptions only)."""
    skills = skill_registry.list_skills_level0()
    if not skills:
        return "No custom skills have been created yet."
    lines = ["Custom skills:"]
    for s in skills:
        lines.append(f"  - {s['display_name']}: {s['description']}")
    return "\n".join(lines)


@tool
def view_skill(name: str, section: str = "") -> str:
    """View a skill's full content (Level 1) or a specific section (Level 2).

    Args:
        name: Skill ID to view
        section: Optional section name (e.g. 'system_prompt', 'description'). Empty = full skill.
    """
    if section:
        content = skill_registry.view_skill_level2(name, section)
        if content is None:
            return f"Skill '{name}' not found or section '{section}' does not exist."
        return f"[{name}] {section}:\n{content}"
    skill = skill_registry.view_skill_level1(name)
    if not skill:
        return f"Skill '{name}' not found."
    lines = [f"Skill: {skill.get('display_name', name)} (v{skill.get('version', 1)})"]
    lines.append(f"Description: {skill.get('description', '')}")
    lines.append(f"System Prompt ({len(skill.get('system_prompt', ''))} chars):")
    lines.append(skill.get('system_prompt', '')[:2000])
    return "\n".join(lines)


@tool
def view_evolution_log() -> str:
    """View the self-evolution history — all tools and skills that were created, patched, or removed."""
    if not os.path.exists(EVOLUTION_LOG):
        return "No evolution events yet."
    log = json.loads(Path(EVOLUTION_LOG).read_text())
    if not log:
        return "No evolution events yet."
    lines = [f"Evolution log ({len(log)} events):"]
    for e in log[-20:]:
        lines.append(f"  [{e['timestamp'][:16]}] {e['action']}: {e['name']} — {e['description'][:60]}")
    return "\n".join(lines)


@tool
def score_skill(name: str, score: int) -> str:
    """Record a score (0-100) after using a skill. Used for maturity tracking.
    Skills auto-promote through maturity levels: draft → tested → hardened → crystallized.

    Args:
        name: Skill ID to score
        score: Quality score 0-100
    """
    result = skill_registry.record_score(name, score)
    if "error" in result:
        return result["error"]
    promoted = " (PROMOTED!)" if result.get("promoted") else ""
    return f"Score recorded: {score}/100. Avg={result['avg_score']}, runs={result['run_count']}, maturity={result['maturity']}{promoted}"


@tool
def write_skill_file(name: str, file_path: str, file_content: str) -> str:
    """Attach a supporting file to a skill (e.g. reference docs, templates, scripts).

    Args:
        name: Skill ID
        file_path: Relative path within the skill (e.g. 'references/api.md')
        file_content: File content
    """
    ok, msg = skill_registry.write_skill_file(name, file_path, file_content)
    return msg


@tool
def remove_skill_file(name: str, file_path: str) -> str:
    """Remove an attached file from a skill.

    Args:
        name: Skill ID
        file_path: Relative path of the file to remove
    """
    ok, msg = skill_registry.remove_skill_file(name, file_path)
    return msg


@tool
def record_skill_feedback(name: str, feedback: str, context: str = "") -> str:
    """Record user feedback/correction for a skill (Learnings Loop).
    Accumulated learnings are auto-injected into the skill context on next use.

    Args:
        name: Skill ID that needs improvement
        feedback: The correction or improvement (e.g. "Use port 2222, not 22 for staging")
        context: Optional context about when/why this feedback was given
    """
    from app.agents.learning_loop import learnings_loop
    result = learnings_loop.record_feedback(name, feedback, context)
    if not result.get("recorded"):
        return f"Not recorded: {result.get('reason', 'unknown')}"
    return f"Feedback recorded for skill '{name}'. Total learnings: {result.get('total_bytes', 0)} bytes."


@tool
def gepa_evolve(prompt: str, eval_description: str = "", generations: int = 3) -> str:
    """Run GEPA (Genetic-Pareto) evolution on a prompt/skill to improve it.

    Args:
        prompt: The original prompt or skill content to evolve
        eval_description: Description of what a good result looks like
        generations: Number of evolution generations (default 3)
    """
    from app.agents.self_evolution import gepa_engine
    eval_cases = [{"category": "quality", "expected_behavior": eval_description}] if eval_description else []
    result = gepa_engine.evolve(prompt, eval_cases, population_size=6, generations=generations)
    best = result.get("best", {})
    return (f"Evolution complete. Baseline: {result['baseline_score']:.3f} → Best: {result['best_score']:.3f} "
            f"(+{result['improvement']:.3f}). Pareto front: {result['pareto_front_size']} candidates.\n"
            f"Best content preview: {best.get('content', '')[:500]}")


@tool
def spawn_agent(agent_name: str, task_prompt: str, background: bool = False) -> str:
    """Spawn a subagent (explore/plan/general-purpose or custom) to perform a task.

    Args:
        agent_name: Name of the agent to spawn (e.g. 'explore', 'plan', 'general-purpose')
        task_prompt: The task for the subagent to perform
        background: If True, run in background and return immediately
    """
    import asyncio
    from app.agents.subagents import subagent_manager
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, subagent_manager.spawn(agent_name, task_prompt, background=background))
                instance = future.result(timeout=60)
        else:
            instance = loop.run_until_complete(subagent_manager.spawn(agent_name, task_prompt, background=background))
    except Exception as e:
        return f"Failed to spawn agent: {e}"
    return f"Agent '{agent_name}' spawned (id={instance.agent_id}, status={instance.status}). Summary: {instance.result_summary[:300]}"


@tool
def send_agent_message(agent_id: str, message: str) -> str:
    """Send a follow-up message to a running/paused subagent (resume).

    Args:
        agent_id: The agent instance ID to send the message to
        message: The message content
    """
    from app.agents.subagents import subagent_manager
    ok, msg = subagent_manager.send_message(agent_id, message)
    return msg


@tool
def register_hook(event: str, command: str, name: str = "", handler_type: str = "command") -> str:
    """Register a lifecycle hook that fires on agent events (PreToolUse, PostToolUse, Stop, etc).

    Args:
        event: Hook event name (PreToolUse, PostToolUse, Stop, SessionStart, etc)
        command: The command/prompt/URL to execute when the hook fires
        name: Optional name for the hook
        handler_type: Type of handler (command, prompt, http)
    """
    from app.agents.hooks import hooks_registry, HookDefinition, HookHandler
    hook = HookDefinition(
        event=event, name=name or f"hook_{event}",
        handlers=[HookHandler(handler_type=handler_type, command=command)],
    )
    ok, msg = hooks_registry.register(hook)
    return msg


@tool
def execute_code_tool(code: str, language: str = "python") -> str:
    """Execute code in a sandboxed environment. Supports Python and Bash.

    Args:
        code: The code to execute
        language: Programming language (python or bash)
    """
    import asyncio
    from app.agents.self_evolution import execute_code as _exec
    try:
        result = asyncio.run(_exec(code, language, timeout=30))
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _exec(code, language, timeout=30))
            result = future.result(timeout=35)
    output = result.get("output", "")
    error = result.get("error", "")
    return f"[{result['status']}] {output}" + (f"\nERROR: {error}" if error else "")


@tool
def elicit_input(title: str, fields_json: str) -> str:
    """Request structured input from the user via an elicitation form.

    Args:
        title: Title of the input request
        fields_json: JSON array of fields, e.g. [{"name":"env","type":"select","label":"Env","options":["dev","prod"]}]
    """
    import json as _json
    from app.agents.self_evolution import elicitation_manager
    from dataclasses import asdict
    try:
        fields = _json.loads(fields_json)
    except Exception as e:
        logger.debug("Suppressed error in evolution: %s", e)
        return "Invalid fields JSON"
    req = elicitation_manager.create_request(title, fields)
    return f"Elicitation created (id={req.elicitation_id}). Waiting for user response."


EVOLUTION_TOOLS = [
    create_tool, list_custom_tools, remove_custom_tool,
    create_skill, patch_skill, edit_skill, rollback_skill,
    list_custom_skills, view_skill, view_evolution_log,
    score_skill, write_skill_file, remove_skill_file, record_skill_feedback,
    gepa_evolve, spawn_agent, send_agent_message, register_hook,
    execute_code_tool, elicit_input,
]
