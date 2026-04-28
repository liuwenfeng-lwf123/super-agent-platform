"""Integration tests: verify modules are WIRED into the main execution path,
not just existing as dead code."""
import ast
import os
import unittest


BACKEND = os.path.join(os.path.dirname(__file__), "..")


class TestHooksWiredIntoSuperAgent(unittest.TestCase):
    """Verify hooks_registry.fire() is called in super_agent.py agent loops."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND, "app/agents/super_agent.py")) as f:
            cls.src = f.read()

    def test_hooks_import(self):
        self.assertIn("from app.agents.hooks import hooks_registry", self.src)

    def test_pre_tool_use_fired_in_tool_runtime(self):
        """PreToolUse now fires in tool_runtime._fire_pre_tool_use_hooks (single source of truth)."""
        with open(os.path.join(BACKEND, "app/agents/tool_runtime.py")) as f:
            rt_src = f.read()
        self.assertIn('fire_sync("PreToolUse"', rt_src)

    def test_post_tool_use_fired_in_tool_runtime(self):
        """PostToolUse now fires in tool_runtime._fire_post_tool_use_hooks."""
        with open(os.path.join(BACKEND, "app/agents/tool_runtime.py")) as f:
            rt_src = f.read()
        self.assertIn('fire_sync("PostToolUse"', rt_src)

    def test_hook_events_consumed_in_agent(self):
        """super_agent.py consumes buffered hook events (deny/modified_input) via SSE."""
        self.assertIn('consume_hook_events', self.src)

    def test_stop_hook_fired(self):
        count = self.src.count('hooks_registry.fire("Stop"')
        self.assertGreaterEqual(count, 1, "Stop should fire in _shared_flow (used by both standard & pro)")

    def test_session_start_fired(self):
        self.assertIn('hooks_registry.fire("SessionStart"', self.src)

    def test_hook_deny_buffered_in_tool_runtime(self):
        """hook_deny events are buffered by tool_runtime for SSE surfacing."""
        with open(os.path.join(BACKEND, "app/agents/tool_runtime.py")) as f:
            rt_src = f.read()
        self.assertIn('"hook_deny"', rt_src)
        self.assertIn('"hook_modified_input"', rt_src)


class TestPerSkillModelRouting(unittest.TestCase):
    """Verify per-skill model override is wired into super_agent.py."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND, "app/agents/super_agent.py")) as f:
            cls.src = f.read()

    def test_active_skills_stored(self):
        self.assertIn("self._active_skills = active_skills", self.src)

    def test_effective_model_used(self):
        self.assertIn("effective_model", self.src)

    def test_skill_model_override(self):
        self.assertIn('sd.get("model")', self.src)

    def test_skill_prompt_injection(self):
        self.assertIn('sd.get("system_prompt")', self.src)

    def test_env_requirements_check(self):
        self.assertIn("check_skill_env_requirements", self.src)


class TestAPIEndpointsExist(unittest.TestCase):
    """Verify all new API endpoints are registered across API route files."""

    @classmethod
    def setUpClass(cls):
        api_dir = os.path.join(BACKEND, "app/api")
        parts = []
        for fname in os.listdir(api_dir):
            if fname.endswith(".py"):
                with open(os.path.join(api_dir, fname)) as f:
                    parts.append(f.read())
        cls.src = "\n".join(parts)

    def _check_endpoint(self, path):
        self.assertIn(path, self.src, f"Missing API endpoint: {path}")

    def test_hooks_endpoints(self):
        self._check_endpoint('"/hooks"')
        self._check_endpoint('"/hooks/register"')
        self._check_endpoint('"/hooks/fire"')
        self._check_endpoint('"/hooks/history"')

    def test_agents_endpoints(self):
        self._check_endpoint('"/subagents"')
        self._check_endpoint('"/subagents/create"')
        self._check_endpoint('"/subagents/spawn"')
        self._check_endpoint('"/subagents/instances"')
        self._check_endpoint('"/subagents/team"')
        self._check_endpoint('"/subagents/teams"')

    def test_gepa_endpoints(self):
        self._check_endpoint('"/evolution/gepa"')
        self._check_endpoint('"/evolution/semantic-check"')

    def test_plugin_endpoints(self):
        self._check_endpoint('"/plugins"')
        self._check_endpoint('"/plugins/discover"')

    def test_cron_endpoints(self):
        self._check_endpoint('"/cron"')

    def test_elicitation_endpoints(self):
        self._check_endpoint('"/elicitation/request"')
        self._check_endpoint('"/elicitation/pending"')

    def test_execute_code_endpoint(self):
        self._check_endpoint('"/execute-code"')

    def test_soul_endpoints(self):
        self._check_endpoint('"/soul"')

    def test_skill_md_endpoints(self):
        self._check_endpoint('"/skills/parse-md"')
        self._check_endpoint('"/skills/render-md"')
        self._check_endpoint('"/skills/check-env"')
        self._check_endpoint('"/skills/scan-external"')

    def test_prompt_cache_endpoint(self):
        self._check_endpoint('"/prompt/cache-breakpoints"')


