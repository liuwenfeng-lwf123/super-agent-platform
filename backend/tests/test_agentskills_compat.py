import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.skills.agentskills_compat import discover_agentskills, load_skill_md, parse_skill_md


class TestAgentSkillsCompatFocused(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="agentskills_compat_")
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmp)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_skill(self, root: Path, name: str, frontmatter: str, body: str, refs: dict[str, str] | None = None) -> Path:
        skill_dir = root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
        if refs:
            ref_dir = skill_dir / "references"
            ref_dir.mkdir(parents=True, exist_ok=True)
            for filename, content in refs.items():
                (ref_dir / filename).write_text(content, encoding="utf-8")
        return skill_dir

    def test_parse_skill_md_flattens_metadata_and_normalizes_keywords(self):
        content = """---
name: compatibility-skill
description: 'Compatibility checks'
keywords: alpha, beta , gamma
metadata:
  author: tester
  version: "2.3.4"
---

# Skill Body

Keep markdown intact.
"""
        frontmatter, body = parse_skill_md(content)

        self.assertEqual(frontmatter["name"], "compatibility-skill")
        self.assertEqual(frontmatter["author"], "tester")
        self.assertEqual(frontmatter["version"], "2.3.4")
        self.assertEqual(frontmatter["keywords"], ["alpha", "beta", "gamma"])
        self.assertIn("# Skill Body", body)
        self.assertIn("Keep markdown intact.", body)

    def test_parse_skill_md_fallback_parser_handles_lists_and_nested_metadata(self):
        content = """---
name: fallback-skill
keywords:
  - alpha
  - beta
metadata:
  author: fallback-user
  version: "1.2.0"
---

Fallback body.
"""
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("yaml unavailable")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            frontmatter, body = parse_skill_md(content)

        self.assertEqual(frontmatter["keywords"], ["alpha", "beta"])
        self.assertEqual(frontmatter["author"], "fallback-user")
        self.assertEqual(frontmatter["version"], "1.2.0")
        self.assertEqual(body.strip(), "Fallback body.")

    def test_load_skill_md_includes_sorted_references_and_limits_to_ten(self):
        skill_root = Path(self._tmp) / ".hermes" / "skills"
        refs = {f"ref{i:02d}.md": f"Reference {i}" for i in range(12)}
        refs["ignore.txt"] = "not included"
        skill_dir = self._write_skill(
            skill_root,
            "reference-skill",
            "name: reference-skill\ndescription: Reference-heavy skill",
            "Primary body.",
            refs=refs,
        )

        skill = load_skill_md(str(skill_dir))

        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "reference-skill")
        self.assertEqual(skill.source_path, str(skill_dir))
        self.assertEqual(len(skill.references), 10)
        self.assertEqual(skill.references[0], "ref00.md")
        self.assertEqual(skill.references[-1], "ref09.md")
        self.assertIn("Primary body.", skill.system_prompt)
        self.assertIn("## Reference: ref00.md", skill.system_prompt)
        self.assertIn("Reference 0", skill.system_prompt)
        self.assertNotIn("## Reference: ref10.md", skill.system_prompt)
        self.assertNotIn("ignore.txt", skill.references)

    def test_discover_agentskills_respects_priority_and_dedupes_names(self):
        home_root = Path(self._tmp) / "home"
        extra_root = Path(self._tmp) / "extra_skills"
        cwd_skills = Path(self._tmp) / ".hermes" / "skills"
        home_skills = home_root / ".hermes" / "skills"

        self._write_skill(
            home_skills,
            "shared-skill",
            "name: shared-skill\ndescription: home version",
            "home body",
        )
        self._write_skill(
            cwd_skills,
            "shared-skill",
            "name: shared-skill\ndescription: cwd version",
            "cwd body",
        )
        self._write_skill(
            extra_root,
            "extra-skill",
            "name: extra-skill\ndescription: extra version",
            "extra body",
        )

        with patch("app.skills.agentskills_compat.Path.home", return_value=home_root), patch.dict(
            os.environ,
            {"HERMES_SKILLS_DIR": str(extra_root)},
            clear=False,
        ):
            skills = discover_agentskills()

        by_name = {skill.name: skill for skill in skills}
        self.assertIn("shared-skill", by_name)
        self.assertIn("extra-skill", by_name)
        self.assertEqual(by_name["shared-skill"].description, "home version")
        self.assertEqual(by_name["shared-skill"].system_prompt.strip(), "home body")
        self.assertEqual(by_name["extra-skill"].description, "extra version")


if __name__ == "__main__":
    unittest.main()
