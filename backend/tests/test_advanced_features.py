"""Tests for advanced features: Hooks, Subagents, GEPA, Plugin, SKILL.md,
Secure Setup, External Dirs, SOUL.md, Prompt Cache, Cron, Elicitation, execute_code."""
import os
import json
import tempfile
import asyncio
import unittest
from pathlib import Path
from dataclasses import asdict

# --- Hooks ---
from app.agents.hooks import (
    HookEvent, HookMatcher, HookHandler, HookDefinition, HookResult,
    HookExecutor, HooksRegistry,
)

# --- Subagents ---
from app.agents.subagents import (
    SubagentConfig, SubagentManager, BUILTIN_AGENTS, PermissionMode,
)

# --- Self-Evolution Advanced ---
from app.agents.self_evolution import (
    SemanticPreservation, ParetoSelector, GEPAEngine, MutationEngine,
    FitnessEvaluator, PluginRegistry, CronManager, ElicitationManager,
    parse_skill_md, render_skill_md, check_skill_env_requirements,
    scan_external_skill_dirs, load_soul, save_soul,
    inject_cache_breakpoints, execute_code,
)

import app.agents.evolution as evo_mod


class EvoTestBase(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_skills = evo_mod.CUSTOM_SKILLS_DIR
        self._old_log = evo_mod.EVOLUTION_LOG
        evo_mod.CUSTOM_SKILLS_DIR = os.path.join(self._tempdir.name, "skills")
        evo_mod.EVOLUTION_LOG = os.path.join(self._tempdir.name, "evo.json")
        os.makedirs(evo_mod.CUSTOM_SKILLS_DIR, exist_ok=True)

    def tearDown(self):
        evo_mod.CUSTOM_SKILLS_DIR = self._old_skills
        evo_mod.EVOLUTION_LOG = self._old_log
        self._tempdir.cleanup()


# ============================================================
# Hooks System
# ============================================================
class TestHookEvent(unittest.TestCase):
    def test_all_events_defined(self):
        events = [e.value for e in HookEvent]
        self.assertIn("PreToolUse", events)
        self.assertIn("PostToolUse", events)
        self.assertIn("Stop", events)
        self.assertIn("SessionStart", events)
        self.assertIn("SubagentStart", events)
        self.assertGreaterEqual(len(events), 20)


class TestHookMatcher(unittest.TestCase):
    def test_tool_name_match(self):
        m = HookMatcher(tool_name="Bash")
        self.assertTrue(m.matches({"tool_name": "Bash"}))
        self.assertFalse(m.matches({"tool_name": "Write"}))

    def test_tool_pattern_match(self):
        m = HookMatcher(tool_name_pattern="mcp__.*")
        self.assertTrue(m.matches({"tool_name": "mcp__github"}))
        self.assertFalse(m.matches({"tool_name": "Bash"}))

    def test_file_pattern_match(self):
        m = HookMatcher(file_pattern="*.py")
        self.assertTrue(m.matches({"file_path": "test.py"}))
        self.assertFalse(m.matches({"file_path": "test.js"}))

    def test_empty_matcher(self):
        m = HookMatcher()
        self.assertTrue(m.matches({}))


class TestHooksRegistry(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        import app.agents.hooks as hooks_mod
        self._hooks_mod = hooks_mod
        self._old_path = hooks_mod.HOOKS_CONFIG_PATH
        hooks_mod.HOOKS_CONFIG_PATH = os.path.join(self._tempdir.name, "hooks.json")
        self.registry = HooksRegistry()

    def tearDown(self):
        self._hooks_mod.HOOKS_CONFIG_PATH = self._old_path
        self._tempdir.cleanup()

    def test_register_hook(self):
        hook = HookDefinition(
            event="PreToolUse",
            handlers=[HookHandler(command="echo test")],
            name="test_hook",
        )
        ok, msg = self.registry.register(hook)
        self.assertTrue(ok)

    def test_register_invalid_event(self):
        hook = HookDefinition(
            event="InvalidEvent",
            handlers=[HookHandler(command="echo")],
        )
        ok, msg = self.registry.register(hook)
        self.assertFalse(ok)

    def test_unregister(self):
        hook = HookDefinition(
            event="Stop", handlers=[HookHandler(command="echo")], name="to_remove",
        )
        self.registry.register(hook)
        ok, _ = self.registry.unregister("to_remove")
        self.assertTrue(ok)
        self.assertEqual(len(self.registry.get_hooks_for_event("Stop")), 0)

    def test_enable_disable(self):
        hook = HookDefinition(
            event="Stop", handlers=[HookHandler(command="echo")], name="toggle",
        )
        self.registry.register(hook)
        self.registry.disable("toggle")
        self.assertEqual(len(self.registry.get_hooks_for_event("Stop")), 0)
        self.registry.enable("toggle")
        self.assertEqual(len(self.registry.get_hooks_for_event("Stop")), 1)

    def test_list_hooks(self):
        hook = HookDefinition(
            event="SessionStart", handlers=[HookHandler(command="echo start")], name="h1",
        )
        self.registry.register(hook)
        hooks = self.registry.list_hooks()
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["name"], "h1")

    def test_fire_sync(self):
        hook = HookDefinition(
            event="SessionEnd",
            handlers=[HookHandler(command="echo done", handler_type="command")],
            name="end_hook",
        )
        self.registry.register(hook)
        results = self.registry.fire_sync("SessionEnd")
        self.assertGreater(len(results), 0)

    def test_register_from_skill(self):
        self.registry.register_from_skill("my_skill", [
            {"event": "PreToolUse", "handlers": [{"command": "echo pre"}]},
        ])
        hooks = self.registry.get_hooks_for_event("PreToolUse")
        self.assertEqual(len(hooks), 1)

    def test_register_from_subagent_stop_conversion(self):
        self.registry.register_from_subagent("my_agent", [
            {"event": "Stop", "handlers": [{"command": "echo stop"}]},
        ])
        # Stop → SubagentStop conversion
        hooks = self.registry.get_hooks_for_event("SubagentStop")
        self.assertEqual(len(hooks), 1)

    def test_history(self):
        hook = HookDefinition(
            event="Notification",
            handlers=[HookHandler(command="echo note", handler_type="command")],
            name="note_hook",
        )
        self.registry.register(hook)
        self.registry.fire_sync("Notification")
        history = self.registry.get_history()
        self.assertGreater(len(history), 0)


# ============================================================
# Subagents
# ============================================================
class TestSubagentConfig(unittest.TestCase):
    def test_builtin_agents_exist(self):
        self.assertIn("explore", BUILTIN_AGENTS)
        self.assertIn("plan", BUILTIN_AGENTS)
        self.assertIn("general-purpose", BUILTIN_AGENTS)

    def test_explore_is_readonly(self):
        explore = BUILTIN_AGENTS["explore"]
        self.assertIn("write_file", explore.disallowed_tools)
        self.assertEqual(explore.model, "haiku")

    def test_permission_modes(self):
        modes = [m.value for m in PermissionMode]
        self.assertIn("default", modes)
        self.assertIn("auto", modes)
        self.assertIn("bypassPermissions", modes)
        self.assertIn("plan", modes)


class TestSubagentManager(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        import app.agents.subagents as sub_mod
        self._sub_mod = sub_mod
        self._old_agents = sub_mod.AGENTS_DIR
        self._old_transcripts = sub_mod.AGENT_TRANSCRIPTS_DIR
        self._old_memory = sub_mod.AGENT_MEMORY_DIR
        sub_mod.AGENTS_DIR = os.path.join(self._tempdir.name, "agents")
        sub_mod.AGENT_TRANSCRIPTS_DIR = os.path.join(self._tempdir.name, "transcripts")
        sub_mod.AGENT_MEMORY_DIR = os.path.join(self._tempdir.name, "memory")
        os.makedirs(sub_mod.AGENTS_DIR, exist_ok=True)
        os.makedirs(sub_mod.AGENT_TRANSCRIPTS_DIR, exist_ok=True)
        os.makedirs(sub_mod.AGENT_MEMORY_DIR, exist_ok=True)
        self.manager = SubagentManager()

    def tearDown(self):
        self._sub_mod.AGENTS_DIR = self._old_agents
        self._sub_mod.AGENT_TRANSCRIPTS_DIR = self._old_transcripts
        self._sub_mod.AGENT_MEMORY_DIR = self._old_memory
        self._tempdir.cleanup()

    def test_create_agent(self):
        ok, msg = self.manager.create_agent("test-agent", description="Test", prompt="Do stuff")
        self.assertTrue(ok)

    def test_list_agents(self):
        agents = self.manager.list_agents()
        names = [a["name"] for a in agents]
        self.assertIn("explore", names)
        self.assertIn("plan", names)

    def test_remove_agent(self):
        self.manager.create_agent("removable", description="temp")
        ok, _ = self.manager.remove_agent("removable")
        self.assertTrue(ok)

    def test_cannot_remove_builtin(self):
        ok, _ = self.manager.remove_agent("explore")
        self.assertFalse(ok)

    def test_agent_memory(self):
        ok, _ = self.manager.save_agent_memory("test-mem", "Remember this pattern")
        self.assertTrue(ok)
        content = self.manager.get_agent_memory("test-mem")
        self.assertIn("Remember this", content)

    def test_send_message(self):
        ok, _ = self.manager.send_message("nonexistent", "hello")
        self.assertFalse(ok)

    def test_create_team(self):
        ok, _ = self.manager.create_team("reviewers", ["explore", "plan"])
        self.assertTrue(ok)
        teams = self.manager.list_teams()
        self.assertIn("reviewers", teams)


# ============================================================
# GEPA Evolution
# ============================================================
class TestSemanticPreservation(unittest.TestCase):
    def test_similar_text(self):
        passes, score = SemanticPreservation.check(
            "Deploy the application to production using Docker",
            "Deploy the app to prod environment using Docker containers",
        )
        self.assertTrue(passes)
        self.assertGreater(score, 0.3)

    def test_completely_different(self):
        passes, score = SemanticPreservation.check(
            "Deploy the application to production",
            "Cook a delicious pasta carbonara recipe",
        )
        self.assertFalse(passes)

    def test_empty_original(self):
        passes, _ = SemanticPreservation.check("", "anything")
        self.assertTrue(passes)


class TestParetoSelector(unittest.TestCase):
    def test_is_dominated(self):
        a = {"score": 0.5, "length": 0.6}
        b = {"score": 0.8, "length": 0.9}
        self.assertTrue(ParetoSelector.is_dominated(a, b, ["score", "length"]))

    def test_not_dominated(self):
        a = {"score": 0.9, "length": 0.3}
        b = {"score": 0.5, "length": 0.8}
        self.assertFalse(ParetoSelector.is_dominated(a, b, ["score", "length"]))

    def test_pareto_front(self):
        candidates = [
            {"id": "a", "score": 0.9, "length": 0.3},
            {"id": "b", "score": 0.5, "length": 0.8},
            {"id": "c", "score": 0.3, "length": 0.3},
        ]
        front = ParetoSelector.pareto_front(candidates, ["score", "length"])
        ids = [c["id"] for c in front]
        self.assertIn("a", ids)
        self.assertIn("b", ids)
        self.assertNotIn("c", ids)

    def test_tournament_select(self):
        candidates = [
            {"id": "a", "score": 0.9},
            {"id": "b", "score": 0.1},
            {"id": "c", "score": 0.5},
        ]
        selected = ParetoSelector.tournament_select(candidates, k=3)
        self.assertIn(selected["id"], ["a", "b", "c"])


class TestGEPAEngine(unittest.TestCase):
    def test_evolve_basic(self):
        engine = GEPAEngine()
        result = engine.evolve(
            "Deploy using Docker. Always check logs. Run tests first.",
            eval_cases=[{"category": "deploy", "expected_behavior": "correct deployment"}],
            population_size=4,
            generations=2,
        )
        self.assertIn("best", result)
        self.assertIn("improvement", result)
        self.assertIn("pareto_front_size", result)
        self.assertGreaterEqual(result["pareto_front_size"], 1)


class TestCrossover(unittest.TestCase):
    def test_crossover(self):
        engine = MutationEngine()
        parent_a = "Line A1\nLine A2\nLine A3\nLine A4"
        parent_b = "Line B1\nLine B2\nLine B3\nLine B4"
        child = engine.crossover(parent_a, parent_b)
        self.assertIn("Line A1", child)
        self.assertIn("Line B", child)


# ============================================================
# SKILL.md Format
# ============================================================
class TestSkillMdParser(unittest.TestCase):
    def test_parse_basic(self):
        content = """---
name: deploy
description: Deploy the app
context: fork
disable-model-invocation: true
allowed-tools: Read Grep Bash
---
Deploy the application:
1. Run tests
2. Build
3. Push"""
        result = parse_skill_md(content)
        self.assertEqual(result["name"], "deploy")
        self.assertEqual(result["context"], "fork")
        self.assertTrue(result["disable_model_invocation"])
        self.assertIn("Deploy the application", result["system_prompt"])

    def test_parse_no_frontmatter(self):
        result = parse_skill_md("Just plain text instructions")
        self.assertIn("Just plain text", result["system_prompt"])

    def test_render_skill_md(self):
        data = {"name": "test", "description": "Test skill",
                "model": "haiku", "system_prompt": "Do things"}
        rendered = render_skill_md(data)
        self.assertIn("---", rendered)
        self.assertIn("name: test", rendered)
        self.assertIn("model: haiku", rendered)
        self.assertIn("Do things", rendered)


# ============================================================
# Per-skill model/effort
# ============================================================
class TestPerSkillModelEffort(EvoTestBase):
    def test_create_with_model(self):
        from app.agents.evolution import SkillRegistry
        reg = SkillRegistry()
        reg.create_skill("s1", "S1", "test", "prompt", model="haiku", effort="low")
        skill = reg.get_skill("s1")
        self.assertEqual(skill["model"], "haiku")
        self.assertEqual(skill["effort"], "low")

    def test_create_with_context_fork(self):
        from app.agents.evolution import SkillRegistry
        reg = SkillRegistry()
        reg.create_skill("s2", "S2", "test", "prompt", context="fork")
        skill = reg.get_skill("s2")
        self.assertEqual(skill["context"], "fork")

    def test_create_with_disable_model_invocation(self):
        from app.agents.evolution import SkillRegistry
        reg = SkillRegistry()
        reg.create_skill("s3", "S3", "test", "prompt", disable_model_invocation=True)
        skill = reg.get_skill("s3")
        self.assertTrue(skill["disable_model_invocation"])


# ============================================================
# Secure Setup on Load
# ============================================================
class TestSecureSetup(unittest.TestCase):
    def test_check_env_requirements(self):
        skill = {
            "required_environment_variables": [
                {"name": "PATH", "prompt": "System PATH"},
                {"name": "NONEXISTENT_VAR_12345", "prompt": "Missing var"},
            ],
        }
        results = check_skill_env_requirements(skill)
        self.assertEqual(len(results), 2)
        path_result = next(r for r in results if r["name"] == "PATH")
        self.assertTrue(path_result["is_set"])
        missing = next(r for r in results if r["name"] == "NONEXISTENT_VAR_12345")
        self.assertFalse(missing["is_set"])


# ============================================================
# External Skill Directories
# ============================================================
class TestExternalSkillDirs(unittest.TestCase):
    def test_scan_with_skill_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = os.path.join(tmpdir, "my-skill")
            os.makedirs(skill_dir)
            Path(os.path.join(skill_dir, "SKILL.md")).write_text(
                "---\nname: ext-skill\ndescription: External\n---\nDo stuff"
            )
            found = scan_external_skill_dirs([tmpdir])
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["name"], "ext-skill")
            self.assertTrue(found[0]["external"])

    def test_scan_empty_dir(self):
        found = scan_external_skill_dirs(["/nonexistent/path"])
        self.assertEqual(len(found), 0)


# ============================================================
# SOUL.md
# ============================================================
class TestSoulMd(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "SOUL.md")
            ok, _ = save_soul("I am helpful and concise.", path)
            self.assertTrue(ok)
            content = load_soul(path)
            self.assertIn("helpful", content)

    def test_load_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            content = load_soul(os.path.join(tmpdir, "NO_SOUL.md"))
            # load_soul checks explicit path first; if given a nonexistent path
            # it may fall through to system defaults. Just check it doesn't crash.
            self.assertIsInstance(content, str)


# ============================================================
# Prompt Cache Injection
# ============================================================
class TestPromptCacheInjection(unittest.TestCase):
    def test_inject_breakpoints(self):
        prompt = "## Personality\nBe helpful\n## Skills\nUse tools\n## Memory\nRemember"
        blocks = inject_cache_breakpoints(prompt)
        self.assertGreater(len(blocks), 1)
        # All but last should have cache_control
        for b in blocks[:-1]:
            self.assertIn("cache_control", b)
        self.assertNotIn("cache_control", blocks[-1])

    def test_single_section(self):
        blocks = inject_cache_breakpoints("Simple prompt no sections")
        self.assertEqual(len(blocks), 1)


# ============================================================
# Plugin System
# ============================================================
class TestPluginRegistry(unittest.TestCase):
    def test_discover_empty(self):
        registry = PluginRegistry()
        plugins = registry.discover("/nonexistent")
        self.assertEqual(len(plugins), 0)

    def test_discover_with_plugin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = os.path.join(tmpdir, "plugins", "test-plugin")
            os.makedirs(plugin_dir)
            Path(os.path.join(plugin_dir, "plugin.json")).write_text(json.dumps({
                "name": "test-plugin", "version": "1.0", "description": "A test plugin",
                "tools": ["custom_tool"],
            }))
            import app.agents.self_evolution as se_mod
            old = se_mod.PLUGINS_DIR
            se_mod.PLUGINS_DIR = os.path.join(tmpdir, "plugins")
            registry = PluginRegistry()
            plugins = registry.discover()
            se_mod.PLUGINS_DIR = old
            self.assertEqual(len(plugins), 1)
            self.assertEqual(plugins[0]["name"], "test-plugin")

    def test_enable_disable(self):
        registry = PluginRegistry()
        registry._plugins["x"] = {"name": "x", "enabled": True}
        registry.disable("x")
        self.assertFalse(registry._plugins["x"]["enabled"])
        registry.enable("x")
        self.assertTrue(registry._plugins["x"]["enabled"])


# ============================================================
# Cron Manager
# ============================================================
class TestCronManager(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        import app.agents.self_evolution as se_mod
        self._se_mod = se_mod
        self._old_cron = se_mod.CRON_DIR
        se_mod.CRON_DIR = os.path.join(self._tempdir.name, "cron")
        os.makedirs(se_mod.CRON_DIR, exist_ok=True)
        self.cron = CronManager()

    def tearDown(self):
        self._se_mod.CRON_DIR = self._old_cron
        self._tempdir.cleanup()

    def test_add_job(self):
        ok, _ = self.cron.add_job("daily-review", "0 9 * * *", "code-review", action_type="skill")
        self.assertTrue(ok)

    def test_list_jobs(self):
        self.cron.add_job("j1", "* * * * *", "cmd1")
        jobs = self.cron.list_jobs()
        self.assertEqual(len(jobs), 1)

    def test_remove_job(self):
        self.cron.add_job("j2", "* * * * *", "cmd2")
        ok, _ = self.cron.remove_job("j2")
        self.assertTrue(ok)
        self.assertEqual(len(self.cron.list_jobs()), 0)

    def test_enable_disable(self):
        self.cron.add_job("j3", "* * * * *", "cmd3")
        self.cron.disable_job("j3")
        job = self.cron.get_job("j3")
        self.assertFalse(job["enabled"])
        self.cron.enable_job("j3")
        job = self.cron.get_job("j3")
        self.assertTrue(job["enabled"])

    def test_duplicate_job(self):
        self.cron.add_job("dup", "* * * * *", "cmd")
        ok, _ = self.cron.add_job("dup", "* * * * *", "cmd")
        self.assertFalse(ok)


# ============================================================
# Elicitation
# ============================================================
class TestElicitation(unittest.TestCase):
    def test_create_and_submit(self):
        mgr = ElicitationManager()
        req = mgr.create_request("Deploy Config", [
            {"name": "env", "type": "select", "label": "Environment", "options": ["dev", "prod"]},
            {"name": "confirm", "type": "boolean", "label": "Confirm?"},
        ])
        self.assertIsNotNone(req.elicitation_id)
        pending = mgr.get_pending()
        self.assertEqual(len(pending), 1)

        ok, _ = mgr.submit_result(req.elicitation_id, {"env": "prod", "confirm": True})
        self.assertTrue(ok)
        result = mgr.get_result(req.elicitation_id)
        self.assertEqual(result["values"]["env"], "prod")
        self.assertEqual(len(mgr.get_pending()), 0)

    def test_submit_invalid(self):
        mgr = ElicitationManager()
        ok, _ = mgr.submit_result("nonexistent", {})
        self.assertFalse(ok)


# ============================================================
# execute_code
# ============================================================
class TestExecuteCode(unittest.TestCase):
    def test_python(self):
        result = asyncio.run(execute_code("print('hello world')", "python"))
        self.assertEqual(result["status"], "success")
        self.assertIn("hello world", result["output"])

    def test_bash(self):
        result = asyncio.run(execute_code("echo 42", "bash"))
        self.assertEqual(result["status"], "success")
        self.assertIn("42", result["output"])

    def test_unsupported_language(self):
        result = asyncio.run(execute_code("code", "rust"))
        self.assertEqual(result["status"], "error")

    def test_timeout(self):
        result = asyncio.run(execute_code("import time; time.sleep(10)", "python", timeout=1))
        self.assertEqual(result["status"], "timeout")


if __name__ == "__main__":
    unittest.main()
