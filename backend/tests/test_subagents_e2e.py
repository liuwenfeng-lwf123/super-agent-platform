import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router
from app.agents.subagents import SubagentManager


class SubagentTestMixin:
    def setUp(self):
        import app.agents.subagents as sub_mod

        self.sub_mod = sub_mod
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        self._old_paths = (
            sub_mod.DATA_DIR,
            sub_mod.AGENTS_DIR,
            sub_mod.AGENT_TRANSCRIPTS_DIR,
            sub_mod.AGENT_MEMORY_DIR,
        )
        sub_mod.DATA_DIR = os.path.join(self._tempdir.name, "data")
        sub_mod.AGENTS_DIR = os.path.join(sub_mod.DATA_DIR, "agents")
        sub_mod.AGENT_TRANSCRIPTS_DIR = os.path.join(sub_mod.DATA_DIR, "agent_transcripts")
        sub_mod.AGENT_MEMORY_DIR = os.path.join(sub_mod.DATA_DIR, "agent_memory")
        os.makedirs(sub_mod.DATA_DIR, exist_ok=True)
        os.makedirs(sub_mod.AGENTS_DIR, exist_ok=True)
        os.makedirs(sub_mod.AGENT_TRANSCRIPTS_DIR, exist_ok=True)
        os.makedirs(sub_mod.AGENT_MEMORY_DIR, exist_ok=True)
        self.manager = SubagentManager()

    def tearDown(self):
        self.sub_mod.DATA_DIR, self.sub_mod.AGENTS_DIR, self.sub_mod.AGENT_TRANSCRIPTS_DIR, self.sub_mod.AGENT_MEMORY_DIR = self._old_paths
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()


