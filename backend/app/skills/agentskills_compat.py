"""
agentskills.io / Claude Code SKILL.md compatibility layer.

Loads skills from the standard `SKILL.md` + YAML frontmatter format used by:
  - agentskills.io marketplace
  - Claude Code custom skills
  - Hermes ~/.hermes/skills/

SKILL.md format:
    ---
    name: my-skill
    description: What this skill does and when to trigger it
    license: MIT
    metadata:
      author: someone
      version: "1.0.0"
    ---
    # Skill Title
    
    Skill content becomes the system prompt...

Discovery paths (in priority order):
  1. ~/.hermes/skills/<name>/SKILL.md
  2. .hermes/skills/<name>/SKILL.md
  3. Custom path via HERMES_SKILLS_DIR env
"""
import os
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class AgentSkillManifest:
    """agentskills.io-compatible skill manifest."""
    name: str
    description: str = ""
    system_prompt: str = ""
    license: str = ""
    version: str = "1.0.0"
    author: str = ""
    category: str = ""
    keywords: list[str] = field(default_factory=list)
    homepage: str = ""
    repository: str = ""
    references: list[str] = field(default_factory=list)
    source_path: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.name.replace("-", " ").title(),
            "description": self.description,
            "system_prompt": self.system_prompt,
            "license": self.license,
            "version": self.version,
            "author": self.author,
            "category": self.category,
            "keywords": self.keywords,
            "source_path": self.source_path,
        }

    def to_skill_config(self) -> dict:
        """Convert to our internal SkillConfig-compatible dict."""
        return {
            "name": self.name,
            "display_name": self.name.replace("-", " ").title(),
            "description": self.description,
            "system_prompt": self.system_prompt,
        }


# ---------------------------------------------------------------------------
# SKILL.md parser
# ---------------------------------------------------------------------------

def parse_skill_md(content: str) -> tuple[dict, str]:
    """Parse SKILL.md: extract YAML frontmatter and body.
    
    Returns (frontmatter_dict, body_text).
    """
    def _coerce_value(raw: str):
        value = raw.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return value.strip("'\"")

    def _next_significant(lines: list[str], start: int) -> tuple[int, str] | None:
        for idx in range(start + 1, len(lines)):
            candidate = lines[idx]
            stripped = candidate.strip()
            if not stripped or stripped.startswith("#"):
                continue
            return idx, candidate
        return None

    def _fallback_yaml_parse(yaml_text: str) -> dict:
        parsed: dict = {}
        lines = yaml_text.split("\n")
        stack: list[tuple[int, object]] = [(-1, parsed)]
        for index, raw_line in enumerate(lines):
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            stripped = raw_line.strip()
            while len(stack) > 1 and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1]
            if stripped.startswith("- "):
                if isinstance(parent, list):
                    parent.append(_coerce_value(stripped[2:]))
                continue
            if ":" not in stripped or not isinstance(parent, dict):
                continue
            key, _, raw_value = stripped.partition(":")
            key = key.strip()
            value = raw_value.strip()
            if value:
                parent[key] = _coerce_value(value)
                continue
            next_item = _next_significant(lines, index)
            next_indent = len(next_item[1]) - len(next_item[1].lstrip(" ")) if next_item else -1
            if next_item and next_indent > indent and next_item[1].strip().startswith("- "):
                parent[key] = []
            else:
                parent[key] = {}
            stack.append((indent, parent[key]))
        return parsed

    frontmatter = {}
    body = content

    # Match --- ... --- block
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', content, re.DOTALL)
    if match:
        yaml_text = match.group(1)
        body = match.group(2)

        try:
            import yaml  # type: ignore
            parsed = yaml.safe_load(yaml_text) or {}
            frontmatter = parsed if isinstance(parsed, dict) else {}
        except Exception as e:
            logger.debug("Suppressed error in agentskills_compat: %s", e)
            frontmatter = _fallback_yaml_parse(yaml_text)

        metadata = frontmatter.get("metadata", {})
        if isinstance(metadata, dict):
            if "author" in metadata and "author" not in frontmatter:
                frontmatter["author"] = metadata.get("author", "")
            if "version" in metadata and "version" not in frontmatter:
                frontmatter["version"] = metadata.get("version", "1.0.0")

        keywords = frontmatter.get("keywords", [])
        if isinstance(keywords, str):
            frontmatter["keywords"] = [v.strip().strip("'\"") for v in keywords.split(",") if v.strip()]

    return frontmatter, body