class TestLangChainToolsRegistered(unittest.TestCase):
    """Verify new LangChain tools are in EVOLUTION_TOOLS."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND, "app/agents/evolution.py")) as f:
            cls.src = f.read()

    def test_gepa_evolve_tool(self):
        self.assertIn("def gepa_evolve(", self.src)

    def test_spawn_agent_tool(self):
        self.assertIn("def spawn_agent(", self.src)

    def test_send_agent_message_tool(self):
        self.assertIn("def send_agent_message(", self.src)

    def test_register_hook_tool(self):
        self.assertIn("def register_hook(", self.src)

    def test_execute_code_tool(self):
        self.assertIn("def execute_code_tool(", self.src)

    def test_elicit_input_tool(self):
        self.assertIn("def elicit_input(", self.src)

    def test_all_in_evolution_tools_list(self):
        self.assertIn("gepa_evolve, spawn_agent, send_agent_message, register_hook", self.src)
        self.assertIn("execute_code_tool, elicit_input", self.src)


class TestToolRuntimeCategories(unittest.TestCase):
    """Verify new tools are categorized in tool_runtime.py."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND, "app/agents/tool_runtime.py")) as f:
            cls.src = f.read()

    def test_gepa_evolve(self):
        self.assertIn('"gepa_evolve"', self.src)

    def test_spawn_agent(self):
        self.assertIn('"spawn_agent"', self.src)

    def test_register_hook(self):
        self.assertIn('"register_hook"', self.src)

    def test_execute_code(self):
        self.assertIn('"execute_code"', self.src)

    def test_elicit_input(self):
        self.assertIn('"elicit_input"', self.src)

    def test_manage_cron(self):
        self.assertIn('"manage_cron"', self.src)

    def test_manage_plugin(self):
        self.assertIn('"manage_plugin"', self.src)


class TestModulesImportable(unittest.TestCase):
    """Verify all new modules can actually be imported without errors."""

    def test_import_hooks(self):
        from app.agents.hooks import HooksRegistry, HookEvent, hooks_registry
        self.assertIsNotNone(hooks_registry)

    def test_import_subagents(self):
        from app.agents.subagents import SubagentManager, subagent_manager, BUILTIN_AGENTS
        self.assertIn("explore", BUILTIN_AGENTS)

    def test_import_gepa(self):
        from app.agents.self_evolution import GEPAEngine, SemanticPreservation, ParetoSelector
        engine = GEPAEngine()
        self.assertIsNotNone(engine)

    def test_import_plugin(self):
        from app.agents.self_evolution import PluginRegistry, plugin_registry
        self.assertIsNotNone(plugin_registry)

    def test_import_cron(self):
        from app.agents.self_evolution import CronManager, cron_manager
        self.assertIsNotNone(cron_manager)

    def test_import_elicitation(self):
        from app.agents.self_evolution import ElicitationManager, elicitation_manager
        self.assertIsNotNone(elicitation_manager)

    def test_import_skill_md(self):
        from app.agents.self_evolution import parse_skill_md, render_skill_md
        self.assertIsNotNone(parse_skill_md)

    def test_import_soul(self):
        from app.agents.self_evolution import load_soul, save_soul
        self.assertIsNotNone(load_soul)

    def test_import_cache(self):
        from app.agents.self_evolution import inject_cache_breakpoints
        self.assertIsNotNone(inject_cache_breakpoints)

    def test_import_execute_code(self):
        from app.agents.self_evolution import execute_code
        self.assertIsNotNone(execute_code)

    def test_evolution_tools_count(self):
        from app.agents.evolution import EVOLUTION_TOOLS
        self.assertGreaterEqual(len(EVOLUTION_TOOLS), 20,
                                f"Expected 20+ tools, got {len(EVOLUTION_TOOLS)}")