class TestSubagentManagerE2E(SubagentTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_spawn_foreground_records_tool_loop_and_transcript(self):
        ok, _ = self.manager.create_agent("tool-loop", prompt="Use the allowed tool", tools=["Allowed"])
        self.assertTrue(ok)

        captured: dict[str, object] = {}

        class FakeAgent:
            async def astream_events(self, payload, version="v2"):
                captured["messages"] = payload["messages"]
                yield {"event": "on_tool_start", "name": "Allowed"}
                yield {"event": "on_tool_end", "name": "Allowed", "data": {"output": "tool output"}}
                yield {
                    "event": "on_chat_model_end",
                    "data": {"output": types.SimpleNamespace(content="TASK_COMPLETE via tool")},
                }

        fake_provider_module = types.ModuleType("app.models.provider")

        async def fake_aclose_model(model):
            return None

        fake_provider_module.llm_provider = types.SimpleNamespace(
            get_fallback_model_names=lambda model=None: [model],
            get_chat_model=lambda *args, **kwargs: object(),
            should_retry_with_fallback=lambda error: False,
            aclose_model=fake_aclose_model,
        )

        fake_tools_module = types.ModuleType("app.agents.tools")
        fake_tools_module.get_all_tools = lambda **kwargs: [
            types.SimpleNamespace(name="Allowed"),
            types.SimpleNamespace(name="Blocked"),
        ]
        fake_tools_module.set_thread_context = lambda thread_id: captured.setdefault("thread_context", thread_id)

        fake_langchain_agents = types.ModuleType("langchain.agents")

        def create_agent(model, tools):
            captured["tool_names"] = [tool.name for tool in tools]
            return FakeAgent()

        fake_langchain_agents.create_agent = create_agent
        fake_langchain = types.ModuleType("langchain")
        fake_langchain.agents = fake_langchain_agents

        fake_messages_module = types.ModuleType("langchain_core.messages")

        class Message:
            def __init__(self, content):
                self.content = content

        fake_messages_module.SystemMessage = Message
        fake_messages_module.HumanMessage = Message
        fake_messages_module.AIMessage = Message
        fake_langchain_core = types.ModuleType("langchain_core")
        fake_langchain_core.messages = fake_messages_module

        with patch.dict(
            sys.modules,
            {
                "app.models.provider": fake_provider_module,
                "app.agents.tools": fake_tools_module,
                "langchain": fake_langchain,
                "langchain.agents": fake_langchain_agents,
                "langchain_core": fake_langchain_core,
                "langchain_core.messages": fake_messages_module,
            },
            clear=False,
        ):
            instance = await self.manager.spawn("tool-loop", "inspect repository")

        self.assertEqual(captured["tool_names"], ["Allowed"])
        self.assertEqual(instance.status, "completed")
        self.assertEqual(instance.result_summary, "TASK_COMPLETE via tool")
        roles = [entry["role"] for entry in instance.transcript]
        self.assertIn("system", roles)
        self.assertIn("user", roles)
        self.assertIn("tool", roles)
        self.assertIn("tool_result", roles)
        self.assertIn("assistant", roles)
        self.assertIn("info", roles)
        transcript_path = Path(self.sub_mod.AGENT_TRANSCRIPTS_DIR) / f"{instance.agent_id}.jsonl"
        self.assertTrue(transcript_path.exists())
        persisted = [json.loads(line) for line in transcript_path.read_text().splitlines()]
        self.assertTrue(any(entry["role"] == "tool" for entry in persisted))
        self.assertTrue(any(entry.get("content") == "TASK_COMPLETE via tool" for entry in persisted))

    async def test_subagent_alias_tools_are_normalized_and_permission_mode_reaches_runtime(self):
        ok, _ = self.manager.create_agent(
            "alias-worker",
            prompt="Use a read tool",
            tools=["Read"],
            disallowed_tools=["Write"],
            permission_mode="auto",
        )
        self.assertTrue(ok)

        captured: dict[str, object] = {}

        class FakeAgent:
            async def astream_events(self, payload, version="v2"):
                from app.agents.tool_runtime import get_runtime_context

                ctx = get_runtime_context()
                captured["runtime_mode"] = ctx.mode
                captured["runtime_thread_id"] = ctx.thread_id
                yield {
                    "event": "on_chat_model_end",
                    "data": {"output": types.SimpleNamespace(content="TASK_COMPLETE alias")},
                }

        fake_provider_module = types.ModuleType("app.models.provider")

        async def fake_aclose_model(model):
            return None

        fake_provider_module.llm_provider = types.SimpleNamespace(
            get_fallback_model_names=lambda model=None: [model],
            get_chat_model=lambda *args, **kwargs: object(),
            should_retry_with_fallback=lambda error: False,
            aclose_model=fake_aclose_model,
        )

        fake_tools_module = types.ModuleType("app.agents.tools")
        fake_tools_module.get_all_tools = lambda **kwargs: [
            types.SimpleNamespace(name="read_file"),
            types.SimpleNamespace(name="write_file"),
        ]
        fake_tools_module.set_thread_context = lambda thread_id: captured.setdefault("tool_thread_context", thread_id)

        fake_langchain_agents = types.ModuleType("langchain.agents")

        def create_agent(model, tools):
            captured["tool_names"] = [tool.name for tool in tools]
            return FakeAgent()

        fake_langchain_agents.create_agent = create_agent
        fake_langchain = types.ModuleType("langchain")
        fake_langchain.agents = fake_langchain_agents

        fake_messages_module = types.ModuleType("langchain_core.messages")

        class Message:
            def __init__(self, content):
                self.content = content

        fake_messages_module.SystemMessage = Message
        fake_messages_module.HumanMessage = Message
        fake_messages_module.AIMessage = Message
        fake_langchain_core = types.ModuleType("langchain_core")
        fake_langchain_core.messages = fake_messages_module

        with patch.dict(
            sys.modules,
            {
                "app.models.provider": fake_provider_module,
                "app.agents.tools": fake_tools_module,
                "langchain": fake_langchain,
                "langchain.agents": fake_langchain_agents,
                "langchain_core": fake_langchain_core,
                "langchain_core.messages": fake_messages_module,
            },
            clear=False,
        ):
            instance = await self.manager.spawn("alias-worker", "inspect file")

        self.assertEqual(captured["tool_names"], ["read_file"])
        self.assertEqual(captured["runtime_mode"], "auto")
        self.assertEqual(captured["runtime_thread_id"], instance.session_id)
        self.assertEqual(captured["tool_thread_context"], instance.session_id)

    async def test_background_instance_can_resume_after_completion(self):
        ok, _ = self.manager.create_agent("background-worker", prompt="Process tasks")
        self.assertTrue(ok)

        started = asyncio.Event()
        release = asyncio.Event()
        call_count = 0

        async def fake_agent_turn(instance, system_prompt, task_prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                started.set()
                await release.wait()
                return "TASK_COMPLETE first run"
            return "TASK_COMPLETE resumed"

        with patch.object(self.manager, "_agent_turn", side_effect=fake_agent_turn):
            instance = await self.manager.spawn("background-worker", "first task", background=True)
            await asyncio.wait_for(started.wait(), timeout=1)
            running_ids = {item["agent_id"] for item in self.manager.get_background_tasks()}
            self.assertIn(instance.agent_id, running_ids)

            ok, message = self.manager.send_message(instance.agent_id, "resume now")
            self.assertFalse(ok)
            self.assertIn("still running", message)

            release.set()
            await asyncio.wait_for(instance.task, timeout=1)
            self.assertEqual(instance.status, "completed")

            ok, message = self.manager.send_message(instance.agent_id, "resume now")
            self.assertTrue(ok)
            self.assertIn(instance.agent_id, message)
            self.assertIsNotNone(instance.task)
            await asyncio.wait_for(instance.task, timeout=1)

        assistant_messages = [entry["content"] for entry in instance.transcript if entry["role"] == "assistant"]
        user_messages = [entry["content"] for entry in instance.transcript if entry["role"] == "user"]
        self.assertEqual(assistant_messages, ["TASK_COMPLETE first run", "TASK_COMPLETE resumed"])
        self.assertEqual(user_messages, ["first task", "resume now"])

    async def test_spawn_stops_after_first_non_empty_response(self):
        ok, _ = self.manager.create_agent("single-pass-worker", prompt="Answer once", max_turns=3)
        self.assertTrue(ok)

        call_count = 0

        async def fake_agent_turn(instance, system_prompt, task_prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "4"
            return "[Subagent completed task: fallback] (LLM unavailable: Connection error.)"

        with patch.object(self.manager, "_agent_turn", side_effect=fake_agent_turn):
            instance = await self.manager.spawn("single-pass-worker", "What is 2+2?")

        self.assertEqual(call_count, 1)
        self.assertEqual(instance.status, "completed")
        self.assertEqual(instance.result_summary, "4")
        assistant_messages = [entry["content"] for entry in instance.transcript if entry["role"] == "assistant"]
        self.assertEqual(assistant_messages, ["4"])

    async def test_agent_turn_retries_with_fallback_model(self):
        captured: dict[str, object] = {"models": []}

        class FakeAgent:
            def __init__(self, model):
                self._model = model

            async def astream_events(self, payload, version="v2"):
                if self._model.name == "primary-model":
                    raise RuntimeError("429 rate limit")
                yield {
                    "event": "on_chat_model_end",
                    "data": {"output": types.SimpleNamespace(content="TASK_COMPLETE fallback success")},
                }

        fake_provider_module = types.ModuleType("app.models.provider")

        async def fake_aclose_model(model):
            return None

        def fake_get_chat_model(model=None, streaming=False, **kwargs):
            captured["models"].append(model)
            return types.SimpleNamespace(name=model)

        fake_provider_module.llm_provider = types.SimpleNamespace(
            get_fallback_model_names=lambda model=None: ["primary-model", "fallback-model"],
            get_chat_model=fake_get_chat_model,
            should_retry_with_fallback=lambda error: "429" in str(error),
            aclose_model=fake_aclose_model,
        )

        fake_tools_module = types.ModuleType("app.agents.tools")
        fake_tools_module.get_all_tools = lambda **kwargs: []
        fake_tools_module.set_thread_context = lambda thread_id: captured.setdefault("thread_context", thread_id)

        fake_langchain_agents = types.ModuleType("langchain.agents")
        fake_langchain_agents.create_agent = lambda model, tools: FakeAgent(model)
        fake_langchain = types.ModuleType("langchain")
        fake_langchain.agents = fake_langchain_agents

        fake_messages_module = types.ModuleType("langchain_core.messages")

        class Message:
            def __init__(self, content):
                self.content = content

        fake_messages_module.SystemMessage = Message
        fake_messages_module.HumanMessage = Message
        fake_messages_module.AIMessage = Message
        fake_langchain_core = types.ModuleType("langchain_core")
        fake_langchain_core.messages = fake_messages_module

        config = self.sub_mod.SubagentConfig(
            name="fallback-worker",
            prompt="Retry with fallback",
            model="primary-model",
        )
        instance = self.sub_mod.SubagentInstance(
            agent_id="fallback-inst-001",
            config=config,
            session_id="fallback-session",
            status="running",
            started_at="now",
            transcript=[{"role": "user", "content": "recover from 429", "timestamp": "now"}],
        )

        with patch.dict(
            sys.modules,
            {
                "app.models.provider": fake_provider_module,
                "app.agents.tools": fake_tools_module,
                "langchain": fake_langchain,
                "langchain.agents": fake_langchain_agents,
                "langchain_core": fake_langchain_core,
                "langchain_core.messages": fake_messages_module,
            },
            clear=False,
        ):
            output = await self.manager._agent_turn(instance, "You are resilient.", "recover from 429")

        self.assertEqual(captured["models"], ["primary-model", "fallback-model"])
        self.assertIn("fallback success", output)

    async def test_spawn_team_runs_background_agents_and_preserves_team_membership(self):
        ok, _ = self.manager.create_team("reviewers", ["explore", "plan"])
        self.assertTrue(ok)
        self.assertEqual(self.manager.list_teams()["reviewers"], ["explore", "plan"])

        async def fake_agent_turn(instance, system_prompt, task_prompt):
            await asyncio.sleep(0.05)
            return f"TASK_COMPLETE {task_prompt}"

        with patch.object(self.manager, "_agent_turn", side_effect=fake_agent_turn):
            instances = await self.manager.spawn_team(
                "reviewers",
                [
                    {"agent": "explore", "prompt": "scan files"},
                    {"agent": "plan", "prompt": "draft plan"},
                ],
                parent_session_id="parent-session",
            )
            self.assertEqual(len(instances), 2)
            running_ids = {item["agent_id"] for item in self.manager.get_background_tasks()}
            self.assertTrue({instance.agent_id for instance in instances}.issubset(running_ids))
            await asyncio.gather(*(instance.task for instance in instances if instance.task is not None))

        self.assertTrue(all(instance.is_background for instance in instances))
        self.assertTrue(all(instance.parent_session_id == "parent-session" for instance in instances))
        self.assertTrue(all(instance.status == "completed" for instance in instances))

    async def test_worktree_cleanup_removes_isolated_checkout(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")

        repo_dir = Path(self._tempdir.name) / "repo"
        repo_dir.mkdir()
        self._init_git_repo(repo_dir)

        original_cwd = os.getcwd()
        os.chdir(repo_dir)
        try:
            ok, _ = self.manager.create_agent("isolated-worker", prompt="Work in a worktree", isolation="worktree")
            self.assertTrue(ok)

            async def fake_agent_turn(instance, system_prompt, task_prompt):
                Path("generated.txt").write_text("from isolated worktree", encoding="utf-8")
                return "TASK_COMPLETE isolated"

            with patch.object(self.manager, "_agent_turn", side_effect=fake_agent_turn):
                instance = await self.manager.spawn("isolated-worker", "modify repository")

            self.assertEqual(instance.status, "completed")
            self.assertTrue(instance.workdir)
            workdir = instance.workdir
            branch = instance.worktree_branch
            self.assertTrue(Path(workdir).exists())
            self.assertTrue((Path(workdir) / "generated.txt").exists())

            ok, message = self.manager.cleanup_worktree(instance.agent_id, remove_branch=True)
            self.assertTrue(ok)
            self.assertIn(instance.agent_id, message)
            self.assertFalse(Path(workdir).exists())
            branch_list = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(branch_list.stdout.strip(), "")
        finally:
            os.chdir(original_cwd)

    def _init_git_repo(self, repo_dir: Path):
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_NAME": "Cascade Test",
                "GIT_AUTHOR_EMAIL": "cascade@test.local",
                "GIT_COMMITTER_NAME": "Cascade Test",
                "GIT_COMMITTER_EMAIL": "cascade@test.local",
            },
        )
        subprocess.run(["git", "init"], cwd=repo_dir, env=env, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Cascade Test"], cwd=repo_dir, env=env, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "cascade@test.local"], cwd=repo_dir, env=env, check=True, capture_output=True, text=True)
        (repo_dir / "README.md").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo_dir, env=env, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, env=env, check=True, capture_output=True, text=True)


class TestSubagentApiE2E(SubagentTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)
        self._subagent_patch = patch.object(self.sub_mod, "subagent_manager", self.manager)
        self._subagent_patch.start()

    def tearDown(self):
        self._subagent_patch.stop()
        try:
            self.client.close()
        except Exception:
            pass
        super().tearDown()

    def test_api_spawn_resume_and_team_flow(self):
        ok, _ = self.manager.create_agent("api-agent", prompt="Handle API subagent flow")
        self.assertTrue(ok)

        async def fake_agent_turn(instance, system_prompt, task_prompt):
            return "TASK_COMPLETE from api"

        with patch.object(self.manager, "_agent_turn", side_effect=fake_agent_turn):
            spawn = self.client.post(
                "/api/subagents/spawn",
                json={"agent_name": "api-agent", "task_prompt": "hello from api"},
            )
            self.assertEqual(spawn.status_code, 200)
            agent_id = spawn.json()["agent_id"]
            self.assertEqual(spawn.json()["status"], "completed")

            instance = self.client.get(f"/api/subagents/instance/{agent_id}")
            self.assertEqual(instance.status_code, 200)
            self.assertEqual(instance.json()["status"], "completed")

            message = self.client.post(
                f"/api/subagents/{agent_id}/message",
                json={"message": "follow up"},
            )
            self.assertEqual(message.status_code, 200)
            self.assertTrue(message.json()["ok"])

            resumed = self.client.get(f"/api/subagents/instance/{agent_id}")
            self.assertEqual(resumed.status_code, 200)
            assistant_count = sum(1 for entry in resumed.json()["transcript"] if entry["role"] == "assistant")
            self.assertEqual(assistant_count, 2)

        team = self.client.post(
            "/api/subagents/team",
            json={"name": "api-team", "agents": ["api-agent", "explore"]},
        )
        self.assertEqual(team.status_code, 200)
        self.assertTrue(team.json()["ok"])

        teams = self.client.get("/api/subagents/teams")
        self.assertEqual(teams.status_code, 200)
        self.assertEqual(teams.json()["teams"]["api-team"], ["api-agent", "explore"])


if __name__ == "__main__":
    unittest.main()
