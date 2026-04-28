"""Live API endpoint tests using FastAPI TestClient.
Actually exercises HTTP paths, not just grep source code."""
import unittest
from fastapi.testclient import TestClient


class TestLiveAPIEndpoints(unittest.TestCase):
    """Verify new API endpoints are reachable and return expected shapes."""

    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.close()
        except Exception:
            pass

    # --- Hooks ---
    def test_GET_hooks(self):
        r = self.client.get("/api/hooks")
        self.assertEqual(r.status_code, 200)
        self.assertIn("hooks", r.json())

    def test_GET_hooks_history(self):
        r = self.client.get("/api/hooks/history")
        self.assertEqual(r.status_code, 200)
        self.assertIn("history", r.json())

    def test_POST_hooks_register(self):
        r = self.client.post("/api/hooks/register", json={
            "event": "PreToolUse", "name": "test_hook_api",
            "handlers": [{"handler_type": "command", "command": "echo hello"}],
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("ok", r.json())
        # cleanup
        self.client.delete("/api/hooks/test_hook_api")

    def test_POST_hooks_fire(self):
        r = self.client.post("/api/hooks/fire", json={
            "event": "PreToolUse", "context": {"tool_name": "test"},
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("results", r.json())

    # --- Subagents (Hermes/Claude Code) ---
    def test_GET_subagents(self):
        r = self.client.get("/api/subagents")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("agents", data)
        names = [a["name"] for a in data["agents"]]
        self.assertIn("explore", names)
        self.assertIn("plan", names)
        self.assertIn("general-purpose", names)

    def test_GET_subagents_instances(self):
        r = self.client.get("/api/subagents/instances")
        self.assertEqual(r.status_code, 200)
        self.assertIn("instances", r.json())

    def test_GET_subagents_teams(self):
        r = self.client.get("/api/subagents/teams")
        self.assertEqual(r.status_code, 200)
        self.assertIn("teams", r.json())

    # --- Plugins ---
    def test_GET_plugins(self):
        r = self.client.get("/api/plugins")
        self.assertEqual(r.status_code, 200)
        self.assertIn("plugins", r.json())

    def test_POST_plugins_discover(self):
        r = self.client.post("/api/plugins/discover", json={})
        self.assertEqual(r.status_code, 200)
        self.assertIn("found", r.json())

    def test_POST_plugins_discover_pip(self):
        r = self.client.post("/api/plugins/discover-pip")
        self.assertEqual(r.status_code, 200)
        self.assertIn("found", r.json())

    # --- Cron ---
    def test_GET_cron(self):
        r = self.client.get("/api/cron")
        self.assertEqual(r.status_code, 200)
        self.assertIn("jobs", r.json())

    def test_POST_cron_scheduler_start_stop(self):
        r = self.client.post("/api/cron/scheduler/start")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))
        r = self.client.post("/api/cron/scheduler/stop")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))

    # --- GEPA ---
    def test_POST_evolution_semantic_check(self):
        r = self.client.post("/api/evolution/semantic-check", json={
            "original": "Do X with care.",
            "evolved": "Do X with great care and attention.",
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("passes", r.json())
        self.assertIn("score", r.json())

    # --- Elicitation ---
    def test_POST_elicitation_request(self):
        r = self.client.post("/api/elicitation/request", json={
            "title": "Test input",
            "fields": [{"name": "env", "type": "select", "options": ["dev", "prod"]}],
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("elicitation_id", data)

    def test_GET_elicitation_pending(self):
        r = self.client.get("/api/elicitation/pending")
        self.assertEqual(r.status_code, 200)
        self.assertIn("pending", r.json())

    # --- SOUL.md ---
    def test_GET_soul(self):
        r = self.client.get("/api/soul")
        self.assertEqual(r.status_code, 200)
        self.assertIn("content", r.json())

    # --- SKILL.md parse ---
    def test_POST_skills_parse_md(self):
        content = """---
name: test
description: A test skill
---
# Body"""
        r = self.client.post("/api/skills/parse-md", json={"content": content})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("name"), "test")

    def test_POST_skills_render_md(self):
        r = self.client.post("/api/skills/render-md", json={
            "name": "test", "description": "Test", "system_prompt": "Body",
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("---", r.json().get("rendered", ""))

    def test_POST_skills_check_env(self):
        r = self.client.post("/api/skills/check-env", json={
            "required_environment_variables": [{"name": "NONEXISTENT_VAR_XYZ"}],
        })
        self.assertEqual(r.status_code, 200)
        reqs = r.json().get("requirements", [])
        self.assertGreater(len(reqs), 0)

    def test_POST_skills_scan_external(self):
        r = self.client.post("/api/skills/scan-external", json={"dirs": ["/tmp/nonexistent_xyz"]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("skills", r.json())

    # --- Prompt Cache ---
    def test_POST_prompt_cache_breakpoints(self):
        r = self.client.post("/api/prompt/cache-breakpoints", json={
            "prompt": "You are a helpful assistant.\n\nAlways be kind.",
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("blocks", r.json())


if __name__ == "__main__":
    unittest.main()
