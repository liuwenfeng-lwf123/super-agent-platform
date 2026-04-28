"""
Tests for core agent features: reflection, dynamic spawn, tool pipelines.
"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch, MagicMock


class TestReflection(unittest.TestCase):
    """Test reflection module."""

    def test_should_reflect_config_disabled(self):
        from app.agents.reflection import should_reflect
        with patch("app.config.settings") as mock_settings:
            mock_settings.enable_reflection = False
            self.assertFalse(should_reflect("pro", "complex question", 500))

    def test_should_reflect_flash_mode(self):
        from app.agents.reflection import should_reflect
        self.assertFalse(should_reflect("flash", "complex question", 500))

    def test_should_reflect_standard_mode(self):
        from app.agents.reflection import should_reflect
        self.assertFalse(should_reflect("standard", "complex question", 500))

    def test_should_reflect_pro_mode_long_response(self):
        from app.agents.reflection import should_reflect
        with patch("app.config.settings") as mock_settings:
            mock_settings.enable_reflection = True
            result = should_reflect("pro", "explain quantum computing", 500)
            self.assertTrue(result)

    def test_should_reflect_short_response(self):
        from app.agents.reflection import should_reflect
        self.assertFalse(should_reflect("pro", "question", 50))

    def test_should_reflect_trivial_message(self):
        from app.agents.reflection import should_reflect
        self.assertFalse(should_reflect("pro", "你好", 500))

    def test_evaluate_response_fallback(self):
        """Test that evaluate_response returns pass=True on LLM failure."""
        from app.agents.reflection import evaluate_response
        with patch("app.agents.reflection.llm_provider") as mock_provider:
            mock_model = AsyncMock()
            mock_model.ainvoke.side_effect = Exception("LLM unavailable")
            mock_provider.get_chat_model.return_value = mock_model
            mock_provider.aclose_model = AsyncMock()
            result = asyncio.run(evaluate_response("test", "response"))
            self.assertTrue(result["pass"])

    def test_reflection_result_class(self):
        from app.agents.reflection import ReflectionResult
        r = ReflectionResult()
        self.assertFalse(r.passed)
        self.assertEqual(r.score, 0)
        self.assertEqual(r.rounds_used, 0)


class TestDynamicSpawn(unittest.TestCase):
    """Test dynamic spawn module."""

    def test_spawn_manager_init(self):
        from app.agents.dynamic_spawn import DynamicSpawnManager
        mgr = DynamicSpawnManager()
        self.assertEqual(len(mgr._active_spawns), 0)
        self.assertEqual(len(mgr._results), 0)

    def test_can_spawn_initial(self):
        from app.agents.dynamic_spawn import DynamicSpawnManager
        mgr = DynamicSpawnManager()
        mgr.register_parent("parent-1")
        can, reason = mgr.can_spawn("parent-1")
        self.assertTrue(can)
        self.assertEqual(reason, "")

    def test_cannot_spawn_max_depth(self):
        from app.agents.dynamic_spawn import DynamicSpawnManager, MAX_SPAWN_DEPTH
        mgr = DynamicSpawnManager()
        mgr._spawn_depth["deep-agent"] = MAX_SPAWN_DEPTH
        can, reason = mgr.can_spawn("deep-agent")
        self.assertFalse(can)
        self.assertIn("depth", reason)

    def test_cannot_spawn_max_children(self):
        from app.agents.dynamic_spawn import DynamicSpawnManager, MAX_CHILDREN_PER_AGENT
        mgr = DynamicSpawnManager()
        mgr.register_parent("parent-2")
        mgr._active_spawns["parent-2"] = [f"child-{i}" for i in range(MAX_CHILDREN_PER_AGENT)]
        can, reason = mgr.can_spawn("parent-2")
        self.assertFalse(can)
        self.assertIn("children", reason)

    def test_cannot_spawn_over_budget(self):
        from app.agents.dynamic_spawn import DynamicSpawnManager
        mgr = DynamicSpawnManager()
        mgr.register_parent("parent-3")
        with patch("app.agents.dynamic_spawn.cost_tracker") as mock_ct:
            mock_ct.is_over_budget.return_value = True
            can, reason = mgr.can_spawn("parent-3")
            self.assertFalse(can)
            self.assertIn("budget", reason.lower())

    def test_cleanup(self):
        from app.agents.dynamic_spawn import DynamicSpawnManager, SpawnResult
        mgr = DynamicSpawnManager()
        mgr.register_parent("p1")
        mgr._active_spawns["p1"] = ["c1", "c2"]
        mgr._results["c1"] = SpawnResult("c1", "task", "researcher", "completed")
        mgr._results["c2"] = SpawnResult("c2", "task2", "coder", "completed")
        mgr._spawn_depth["c1"] = 1
        mgr._spawn_depth["c2"] = 1
        mgr.cleanup("p1")
        self.assertNotIn("p1", mgr._active_spawns)
        self.assertNotIn("c1", mgr._results)

    def test_spawn_request_defaults(self):
        from app.agents.dynamic_spawn import SpawnRequest
        req = SpawnRequest(task="do something")
        self.assertEqual(req.role, "researcher")
        self.assertEqual(req.timeout, 90)
        self.assertEqual(req.priority, "normal")


class TestToolPipelines(unittest.TestCase):
    """Test tool pipeline registry."""

    def test_builtin_pipelines_registered(self):
        from app.agents.tool_pipelines import PipelineRegistry
        reg = PipelineRegistry()
        pipelines = reg.list_pipelines()
        self.assertEqual(len(pipelines), 5)
        names = [p["name"] for p in pipelines]
        self.assertIn("research_and_summarize", names)
        self.assertIn("code_and_test", names)
        self.assertIn("fetch_and_extract", names)
        self.assertIn("multi_search_compare", names)
        self.assertIn("read_analyze_write", names)

    def test_filter_by_tag(self):
        from app.agents.tool_pipelines import PipelineRegistry
        reg = PipelineRegistry()
        research = reg.list_pipelines(tag="research")
        self.assertTrue(len(research) >= 1)
        for p in research:
            self.assertIn("research", p["tags"])

    def test_register_custom_pipeline(self):
        from app.agents.tool_pipelines import PipelineRegistry, Pipeline, PipelineStep
        reg = PipelineRegistry()
        custom = Pipeline(
            name="my_pipeline",
            description="test",
            steps=[PipelineStep(tool_name="web_search", input_template={"query": "$input.q"})],
            created_by="user",
            tags=["custom"],
        )
        reg.register(custom)
        self.assertIsNotNone(reg.get("my_pipeline"))

    def test_remove_user_pipeline(self):
        from app.agents.tool_pipelines import PipelineRegistry, Pipeline, PipelineStep
        reg = PipelineRegistry()
        custom = Pipeline(name="temp", description="t", steps=[], created_by="user")
        reg.register(custom)
        self.assertTrue(reg.remove("temp"))
        self.assertIsNone(reg.get("temp"))

    def test_cannot_remove_system_pipeline(self):
        from app.agents.tool_pipelines import PipelineRegistry
        reg = PipelineRegistry()
        self.assertFalse(reg.remove("research_and_summarize"))

    def test_resolve_input_ref(self):
        from app.agents.tool_pipelines import PipelineRegistry
        reg = PipelineRegistry()
        result = reg._resolve_ref("$input.query", {"query": "hello"}, {})
        self.assertEqual(result, "hello")

    def test_resolve_step_ref(self):
        from app.agents.tool_pipelines import PipelineRegistry
        reg = PipelineRegistry()
        step_outputs = {"step_0": {"results": ["item1", "item2"]}}
        result = reg._resolve_ref("$step_0.results", {}, step_outputs)
        self.assertEqual(result, ["item1", "item2"])

    def test_pipeline_not_found(self):
        from app.agents.tool_pipelines import PipelineRegistry
        reg = PipelineRegistry()
        result = asyncio.run(reg.execute("nonexistent", {}))
        self.assertEqual(result.status, "failed")
        self.assertIn("not found", result.errors[0])


class TestAPIEndpoints(unittest.TestCase):
    """Test new API endpoints."""

    def test_pipeline_list_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.get("/api/pipelines")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("pipelines", data)
        self.assertTrue(len(data["pipelines"]) >= 5)

    def test_spawn_status_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.get("/api/spawn/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("max_depth", data)
        self.assertEqual(data["max_depth"], 3)

    def test_create_pipeline_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.post("/api/pipelines", json={
            "name": "test_endpoint_pipeline",
            "description": "test pipeline",
            "steps": [{"tool_name": "web_search", "input_template": {"query": "$input.q"}}],
            "tags": ["test"],
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        # Cleanup
        client.delete("/api/pipelines/test_endpoint_pipeline")

    def test_delete_pipeline_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        # Create first
        client.post("/api/pipelines", json={
            "name": "to_delete",
            "description": "will be deleted",
            "steps": [],
        })
        resp = client.delete("/api/pipelines/to_delete")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_delete_system_pipeline_fails(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.delete("/api/pipelines/research_and_summarize")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["success"])


if __name__ == "__main__":
    unittest.main()
