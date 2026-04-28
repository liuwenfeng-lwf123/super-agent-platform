import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router


class ControlFlowApiTestMixin:
    def setUp(self):
        import app.agents.hooks as hooks_mod
        import app.agents.self_evolution as evo_mod
        import app.agents.subagents as sub_mod
        import app.agents.tool_runtime as tool_mod

        self.hooks_mod = hooks_mod
        self.evo_mod = evo_mod
        self.sub_mod = sub_mod
        self.tool_mod = tool_mod
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)

        self._old_hook_paths = (hooks_mod.HOOKS_DIR, hooks_mod.HOOKS_CONFIG_PATH)
        hooks_mod.HOOKS_DIR = os.path.join(self._tempdir.name, "hooks")
        hooks_mod.HOOKS_CONFIG_PATH = os.path.join(hooks_mod.HOOKS_DIR, "hooks_config.json")
        os.makedirs(hooks_mod.HOOKS_DIR, exist_ok=True)

        self._old_subagent_paths = (
            sub_mod.DATA_DIR,
            sub_mod.AGENTS_DIR,
            sub_mod.AGENT_TRANSCRIPTS_DIR,
            sub_mod.AGENT_MEMORY_DIR,
        )
        sub_mod.DATA_DIR = os.path.join(self._tempdir.name, "subagents-data")
        sub_mod.AGENTS_DIR = os.path.join(sub_mod.DATA_DIR, "agents")
        sub_mod.AGENT_TRANSCRIPTS_DIR = os.path.join(sub_mod.DATA_DIR, "agent_transcripts")
        sub_mod.AGENT_MEMORY_DIR = os.path.join(sub_mod.DATA_DIR, "agent_memory")
        os.makedirs(sub_mod.AGENTS_DIR, exist_ok=True)
        os.makedirs(sub_mod.AGENT_TRANSCRIPTS_DIR, exist_ok=True)
        os.makedirs(sub_mod.AGENT_MEMORY_DIR, exist_ok=True)

        self._old_evo_paths = (
            evo_mod.PLUGINS_DIR,
            evo_mod.USER_PLUGINS_DIR,
            evo_mod.CRON_DIR,
        )
        evo_mod.PLUGINS_DIR = os.path.join(self._tempdir.name, "plugins")
        evo_mod.USER_PLUGINS_DIR = os.path.join(self._tempdir.name, "user-plugins")
        evo_mod.CRON_DIR = os.path.join(self._tempdir.name, "cron")
        os.makedirs(evo_mod.PLUGINS_DIR, exist_ok=True)
        os.makedirs(evo_mod.USER_PLUGINS_DIR, exist_ok=True)
        os.makedirs(evo_mod.CRON_DIR, exist_ok=True)

        self.hooks_registry = hooks_mod.HooksRegistry()
        self.subagent_manager = sub_mod.SubagentManager()
        self.plugin_registry = evo_mod.PluginRegistry()
        self.cron_manager = evo_mod.CronManager()
        self.elicitation_manager = evo_mod.ElicitationManager()
        self.permission_requests = tool_mod.PermissionRequestManager()

        self._patches = [
            patch.object(hooks_mod, "hooks_registry", self.hooks_registry),
            patch.object(sub_mod, "subagent_manager", self.subagent_manager),
            patch.object(evo_mod, "plugin_registry", self.plugin_registry),
            patch.object(evo_mod, "cron_manager", self.cron_manager),
            patch.object(evo_mod, "elicitation_manager", self.elicitation_manager),
            patch.object(tool_mod, "permission_requests", self.permission_requests),
        ]
        for active_patch in self._patches:
            active_patch.start()

        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)

    def tearDown(self):
        try:
            self.client.close()
        except Exception:
            pass
        for active_patch in reversed(self._patches):
            active_patch.stop()
        self.hooks_mod.HOOKS_DIR, self.hooks_mod.HOOKS_CONFIG_PATH = self._old_hook_paths
        (
            self.sub_mod.DATA_DIR,
            self.sub_mod.AGENTS_DIR,
            self.sub_mod.AGENT_TRANSCRIPTS_DIR,
            self.sub_mod.AGENT_MEMORY_DIR,
        ) = self._old_subagent_paths
        (
            self.evo_mod.PLUGINS_DIR,
            self.evo_mod.USER_PLUGINS_DIR,
            self.evo_mod.CRON_DIR,
        ) = self._old_evo_paths
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()


