import asyncio
import getpass
import os
import shutil
import tempfile
import unittest

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.cost_tracker import CostTracker
from app.docker_runtime_backend import DockerRuntimeBackend
from app.models.provider import llm_provider
from app.sandbox.manager import SandboxExecutor
from app.ssh_runtime_backend import SSHRuntimeBackend


class TestDockerLiveMatrix(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_DOCKER_LIVE"), "Live Docker matrix: set RUN_DOCKER_LIVE=1")
    def test_docker_runtime_health_and_execution(self):
        if shutil.which("docker") is None:
            self.skipTest("docker CLI not available")
        with tempfile.TemporaryDirectory() as tempdir:
            old_cwd = os.getcwd()
            os.chdir(tempdir)
            try:
                os.makedirs("data", exist_ok=True)
                backend = DockerRuntimeBackend(sandbox=SandboxExecutor())
                health = backend.health_status(force_refresh=True)
                if not health.get("available"):
                    self.skipTest(health.get("error") or "docker daemon unavailable")
                if not health.get("images_local", {}).get("python"):
                    self.skipTest(f"docker image not present locally: {backend.python_image}")
                result = asyncio.run(backend.execute_python("print('docker-live-matrix')", thread_id="docker-live-matrix"))
                self.assertTrue(result["success"], result)
                self.assertEqual(result["output"].strip(), "docker-live-matrix")
            finally:
                os.chdir(old_cwd)


class TestSSHLiveMatrix(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_SSH_LIVE"), "Live SSH matrix: set RUN_SSH_LIVE=1")
    def test_ssh_runtime_health_and_execution(self):
        if shutil.which("ssh") is None:
            self.skipTest("ssh CLI not available")
        with tempfile.TemporaryDirectory() as tempdir:
            old_cwd = os.getcwd()
            os.chdir(tempdir)
            try:
                os.makedirs("data", exist_ok=True)
                backend = SSHRuntimeBackend(
                    sandbox=SandboxExecutor(),
                    host=(os.getenv("SSH_RUNTIME_E2E_HOST") or "localhost").strip(),
                    user=(os.getenv("SSH_RUNTIME_E2E_USER") or getpass.getuser()).strip(),
                    port=int(os.getenv("SSH_RUNTIME_E2E_PORT", os.getenv("SSH_RUNTIME_PORT", "22"))),
                    identity_file=(os.getenv("SSH_RUNTIME_E2E_IDENTITY_FILE") or "").strip() or None,
                    remote_base_dir=os.getenv("SSH_RUNTIME_E2E_REMOTE_BASE_DIR", f"/tmp/hermes-ssh-live-matrix-{os.getpid()}"),
                    strict_host_key_checking=(os.getenv("SSH_RUNTIME_E2E_STRICT_HOST_KEY_CHECKING") or "accept-new").strip(),
                )
                health = backend.health_status(force_refresh=True)
                if not health.get("available"):
                    self.skipTest(health.get("error") or "ssh connection unavailable")
                remote_capabilities = health.get("remote_capabilities") or {}
                missing = [name for name in ("python", "bash", "tar") if not remote_capabilities.get(name)]
                if missing:
                    self.skipTest(f"missing remote capabilities: {', '.join(missing)}")
                result = asyncio.run(backend.execute_python("print('ssh-live-matrix')", thread_id="ssh-live-matrix"))
                self.assertTrue(result["success"], result)
                self.assertEqual(result["output"].strip(), "ssh-live-matrix")
            finally:
                try:
                    backend._run_ssh_probe("bash", "-lc", f"rm -rf {backend.remote_base_dir}")
                except Exception:
                    pass
                os.chdir(old_cwd)


class TestRealLLMLiveMatrix(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.environ.get("REAL_LLM_E2E"), "Live LLM matrix: set REAL_LLM_E2E=1")
    async def test_real_llm_usage_metadata_roundtrip(self):
        model_name = os.getenv("REAL_LLM_MATRIX_MODEL") or os.getenv("PROMPT_CACHE_TEST_MODEL") or None
        model = llm_provider.get_chat_model(model_name, streaming=False)
        try:
            response = await model.ainvoke(
                [
                    SystemMessage(content="Reply concisely."),
                    HumanMessage(content="Reply with a short pong-style acknowledgement."),
                ]
            )
            text = response.content if hasattr(response, "content") else str(response)
            self.assertTrue(str(text).strip())
            tracker = CostTracker()
            tracker.start_tracking(model=model_name or "default", thread_id="real-llm-matrix", mode="standard")
            usage = tracker.add_tokens_from_api_response(response)
            tracker.finish_tracking()
            self.assertGreater(usage.get("input_tokens", 0), 0)
            self.assertGreater(usage.get("output_tokens", 0), 0)
        finally:
            await llm_provider.aclose_model(model)


class TestPromptCacheLiveMatrix(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.environ.get("RUN_PROMPT_CACHE_E2E"), "Prompt cache matrix: set RUN_PROMPT_CACHE_E2E=1")
    async def test_prompt_cache_provider_reports_cache_tokens_when_supported(self):
        model_name = os.getenv("PROMPT_CACHE_TEST_MODEL") or os.getenv("REAL_LLM_MATRIX_MODEL") or None
        model = llm_provider.get_chat_model(model_name, streaming=False)
        try:
            shared_context = "\n".join(f"Stable rule {i}: keep formatting deterministic." for i in range(300))
            messages = [
                SystemMessage(content=f"You are a deterministic assistant.\n{shared_context}"),
                HumanMessage(content="Summarize the stability rules in one short sentence."),
            ]

            first = await model.ainvoke(messages)
            second = await model.ainvoke(messages)

            tracker_first = CostTracker()
            tracker_first.start_tracking(model=model_name or "default", thread_id="prompt-cache-1", mode="standard")
            usage_first = tracker_first.add_tokens_from_api_response(first)
            tracker_first.finish_tracking()

            tracker_second = CostTracker()
            tracker_second.start_tracking(model=model_name or "default", thread_id="prompt-cache-2", mode="standard")
            usage_second = tracker_second.add_tokens_from_api_response(second)
            tracker_second.finish_tracking()

            cache_total_first = usage_first.get("cache_creation_tokens", 0) + usage_first.get("cache_read_tokens", 0)
            cache_total_second = usage_second.get("cache_creation_tokens", 0) + usage_second.get("cache_read_tokens", 0)
            if cache_total_first == 0 and cache_total_second == 0:
                self.skipTest("provider did not expose prompt cache token telemetry for this model")
            self.assertGreaterEqual(cache_total_second, 0)
            self.assertGreater(
                cache_total_first + cache_total_second,
                0,
            )
        finally:
            await llm_provider.aclose_model(model)


if __name__ == "__main__":
    unittest.main()