def load_skill_md(skill_dir: str) -> Optional[AgentSkillManifest]:
    """Load a single skill from a directory containing SKILL.md."""
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None

    try:
        content = Path(skill_md).read_text(encoding="utf-8", errors="replace")
        fm, body = parse_skill_md(content)

        # Collect references
        refs = []
        ref_dir = os.path.join(skill_dir, "references")
        if os.path.isdir(ref_dir):
            for f in sorted(os.listdir(ref_dir)):
                if f.endswith(".md"):
                    refs.append(os.path.join(ref_dir, f))

        name = fm.get("name", os.path.basename(skill_dir))
        description = fm.get("description", "")

        # Build system prompt: body + references
        system_prompt = body.strip()
        for ref_path in refs[:10]:  # limit to 10 reference files
            try:
                ref_content = Path(ref_path).read_text(encoding="utf-8", errors="replace")
                ref_name = os.path.basename(ref_path)
                system_prompt += f"\n\n## Reference: {ref_name}\n{ref_content[:5000]}"
            except Exception as e:
                logger.debug("Suppressed error in agentskills_compat: %s", e)

        limited_refs = refs[:10]

        return AgentSkillManifest(
            name=name,
            description=description,
            system_prompt=system_prompt[:50000],
            license=fm.get("license", ""),
            version=fm.get("version", "1.0.0"),
            author=fm.get("author", ""),
            category=fm.get("category", ""),
            keywords=fm.get("keywords", []) if isinstance(fm.get("keywords"), list) else [],
            homepage=fm.get("homepage", ""),
            repository=fm.get("repository", ""),
            references=[os.path.basename(r) for r in limited_refs],
            source_path=skill_dir,
        )
    except Exception as e:
        logger.warning("Failed to load SKILL.md from %s: %s", skill_dir, e)
        return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _get_skill_search_paths() -> list[Path]:
    """Skill discovery paths."""
    paths = [
        Path.home() / ".hermes" / "skills",
        Path.home() / ".claude" / "skills",
        Path.cwd() / ".hermes" / "skills",
        Path.cwd() / ".claude" / "skills",
    ]
    extra = os.environ.get("HERMES_SKILLS_DIR")
    if extra:
        paths.append(Path(extra))
    claude_extra = os.environ.get("CLAUDE_SKILLS_DIR")
    if claude_extra:
        paths.append(Path(claude_extra))
    return paths


def discover_agentskills() -> list[AgentSkillManifest]:
    """Discover all SKILL.md-based skills from search paths."""
    found: list[AgentSkillManifest] = []
    seen_names: set[str] = set()

    for root in _get_skill_search_paths():
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            skill = load_skill_md(str(entry))
            if skill and skill.name not in seen_names:
                seen_names.add(skill.name)
                found.append(skill)

    return found


def discover_and_register():
    """Discover agentskills.io-compatible skills and register them into the skill registry."""
    skills = discover_agentskills()
    if not skills:
        return 0

    try:
        from app.skills.base import SkillWorkflow, skill_registry
        count = 0
        for s in skills:
            if skill_registry.get(s.name):
                continue  # already registered
            workflow = SkillWorkflow(
                name=s.name,
                display_name=s.name.replace("-", " ").title(),
                description=s.description,
                system_prompt=s.system_prompt,
                steps=[],
            )
            skill_registry.register(workflow)
            count += 1
        logger.info("Registered %d agentskills.io skills", count)
        return count
    except Exception as e:
        logger.warning("Failed to register agentskills: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Export skill in agentskills.io format
# ---------------------------------------------------------------------------

def export_skill_md(name: str, description: str, system_prompt: str,
                    author: str = "", version: str = "1.0.0",
                    license: str = "MIT") -> str:
    """Export a skill to SKILL.md format (agentskills.io compatible)."""
    frontmatter = f"""---
name: {name}
description: '{description}'
license: {license}
metadata:
  author: {author}
  version: "{version}"
---
"""
    return frontmatter + "\n" + system_prompt