class TestPermissionFlowE2E(ControlFlowApiTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_permission_request_can_be_approved_through_api(self):
        queue = self.permission_requests.subscribe("thread-approve")
        transport = httpx.ASGITransport(app=self.app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                request_task = asyncio.create_task(
                    self.permission_requests.request_permission(
                        thread_id="thread-approve",
                        agent_id="agent-1",
                        mode="default",
                        tool_name="execute_bash",
                        tool_input={"command": "echo risky"},
                        reason="Needs approval",
                        source="user",
                    )
                )
                pending_event = await asyncio.wait_for(queue.get(), timeout=1)
                self.assertEqual(pending_event["type"], "permission_request")
                request_id = pending_event["data"]["request_id"]

                pending = await client.get("/api/permissions/pending", params={"thread_id": "thread-approve"})
                self.assertEqual(pending.status_code, 200)
                self.assertEqual(len(pending.json()["requests"]), 1)
                self.assertEqual(pending.json()["requests"][0]["request_id"], request_id)

                approve = await client.post(f"/api/permissions/{request_id}/approve", params={"note": "looks good"})
                self.assertEqual(approve.status_code, 200)
                self.assertEqual(approve.json()["status"], "approved")
                self.assertEqual(approve.json()["resolution_note"], "looks good")

                resolved_event = await asyncio.wait_for(queue.get(), timeout=1)
                self.assertEqual(resolved_event["data"]["status"], "approved")
                result = await asyncio.wait_for(request_task, timeout=1)
                self.assertEqual(result.decision, "allow")
                self.assertEqual(result.reason, "looks good")
        finally:
            self.permission_requests.unsubscribe("thread-approve", queue)

    async def test_permission_request_can_be_denied_through_api(self):
        queue = self.permission_requests.subscribe("thread-deny")
        transport = httpx.ASGITransport(app=self.app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                request_task = asyncio.create_task(
                    self.permission_requests.request_permission(
                        thread_id="thread-deny",
                        agent_id="agent-2",
                        mode="default",
                        tool_name="write_file",
                        tool_input={"path": "secret.txt", "content": "classified"},
                        reason="Needs approval",
                        source="user",
                    )
                )
                pending_event = await asyncio.wait_for(queue.get(), timeout=1)
                request_id = pending_event["data"]["request_id"]

                deny = await client.post(f"/api/permissions/{request_id}/deny", params={"note": "not allowed"})
                self.assertEqual(deny.status_code, 200)
                self.assertEqual(deny.json()["status"], "denied")

                resolved_event = await asyncio.wait_for(queue.get(), timeout=1)
                self.assertEqual(resolved_event["data"]["status"], "denied")
                result = await asyncio.wait_for(request_task, timeout=1)
                self.assertEqual(result.decision, "deny")
                self.assertEqual(result.reason, "not allowed")
        finally:
            self.permission_requests.unsubscribe("thread-deny", queue)


class TestControlFlowApiE2E(ControlFlowApiTestMixin, unittest.TestCase):
    def test_elicitation_request_submit_and_result_flow(self):
        create = self.client.post(
            "/api/elicitation/request",
            json={
                "title": "Deployment Config",
                "description": "Choose deployment target",
                "fields": [
                    {"name": "env", "type": "select", "label": "Environment", "options": ["dev", "prod"]},
                    {"name": "confirm", "type": "boolean", "label": "Confirm"},
                ],
            },
        )
        self.assertEqual(create.status_code, 200)
        elicitation_id = create.json()["elicitation_id"]

        pending = self.client.get("/api/elicitation/pending")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(len(pending.json()["pending"]), 1)
        self.assertEqual(pending.json()["pending"][0]["elicitation_id"], elicitation_id)

        submit = self.client.post(
            f"/api/elicitation/{elicitation_id}/submit",
            json={"values": {"env": "prod", "confirm": True}},
        )
        self.assertEqual(submit.status_code, 200)
        self.assertTrue(submit.json()["ok"])

        result = self.client.get(f"/api/elicitation/{elicitation_id}")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["values"], {"env": "prod", "confirm": True})

        pending_after = self.client.get("/api/elicitation/pending")
        self.assertEqual(pending_after.status_code, 200)
        self.assertEqual(pending_after.json()["pending"], [])

    def test_plugin_discover_load_and_bindings_are_visible_via_api(self):
        plugin_name = "demo-plugin"
        plugin_dir = Path(self.evo_mod.PLUGINS_DIR) / plugin_name
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": plugin_name,
                    "version": "1.0.0",
                    "description": "Plugin for e2e testing",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (plugin_dir / "main.py").write_text(
            "HOOKS = [{\"event\": \"Notification\", \"handlers\": [{\"handler_type\": \"command\", \"command\": \"echo plugin\"}]}]\n"
            "AGENTS = [{\"name\": \"plugin-reviewer\", \"description\": \"Plugin-provided reviewer\"}]\n",
            encoding="utf-8",
        )

        discover = self.client.post("/api/plugins/discover", json={})
        self.assertEqual(discover.status_code, 200)
        discovered_names = {plugin["name"] for plugin in discover.json()["plugins"]}
        self.assertIn(plugin_name, discovered_names)

        load = self.client.post(f"/api/plugins/{plugin_name}/load")
        self.assertEqual(load.status_code, 200)
        self.assertTrue(load.json()["ok"])

        plugins = self.client.get("/api/plugins")
        self.assertEqual(plugins.status_code, 200)
        plugin_payload = next(plugin for plugin in plugins.json()["plugins"] if plugin["name"] == plugin_name)
        self.assertTrue(plugin_payload["loaded"])

        subagents = self.client.get("/api/subagents")
        self.assertEqual(subagents.status_code, 200)
        subagent_names = [agent["name"] for agent in subagents.json()["agents"]]
        self.assertIn("plugin-reviewer", subagent_names)

        hooks = self.client.get("/api/hooks")
        self.assertEqual(hooks.status_code, 200)
        hook_names = [hook["name"] for hook in hooks.json()["hooks"]]
        self.assertIn(f"skill:{plugin_name}:Notification", hook_names)

    def test_cron_job_full_api_lifecycle(self):
        add = self.client.post(
            "/api/cron",
            json={
                "name": "demo-job",
                "schedule": "* * * * *",
                "action": "echo cron-e2e",
                "action_type": "command",
            },
        )
        self.assertEqual(add.status_code, 200)
        self.assertTrue(add.json()["ok"])

        jobs = self.client.get("/api/cron")
        self.assertEqual(jobs.status_code, 200)
        job = next(item for item in jobs.json()["jobs"] if item["name"] == "demo-job")
        self.assertTrue(job["enabled"])
        self.assertTrue(job["next_run"])

        run = self.client.post("/api/cron/demo-job/run")
        self.assertEqual(run.status_code, 200)
        self.assertEqual(run.json()["status"], "success")
        self.assertIn("cron-e2e", run.json()["output"])
        self.assertEqual(run.json()["delivery"]["status"], "logged")

        disable = self.client.post("/api/cron/demo-job/disable")
        self.assertEqual(disable.status_code, 200)
        self.assertTrue(disable.json()["ok"])
        job_after_disable = next(item for item in self.client.get("/api/cron").json()["jobs"] if item["name"] == "demo-job")
        self.assertFalse(job_after_disable["enabled"])
        self.assertEqual(job_after_disable["next_run"], "")

        enable = self.client.post("/api/cron/demo-job/enable")
        self.assertEqual(enable.status_code, 200)
        self.assertTrue(enable.json()["ok"])
        job_after_enable = next(item for item in self.client.get("/api/cron").json()["jobs"] if item["name"] == "demo-job")
        self.assertTrue(job_after_enable["enabled"])
        self.assertTrue(job_after_enable["next_run"])

        remove = self.client.delete("/api/cron/demo-job")
        self.assertEqual(remove.status_code, 200)
        self.assertTrue(remove.json()["ok"])
        remaining_names = [item["name"] for item in self.client.get("/api/cron").json()["jobs"]]
        self.assertNotIn("demo-job", remaining_names)


if __name__ == "__main__":
    unittest.main()