class TestSubagentMultiTurnLoop(unittest.TestCase):
    """Verify subagent _agent_turn uses create_react_agent (multi-turn)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND, "app/agents/subagents.py")) as f:
            cls.src = f.read()

    def test_uses_create_react_agent(self):
        self.assertIn("create_react_agent", self.src)

    def test_astream_events(self):
        self.assertIn("astream_events", self.src)

    def test_tool_filtering(self):
        # whitelist uses config.tools (Claude Code naming), blacklist uses disallowed_tools
        self.assertIn("config.tools", self.src)
        self.assertIn("config.disallowed_tools", self.src)

    def test_tool_events_in_transcript(self):
        self.assertIn('"role": "tool"', self.src)
        self.assertIn('"role": "tool_result"', self.src)


class TestPluginCodeLoading(unittest.TestCase):
    """Verify plugin system can load Python code via importlib."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND, "app/agents/self_evolution.py")) as f:
            cls.src = f.read()

    def test_load_plugin_method(self):
        self.assertIn("def load_plugin(", self.src)

    def test_importlib_usage(self):
        self.assertIn("importlib.util.spec_from_file_location", self.src)
        self.assertIn("spec.loader.exec_module", self.src)

    def test_load_all_method(self):
        self.assertIn("def load_all(", self.src)

    def test_pip_entry_points(self):
        self.assertIn("def discover_pip_plugins(", self.src)
        self.assertIn("hermes_plugins", self.src)

    def test_auto_register_hooks(self):
        self.assertIn("register_from_skill", self.src)

    def test_load_plugin_works(self):
        from app.agents.self_evolution import plugin_registry
        ok, msg = plugin_registry.load_plugin("nonexistent")
        self.assertFalse(ok)


class TestCronBackgroundScheduler(unittest.TestCase):
    """Verify cron manager has background scheduler."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND, "app/agents/self_evolution.py")) as f:
            cls.src = f.read()

    def test_start_scheduler_method(self):
        self.assertIn("def start_scheduler(", self.src)

    def test_stop_scheduler_method(self):
        self.assertIn("def stop_scheduler(", self.src)

    def test_cron_matches_now(self):
        self.assertIn("def _cron_matches_now(", self.src)

    def test_daemon_thread(self):
        self.assertIn("daemon=True", self.src)
        self.assertIn('name="cron-scheduler"', self.src)

    def test_cron_expression_matching(self):
        from app.agents.self_evolution import CronManager
        cm = CronManager()
        # '*' should always match
        self.assertTrue(cm._cron_matches_now("* * * * *"))
        # Impossible minute should not match
        self.assertFalse(cm._cron_matches_now("99 99 99 99 99"))

    def test_scheduler_api_endpoints(self):
        api_dir = os.path.join(BACKEND, "app/api")
        api = ""
        for fname in os.listdir(api_dir):
            if fname.endswith(".py"):
                with open(os.path.join(api_dir, fname)) as f:
                    api += f.read()
        self.assertIn("/cron/scheduler/start", api)
        self.assertIn("/cron/scheduler/stop", api)


class TestGEPALLMMutation(unittest.TestCase):
    """Verify GEPA uses LLM mutations with rule-based fallback."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BACKEND, "app/agents/self_evolution.py")) as f:
            cls.src = f.read()

    def test_use_llm_parameter(self):
        self.assertIn("use_llm: bool = True", self.src)

    def test_try_llm_mutations_method(self):
        self.assertIn("def _try_llm_mutations(", self.src)

    def test_llm_falls_back_to_rules(self):
        from app.agents.self_evolution import MutationEngine
        me = MutationEngine()
        # With use_llm=False, should use rule-based
        candidates = me.generate_mutations("Test prompt content", use_llm=False)
        self.assertGreater(len(candidates), 0)
        for c in candidates:
            self.assertIn("mutation_type", c)

    def test_llm_mutation_system_prompt(self):
        self.assertIn("prompt evolution engine", self.src)


if __name__ == "__main__":
    unittest.main()
