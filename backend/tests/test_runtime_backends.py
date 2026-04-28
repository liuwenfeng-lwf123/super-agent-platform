import asyncio
import getpass
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router
from app.runtime_backend import RuntimeBackend
from app.docker_runtime_backend import DockerRuntimeBackend
from app.ssh_runtime_backend import SSHRuntimeBackend
from app.runtime_backends import LocalRuntimeBackend, RuntimeManager
from app.sandbox.manager import SandboxExecutor


class FakeRuntimeBackend:
    def __init__(self, root: str, name: str = "fake"):
        self.name = name
        self.kind = "test"
        self.description = "Fake runtime backend for tests"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "capabilities": {
                "execute": ["bash"],
                "workspace": True,
                "outputs": True,
                "file_history": True,
                "shadow_workspace": False,
            },
        }

    def _thread_root(self, thread_id: str) -> Path:
        path = self.root / (thread_id or "_default")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_thread_workspace(self, thread_id: str) -> str:
        return str(self._thread_root(thread_id))

    def get_workspace_dir(self, thread_id: str) -> str:
        path = self._thread_root(thread_id) / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def get_outputs_dir(self, thread_id: str) -> str:
        path = self._thread_root(thread_id) / "outputs"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def get_uploads_dir(self, thread_id: str) -> str:
        path = self._thread_root(thread_id) / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def resolve_workspace_path(self, thread_id: str, rel_path: str) -> str | None:
        return str((Path(self.get_workspace_dir(thread_id)) / rel_path).resolve())

    def resolve_outputs_path(self, thread_id: str, rel_path: str) -> str | None:
        return str((Path(self.get_outputs_dir(thread_id)) / rel_path).resolve())

    def get_file_history(self, thread_id: str, path: str | None = None, limit: int = 50) -> list[dict]:
        entries = [{"path": path or "demo.txt", "action": "modify", "timestamp": "2026-01-01T00:00:00"}]
        return entries[-limit:]

    async def execute_python(self, code: str, timeout: int | None = None, thread_id: str | None = None) -> dict:
        return {"success": True, "output": f"fake-python:{thread_id}", "error": "", "exit_code": 0}

    async def execute_javascript(self, code: str, timeout: int | None = None, thread_id: str | None = None) -> dict:
        return {"success": True, "output": f"fake-js:{thread_id}", "error": "", "exit_code": 0}

    async def execute_bash(self, command: str, timeout: int | None = None, thread_id: str | None = None) -> dict:
        return {"success": True, "output": f"fake-bash:{command}:{thread_id}", "error": "", "exit_code": 0}

    async def write_file(self, path: str, content: str, thread_id: str) -> dict:
        target = Path(self.get_workspace_dir(thread_id)) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"success": True, "path": str(target), "size": len(content)}

    async def read_file(self, path: str, thread_id: str) -> dict:
        target = Path(self.get_workspace_dir(thread_id)) / path
        if not target.exists():
            return {"success": False, "error": "File not found"}
        return {"success": True, "content": target.read_text(encoding="utf-8"), "path": str(target)}

    async def list_files(self, path: str = ".", thread_id: str = "") -> dict:
        target = Path(self.get_workspace_dir(thread_id)) / path
        target.mkdir(parents=True, exist_ok=True)
        entries = []
        for entry in target.iterdir():
            entries.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else 0,
            })
        entries.sort(key=lambda item: (not item["is_dir"], item["name"]))
        return {"success": True, "path": path, "entries": entries}

    async def write_file_bytes(self, path: str, data: bytes, thread_id: str) -> dict:
        target = Path(self.get_workspace_dir(thread_id)) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"success": True, "path": str(target), "size": len(data)}

    async def read_file_bytes(self, path: str, thread_id: str) -> dict:
        target = Path(self.get_workspace_dir(thread_id)) / path
        if not target.exists():
            return {"success": False, "error": "File not found"}
        data = target.read_bytes()
        return {"success": True, "path": str(target), "data": data, "size": len(data)}

    async def save_output(self, filename: str, content: str, thread_id: str) -> dict:
        target = Path(self.get_outputs_dir(thread_id)) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"success": True, "path": str(target), "filename": filename}


class _FakeDockerProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.returncode = -9


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ssh_e2e_port() -> int:
    raw = (os.getenv("SSH_RUNTIME_E2E_PORT") or "22").strip()
    try:
        return int(raw)
    except Exception:
        return 22


class TestDockerRuntimeBackend(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        os.makedirs("data", exist_ok=True)
        self.backend = DockerRuntimeBackend(sandbox=SandboxExecutor())

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()

    def test_describe_reports_availability(self):
        with patch("app.docker_runtime_backend.shutil.which", return_value=None):
            payload = self.backend.describe()
        self.assertEqual(payload["name"], "docker")
        self.assertFalse(payload["available"])
        self.assertIn("python", payload["images"])
        self.assertEqual(payload["constraints"]["memory"], "1g")
        self.assertTrue(payload["constraints"]["read_only_root"])
        self.assertFalse(payload["health"]["cli_available"])

    def test_backends_conform_to_runtime_backend_protocol(self):
        local_backend = LocalRuntimeBackend(SandboxExecutor())
        ssh_backend = SSHRuntimeBackend(sandbox=SandboxExecutor(), host="example.com")
        self.assertIsInstance(local_backend, RuntimeBackend)
        self.assertIsInstance(self.backend, RuntimeBackend)
        self.assertIsInstance(ssh_backend, RuntimeBackend)

    def test_health_reports_daemon_failure(self):
        with patch("app.docker_runtime_backend.shutil.which", return_value="/usr/bin/docker"):
            with patch.object(
                self.backend,
                "_run_docker_probe",
                side_effect=[_FakeCompletedProcess(returncode=1, stderr="daemon down")],
            ):
                status = self.backend.health_status(force_refresh=True)
        self.assertTrue(status["cli_available"])
        self.assertFalse(status["daemon_available"])
        self.assertEqual(status["error"], "daemon down")

    def test_health_reports_local_images(self):
        with patch("app.docker_runtime_backend.shutil.which", return_value="/usr/bin/docker"):
            with patch.object(
                self.backend,
                "_run_docker_probe",
                side_effect=[
                    _FakeCompletedProcess(returncode=0, stdout="26.1.0\n"),
                    _FakeCompletedProcess(returncode=0),
                    _FakeCompletedProcess(returncode=1),
                    _FakeCompletedProcess(returncode=0),
                ],
            ):
                status = self.backend.health_status(force_refresh=True)
        self.assertTrue(status["available"])
        self.assertTrue(status["daemon_available"])
        self.assertEqual(status["server_version"], "26.1.0")
        self.assertEqual(status["images_local"], {"python": True, "javascript": False, "bash": True})

    def test_prewarm_images_pulls_missing_images(self):
        backend = DockerRuntimeBackend(sandbox=SandboxExecutor(), bash_image="bash:5.2")
        with patch.object(
            backend,
            "health_status",
            side_effect=[
                {
                    "daemon_available": True,
                    "images_local": {"python": False, "javascript": True, "bash": False},
                },
                {
                    "daemon_available": True,
                    "images_local": {"python": True, "javascript": True, "bash": True},
                },
            ],
        ):
            with patch.object(backend, "docker_cli_path", return_value="/usr/bin/docker"):
                with patch.object(
                    backend,
                    "_run_docker_pull",
                    side_effect=[_FakeCompletedProcess(returncode=0), _FakeCompletedProcess(returncode=0)],
                ) as pull_mock:
                    result = backend.prewarm_images(force_refresh=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(pull_mock.call_count, 2)
        self.assertEqual(result["pulled_images"], [backend.python_image, backend.bash_image])
        snapshot = backend.observability_snapshot()
        self.assertEqual(snapshot["operations"]["prewarm_images"]["count"], 1)
        self.assertEqual(snapshot["operations"]["prewarm_images"]["success"], 1)

    def test_observability_snapshot_tracks_container_execution(self):
        with patch("app.docker_runtime_backend.shutil.which", return_value="/usr/bin/docker"):
            with patch(
                "app.docker_runtime_backend.asyncio.create_subprocess_exec",
                AsyncMock(return_value=_FakeDockerProcess(stdout=b"docker ok\n", stderr=b"", returncode=0)),
            ):
                result = asyncio.run(self.backend.execute_python("print('ok')", thread_id="docker-observe-thread"))
        self.assertTrue(result["success"])
        snapshot = self.backend.observability_snapshot()
        self.assertEqual(snapshot["operations"]["run_container"]["count"], 1)
        self.assertEqual(snapshot["operations"]["run_container"]["success"], 1)
        self.assertEqual(snapshot["recent_events"][-1]["metadata"]["image"], self.backend.python_image)

    def test_explicit_constraints_are_exposed_and_applied(self):
        backend = DockerRuntimeBackend(
            sandbox=SandboxExecutor(),
            network="none",
            memory="512m",
            cpus="0.5",
            pids_limit=64,
            read_only_root=True,
            tmpfs_size_mb=32,
            cap_drop_all=True,
            no_new_privileges=True,
        )
        payload = backend.describe()
        self.assertEqual(payload["constraints"]["network"], "none")
        self.assertEqual(payload["constraints"]["memory"], "512m")
        self.assertEqual(payload["constraints"]["cpus"], "0.5")
        args = backend._docker_run_args(
            docker_path="/usr/bin/docker",
            thread_root="/tmp/thread",
            container_cwd="/thread/workspace",
            env={"HOME": "/thread"},
        )
        self.assertIn("--network", args)
        self.assertIn("none", args)
        self.assertIn("--memory", args)
        self.assertIn("512m", args)
        self.assertIn("--cpus", args)
        self.assertIn("0.5", args)
        self.assertIn("--pids-limit", args)
        self.assertIn("64", args)
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop", args)
        self.assertIn("ALL", args)
        self.assertIn("--security-opt", args)
        self.assertIn("no-new-privileges", args)
        self.assertIn("--tmpfs", args)

    def test_execute_bash_returns_unavailable_when_cli_missing(self):
        with patch("app.docker_runtime_backend.shutil.which", return_value=None):
            result = asyncio.run(self.backend.execute_bash("echo hi", thread_id="docker-thread"))
        self.assertFalse(result["success"])
        self.assertIn("Docker CLI not available", result["error"])

    def test_execute_python_uses_docker_cli(self):
        calls: list[tuple] = []

        async def fake_exec(*args, **kwargs):
            calls.append((args, kwargs))
            return _FakeDockerProcess(stdout=b"docker ok\n", stderr=b"", returncode=0)

        with patch("app.docker_runtime_backend.shutil.which", return_value="/usr/bin/docker"):
            with patch("app.docker_runtime_backend.asyncio.create_subprocess_exec", side_effect=fake_exec):
                result = asyncio.run(self.backend.execute_python("print('ok')", thread_id="docker-thread"))

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "docker ok\n")
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0], "/usr/bin/docker")
        self.assertIn("run", args)
        self.assertIn(self.backend.python_image, args)
        self.assertIn("_sandbox_exec.py", args)
        self.assertIn("--memory", args)
        self.assertIn("--cpus", args)
        self.assertIn("--pids-limit", args)
        self.assertIn("--read-only", args)
        self.assertIn("--tmpfs", args)
        self.assertIn("HOME=/thread", args)
        self.assertTrue(str(kwargs["cwd"]).endswith("workspace"))


@unittest.skipUnless(shutil.which("docker"), "Docker CLI not available")
class TestDockerRuntimeBackendE2E(unittest.TestCase):
    def setUp(self):
        self._tempdir = None
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        self.backend = DockerRuntimeBackend(sandbox=SandboxExecutor())
        health = self.backend.health_status(force_refresh=True)
        if not health.get("available"):
            self._tempdir.cleanup()
            self.skipTest(health.get("error") or "Docker daemon unavailable")
        if not health.get("images_local", {}).get("python"):
            self._tempdir.cleanup()
            self.skipTest(f"Docker image not present locally: {self.backend.python_image}")
        os.chdir(self._tempdir.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._old_cwd)
        if self._tempdir is not None:
            self._tempdir.cleanup()

    def test_execute_python_in_real_docker(self):
        result = asyncio.run(self.backend.execute_python("print('docker-e2e')", thread_id="docker-e2e-thread"))
        self.assertTrue(result["success"], result)
        self.assertEqual(result["output"].strip(), "docker-e2e")


class TestRuntimeManager(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        os.makedirs("data", exist_ok=True)
        self.manager = RuntimeManager(
            mapping_path=Path("data") / "runtime-map.json",
            local_backend=LocalRuntimeBackend(SandboxExecutor()),
        )
        self.fake_backend = FakeRuntimeBackend(os.path.join(self._tempdir.name, "fake-runtime"))
        self.manager.register_backend(self.fake_backend)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()

    def test_assign_and_persist_thread_backend(self):
        result = self.manager.set_thread_backend("thread-1", "fake")
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["backend"], "fake")
        self.assertTrue(result["assigned"])

        restored = RuntimeManager(
            mapping_path=Path("data") / "runtime-map.json",
            local_backend=LocalRuntimeBackend(SandboxExecutor()),
        )
        restored.register_backend(FakeRuntimeBackend(os.path.join(self._tempdir.name, "fake-runtime-2")))
        restored_info = restored.get_thread_runtime("thread-1")
        self.assertEqual(restored_info["backend"], "fake")
        self.assertTrue(restored_info["assigned"])

    def test_execute_routes_to_assigned_backend(self):
        self.manager.set_thread_backend("thread-2", "fake")
        result = asyncio.run(self.manager.execute_bash("echo hi", thread_id="thread-2"))
        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "fake-bash:echo hi:thread-2")
        snapshot = self.manager.observability_snapshot()
        self.assertEqual(snapshot["backends"]["fake"]["operations"]["set_thread_backend"]["count"], 1)
        self.assertEqual(snapshot["backends"]["fake"]["operations"]["execute_bash"]["count"], 1)

    def test_execute_bash_failure_includes_runtime_hint(self):
        class FailingDockerBackend(FakeRuntimeBackend):
            def __init__(self, root: str):
                super().__init__(root, name="docker")
                self.kind = "container"
                self.description = "Failing docker backend"

            def describe(self) -> dict:
                return {
                    "name": self.name,
                    "kind": self.kind,
                    "description": self.description,
                    "available": False,
                    "health": {
                        "cli_available": True,
                        "daemon_available": False,
                        "images_local": {"python": False, "javascript": False, "bash": False},
                    },
                    "capabilities": {
                        "execute": ["bash"],
                        "workspace": True,
                        "outputs": True,
                        "file_history": True,
                        "shadow_workspace": False,
                    },
                }

            async def execute_bash(self, command: str, timeout: int | None = None, thread_id: str | None = None) -> dict:
                raise RuntimeError("daemon down")

        docker_backend = FailingDockerBackend(os.path.join(self._tempdir.name, "docker-runtime"))
        self.manager.register_backend(docker_backend)
        self.manager.set_thread_backend("thread-docker", "docker")

        with patch.object(self.manager, "_failover_candidates", return_value=[]):
            result = asyncio.run(self.manager.execute_bash("echo hi", thread_id="thread-docker"))

        self.assertFalse(result["success"])
        self.assertEqual(result["backend"], "docker")
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("Docker daemon 不可用", result["hint"])

    def test_execute_bash_failover_moves_to_local_on_runtime_backend_failure(self):
        class FailingDockerBackend(FakeRuntimeBackend):
            def __init__(self, root: str):
                super().__init__(root, name="docker")
                self.kind = "container"
                self.description = "Failing docker backend"

            def describe(self) -> dict:
                return {
                    "name": self.name,
                    "kind": self.kind,
                    "description": self.description,
                    "available": False,
                    "health": {
                        "cli_available": True,
                        "daemon_available": False,
                        "images_local": {"python": False, "javascript": False, "bash": False},
                    },
                    "capabilities": {
                        "execute": ["bash"],
                        "workspace": True,
                        "outputs": True,
                        "file_history": True,
                        "shadow_workspace": False,
                    },
                }

            async def execute_bash(self, command: str, timeout: int | None = None, thread_id: str | None = None) -> dict:
                return {"success": False, "output": "", "error": "Docker daemon unavailable", "exit_code": -1}

        docker_backend = FailingDockerBackend(os.path.join(self._tempdir.name, "docker-runtime-failover"))
        self.manager.register_backend(docker_backend)
        self.manager.set_thread_backend("thread-failover", "docker")

        local_backend = self.manager.get_backend_by_name("local")
        with patch.object(
            local_backend,
            "execute_bash",
            AsyncMock(return_value={"success": True, "output": "local ok", "error": "", "exit_code": 0}),
        ) as local_execute:
            result = asyncio.run(self.manager.execute_bash("echo hi", thread_id="thread-failover"))

        self.assertTrue(result["success"])
        self.assertEqual(result["backend"], "local")
        self.assertEqual(result["requested_backend"], "docker")
        self.assertEqual(result["fallback_from"], "docker")
        self.assertEqual(result["attempted_backends"], ["docker", "local"])
        self.assertEqual(result["failover_count"], 1)
        local_execute.assert_awaited_once()

    def test_execute_bash_does_not_failover_on_command_failure(self):
        class CommandFailingDockerBackend(FakeRuntimeBackend):
            def __init__(self, root: str):
                super().__init__(root, name="docker")
                self.kind = "container"
                self.description = "Command failing docker backend"

            def describe(self) -> dict:
                return {
                    "name": self.name,
                    "kind": self.kind,
                    "description": self.description,
                    "available": True,
                    "health": {
                        "cli_available": True,
                        "daemon_available": True,
                        "images_local": {"python": True, "javascript": True, "bash": True},
                    },
                    "capabilities": {
                        "execute": ["bash"],
                        "workspace": True,
                        "outputs": True,
                        "file_history": True,
                        "shadow_workspace": False,
                    },
                }

            async def execute_bash(self, command: str, timeout: int | None = None, thread_id: str | None = None) -> dict:
                return {"success": False, "output": "", "error": "bash: missing: command not found", "exit_code": 127}

        docker_backend = CommandFailingDockerBackend(os.path.join(self._tempdir.name, "docker-runtime-command-fail"))
        self.manager.register_backend(docker_backend)
        self.manager.set_thread_backend("thread-command-fail", "docker")

        local_backend = self.manager.get_backend_by_name("local")
        with patch.object(local_backend, "execute_bash", AsyncMock()) as local_execute:
            result = asyncio.run(self.manager.execute_bash("missing", thread_id="thread-command-fail"))

        self.assertFalse(result["success"])
        self.assertEqual(result["backend"], "docker")
        self.assertNotIn("fallback_from", result)
        local_execute.assert_not_awaited()

    def test_execute_bash_tool_surfaces_runtime_hint(self):
        from app.agents import tools as tools_module

        with patch.object(
            tools_module.runtime_manager,
            "execute_bash",
            AsyncMock(
                return_value={
                    "success": False,
                    "error": "daemon down",
                    "exit_code": -1,
                    "hint": "Docker daemon 不可用。请启动 Docker Desktop/daemon，或切换到 local/ssh 运行时。",
                }
            ),
        ):
            result = asyncio.run(tools_module.execute_bash.ainvoke({"command": "echo hi"}))

        self.assertIn("Hint:", result)
        self.assertIn("Docker daemon 不可用", result)

    def test_unknown_backend_rejected(self):
        result = self.manager.set_thread_backend("thread-3", "missing")
        self.assertIn("error", result)
        self.assertIn("available_backends", result)

    def test_auto_route_uses_policy_for_execution_and_workspace(self):
        result = self.manager.set_thread_backend(
            "thread-auto",
            "auto",
            policy={
                "name": "test_auto_route",
                "execute_order": ["fake", "local"],
                "workspace_order": ["local", "fake"],
            },
        )

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["backend"], "auto")
        self.assertEqual(result["effective_backend"], "fake")
        self.assertEqual(result["workspace_backend"], "local")
        self.assertEqual(result["outputs_backend"], "local")
        self.assertEqual(result["routing"]["mode"], "auto")
        self.assertEqual(result["routing"]["policy"]["name"], "test_auto_route")
        self.assertEqual(result["routing"]["selected"]["execute_bash"], "fake")
        self.assertEqual(result["routing"]["selected"]["execute_python"], "local")

        bash_result = asyncio.run(self.manager.execute_bash("echo hi", thread_id="thread-auto"))
        self.assertTrue(bash_result["success"])
        self.assertEqual(bash_result["output"], "fake-bash:echo hi:thread-auto")

        self.assertEqual(
            self.manager.get_backend_name("thread-auto", operation="execute_python", language="python"),
            "local",
        )
        local_backend = self.manager.get_backend_by_name("local")
        with patch.object(
            local_backend,
            "execute_python",
            AsyncMock(return_value={"success": True, "output": "auto-local\n", "error": "", "exit_code": 0}),
        ):
            python_result = asyncio.run(self.manager.execute_python("print('auto-local')", thread_id="thread-auto"))
        self.assertTrue(python_result["success"], python_result)
        self.assertEqual(python_result["output"].strip(), "auto-local")

        write_result = asyncio.run(self.manager.write_file("auto.txt", "hello auto", thread_id="thread-auto"))
        self.assertTrue(write_result["success"])
        self.assertTrue((Path(self.manager.get_workspace_dir("thread-auto")) / "auto.txt").is_file())

        snapshot = self.manager.observability_snapshot()
        self.assertEqual(snapshot["backends"]["fake"]["operations"]["set_thread_backend"]["count"], 1)
        self.assertEqual(snapshot["backends"]["fake"]["operations"]["execute_bash"]["count"], 1)
        self.assertEqual(snapshot["backends"]["local"]["operations"]["execute_python"]["count"], 1)
        self.assertEqual(snapshot["backends"]["local"]["operations"]["write_file"]["count"], 1)

    def test_auto_route_policy_persists_across_reload(self):
        self.manager.set_thread_backend(
            "thread-persist",
            "auto",
            policy={
                "name": "persisted_auto_route",
                "execute_order": ["fake", "local"],
                "workspace_order": ["local", "fake"],
            },
        )

        restored = RuntimeManager(
            mapping_path=Path("data") / "runtime-map.json",
            local_backend=LocalRuntimeBackend(SandboxExecutor()),
        )
        restored.register_backend(FakeRuntimeBackend(os.path.join(self._tempdir.name, "fake-runtime-restored")))

        restored_info = restored.get_thread_runtime("thread-persist")
        self.assertEqual(restored_info["backend"], "auto")
        self.assertEqual(restored_info["effective_backend"], "fake")
        self.assertEqual(restored_info["workspace_backend"], "local")
        self.assertEqual(restored_info["routing"]["policy"]["name"], "persisted_auto_route")
        self.assertEqual(restored.get_backend_name("thread-persist", operation="execute_bash", language="bash"), "fake")


class TestSSHRuntimeBackend(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        os.makedirs("data", exist_ok=True)
        self.backend = SSHRuntimeBackend(sandbox=SandboxExecutor())

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()

    def test_describe_reports_missing_host_configuration(self):
        with patch("app.ssh_runtime_backend.shutil.which", return_value="/usr/bin/ssh"):
            payload = self.backend.describe()
        self.assertEqual(payload["name"], "ssh")
        self.assertEqual(payload["kind"], "remote")
        self.assertFalse(payload["available"])
        self.assertFalse(payload["health"]["host_configured"])
        self.assertEqual(payload["health"]["error"], "SSH runtime host not configured")

    def test_health_reports_remote_capabilities(self):
        backend = SSHRuntimeBackend(sandbox=SandboxExecutor(), host="example.com")
        with patch("app.ssh_runtime_backend.shutil.which", return_value="/usr/bin/ssh"):
            with patch.object(
                backend,
                "_run_ssh_probe",
                side_effect=[
                    _FakeCompletedProcess(returncode=0, stdout="ok"),
                    _FakeCompletedProcess(returncode=0),
                    _FakeCompletedProcess(returncode=1),
                    _FakeCompletedProcess(returncode=0),
                    _FakeCompletedProcess(returncode=0),
                    _FakeCompletedProcess(returncode=1),
                ],
            ):
                status = backend.health_status(force_refresh=True)
        self.assertTrue(status["available"])
        self.assertTrue(status["connection_available"])
        self.assertEqual(
            status["remote_capabilities"],
            {"python": True, "javascript": False, "bash": True, "tar": True, "rsync": False},
        )

    def test_sync_to_remote_prefers_rsync_when_available(self):
        backend = SSHRuntimeBackend(sandbox=SandboxExecutor(), host="example.com")
        backend.health_status = lambda force_refresh=False: {
            "available": True,
            "error": "",
            "remote_capabilities": {"rsync": True},
        }
        with patch.object(backend, "rsync_cli_path", return_value="/usr/bin/rsync"):
            with patch.object(
                backend,
                "_run_local_process",
                AsyncMock(return_value={"success": True, "output": "", "error": "", "exit_code": 0}),
            ) as local_process:
                with patch.object(backend, "_run_shell_pipeline", AsyncMock()) as shell_pipeline:
                    result = asyncio.run(backend._sync_to_remote("ssh-thread"))
        self.assertTrue(result["success"])
        self.assertEqual(result["sync_strategy"], "rsync")
        local_process.assert_awaited_once()
        shell_pipeline.assert_not_awaited()
        snapshot = backend.observability_snapshot()
        self.assertEqual(snapshot["operations"]["sync_to_remote"]["count"], 1)
        self.assertEqual(snapshot["recent_events"][-1]["metadata"]["strategy"], "rsync")
        args = local_process.await_args.args[0]
        self.assertEqual(args[0], "/usr/bin/rsync")
        self.assertIn("--delete", args)
        self.assertIn("--rsync-path", args)

    def test_sync_to_remote_falls_back_to_tar_when_rsync_fails(self):
        backend = SSHRuntimeBackend(sandbox=SandboxExecutor(), host="example.com")
        backend.health_status = lambda force_refresh=False: {
            "available": True,
            "error": "",
            "remote_capabilities": {"rsync": True},
        }
        with patch.object(backend, "rsync_cli_path", return_value="/usr/bin/rsync"):
            with patch.object(
                backend,
                "_run_local_process",
                AsyncMock(return_value={"success": False, "output": "", "error": "rsync failed", "exit_code": 23}),
            ):
                with patch.object(
                    backend,
                    "_run_shell_pipeline",
                    AsyncMock(return_value={"success": True, "output": "", "error": "", "exit_code": 0}),
                ) as shell_pipeline:
                    result = asyncio.run(backend._sync_to_remote("ssh-thread"))
        self.assertTrue(result["success"])
        self.assertEqual(result["sync_strategy"], "tar")
        self.assertEqual(result["fallback_from"], "rsync")
        self.assertEqual(result["fallback_error"], "rsync failed")
        shell_pipeline.assert_awaited_once()

    def test_sync_from_remote_prefers_rsync_when_available(self):
        backend = SSHRuntimeBackend(sandbox=SandboxExecutor(), host="example.com")
        backend.health_status = lambda force_refresh=False: {
            "available": True,
            "error": "",
            "remote_capabilities": {"rsync": True},
        }
        with patch.object(backend, "rsync_cli_path", return_value="/usr/bin/rsync"):
            with patch.object(
                backend,
                "_run_local_process",
                AsyncMock(return_value={"success": True, "output": "", "error": "", "exit_code": 0}),
            ) as local_process:
                with patch.object(backend, "_run_shell_pipeline", AsyncMock()) as shell_pipeline:
                    result = asyncio.run(backend._sync_from_remote("ssh-thread"))
        self.assertTrue(result["success"])
        self.assertEqual(result["sync_strategy"], "rsync")
        local_process.assert_awaited_once()
        shell_pipeline.assert_not_awaited()
        args = local_process.await_args.args[0]
        self.assertEqual(args[0], "/usr/bin/rsync")
        self.assertIn("--delete", args)

    def test_execute_python_runs_sync_and_remote_flow(self):
        backend = SSHRuntimeBackend(sandbox=SandboxExecutor(), host="example.com")
        backend.health_status = lambda force_refresh=False: {"available": True, "error": ""}
        backend._sync_to_remote = AsyncMock(return_value={"success": True, "output": "", "error": "", "exit_code": 0})
        backend._run_remote_script = AsyncMock(return_value={"success": True, "output": "ssh ok\n", "error": "", "exit_code": 0})
        backend._sync_from_remote = AsyncMock(return_value={"success": True, "output": "", "error": "", "exit_code": 0})

        result = asyncio.run(backend.execute_python("print('ok')", thread_id="ssh-thread"))

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "ssh ok\n")
        backend._sync_to_remote.assert_awaited_once()
        backend._run_remote_script.assert_awaited_once()
        backend._sync_from_remote.assert_awaited_once()

    def test_execute_bash_returns_health_error_when_unavailable(self):
        backend = SSHRuntimeBackend(sandbox=SandboxExecutor(), host="example.com")
        backend.health_status = lambda force_refresh=False: {"available": False, "error": "ssh down"}

        result = asyncio.run(backend.execute_bash("echo hi", thread_id="ssh-thread"))

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "ssh down")


@unittest.skipUnless(shutil.which("ssh"), "SSH CLI not available")
class TestSSHRuntimeBackendE2E(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        self.addCleanup(lambda: os.chdir(self._old_cwd))
        self.addCleanup(self._tempdir.cleanup)
        os.makedirs("data", exist_ok=True)
        host = (os.getenv("SSH_RUNTIME_E2E_HOST") or "localhost").strip()
        user = (os.getenv("SSH_RUNTIME_E2E_USER") or getpass.getuser()).strip()
        identity_file = (os.getenv("SSH_RUNTIME_E2E_IDENTITY_FILE") or "").strip() or None
        strict_host_key_checking = (os.getenv("SSH_RUNTIME_E2E_STRICT_HOST_KEY_CHECKING") or "accept-new").strip()
        self.backend = SSHRuntimeBackend(
            sandbox=SandboxExecutor(),
            host=host,
            user=user,
            port=_ssh_e2e_port(),
            identity_file=identity_file,
            remote_base_dir=f"/tmp/hermes-ssh-runtime-e2e-{uuid.uuid4().hex}",
            strict_host_key_checking=strict_host_key_checking,
        )
        health = self.backend.health_status(force_refresh=True)
        if not health.get("available"):
            self.skipTest(health.get("error") or "SSH loopback unavailable")
        remote_capabilities = health.get("remote_capabilities") or {}
        missing = [name for name in ("python", "bash", "tar") if not remote_capabilities.get(name)]
        if missing:
            self.skipTest(f"Missing remote capabilities: {', '.join(missing)}")
        self.addCleanup(self._cleanup_remote_root)

    def _cleanup_remote_root(self):
        try:
            backend = getattr(self, "backend", None)
            if backend is not None:
                backend._run_ssh_probe("bash", "-lc", f"rm -rf {backend.remote_base_dir}")
        except Exception:
            pass

    def test_execute_python_syncs_remote_workspace_back(self):
        thread_id = "ssh-e2e-python-thread"
        result = asyncio.run(
            self.backend.execute_python(
                "from pathlib import Path\nPath('e2e.txt').write_text('from-ssh', encoding='utf-8')\nprint('ssh-e2e')",
                thread_id=thread_id,
            )
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["output"].strip(), "ssh-e2e")
        local_file = Path(self.backend.get_workspace_dir(thread_id)) / "e2e.txt"
        self.assertTrue(local_file.is_file())
        self.assertEqual(local_file.read_text(encoding="utf-8"), "from-ssh")

    def test_execute_bash_preserves_cwd_across_calls(self):
        thread_id = "ssh-e2e-bash-thread"
        first = asyncio.run(
            self.backend.execute_bash(
                "mkdir -p nested && cd nested && printf 'hello-ssh' > note.txt && pwd",
                thread_id=thread_id,
            )
        )
        self.assertTrue(first["success"], first)
        self.assertTrue(first["output"].strip().endswith("/workspace/nested"), first["output"])

        second = asyncio.run(self.backend.execute_bash("pwd && cat note.txt", thread_id=thread_id))

        self.assertTrue(second["success"], second)
        output_lines = [line.strip() for line in second["output"].splitlines() if line.strip()]
        self.assertGreaterEqual(len(output_lines), 2)
        self.assertTrue(output_lines[0].endswith("/workspace/nested"), second["output"])
        self.assertEqual(output_lines[-1], "hello-ssh")
        local_note = Path(self.backend.get_workspace_dir(thread_id)) / "nested" / "note.txt"
        self.assertTrue(local_note.is_file())
        self.assertEqual(local_note.read_text(encoding="utf-8"), "hello-ssh")


class TestRuntimeApi(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        os.makedirs("data", exist_ok=True)
        self.manager = RuntimeManager(
            mapping_path=Path("data") / "runtime-map.json",
            local_backend=LocalRuntimeBackend(SandboxExecutor()),
        )
        self.fake_backend = FakeRuntimeBackend(os.path.join(self._tempdir.name, "fake-runtime"))
        self.manager.register_backend(self.fake_backend)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self._patch = patch("app.api.chat.runtime_manager", self.manager)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        try:
            self.client.close()
        except Exception:
            pass
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()

    def test_runtime_endpoints_and_workspace_routing(self):
        runtimes = self.client.get("/api/runtimes")
        self.assertEqual(runtimes.status_code, 200)
        payload = runtimes.json()
        self.assertEqual(payload["default_backend"], "local")
        self.assertEqual(payload["routing_defaults"]["mode"], "auto")
        self.assertEqual(payload["observability"]["component"], "runtime_manager")
        self.assertEqual(sorted(item["name"] for item in payload["backends"]), ["docker", "fake", "local", "ssh"])
        docker_runtime = next(item for item in payload["backends"] if item["name"] == "docker")
        self.assertEqual(docker_runtime["kind"], "container")
        self.assertIn("manager", docker_runtime["observability"])
        self.assertIn("backend", docker_runtime["observability"])
        ssh_runtime = next(item for item in payload["backends"] if item["name"] == "ssh")
        self.assertEqual(ssh_runtime["kind"], "remote")
        self.assertIn("backend", ssh_runtime["observability"])

        initial = self.client.get("/api/threads/thread-9/runtime")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["backend"], "local")
        self.assertFalse(initial.json()["assigned"])
        self.assertIn("observability", initial.json())

        updated = self.client.post("/api/threads/thread-9/runtime", json={"backend": "fake"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["backend"], "fake")
        self.assertEqual(updated.json()["status"], "updated")

        docker_backend = self.manager.get_backend_by_name("docker")
        with patch.object(
            docker_backend,
            "prewarm_images",
            return_value={"success": True, "backend": "docker", "status": "ready", "pulled_images": []},
        ) as prewarm_mock:
            prewarm = self.client.post("/api/runtimes/docker/prewarm")
        self.assertEqual(prewarm.status_code, 200)
        self.assertTrue(prewarm.json()["success"])
        self.assertEqual(prewarm.json()["backend"], "docker")
        prewarm_mock.assert_called_once_with(force_refresh=True)

        runtimes_after_ops = self.client.get("/api/runtimes").json()
        self.assertEqual(runtimes_after_ops["observability"]["backends"]["fake"]["operations"]["set_thread_backend"]["count"], 1)
        self.assertEqual(runtimes_after_ops["observability"]["backends"]["docker"]["operations"]["prewarm_backend"]["count"], 1)

        write_result = asyncio.run(self.fake_backend.write_file("demo.txt", "hello runtime", thread_id="thread-9"))
        self.assertTrue(write_result["success"])

        listing = self.client.get("/api/workspace/thread-9/files")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["entries"][0]["name"], "demo.txt")

        read = self.client.get("/api/workspace/thread-9/read", params={"path": "demo.txt"})
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["content"], "hello runtime")

        cleared = self.client.delete("/api/threads/thread-9/runtime")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json()["status"], "cleared")
        self.assertEqual(cleared.json()["backend"], "local")

    def test_runtime_api_accepts_auto_backend_policy(self):
        updated = self.client.post(
            "/api/threads/thread-auto/runtime",
            json={
                "backend": "auto",
                "policy": {
                    "name": "api_auto_route",
                    "execute_order": ["fake", "local"],
                    "workspace_order": ["local", "fake"],
                },
            },
        )
        self.assertEqual(updated.status_code, 200)
        updated_payload = updated.json()
        self.assertEqual(updated_payload["backend"], "auto")
        self.assertEqual(updated_payload["effective_backend"], "fake")
        self.assertEqual(updated_payload["workspace_backend"], "local")
        self.assertEqual(updated_payload["routing"]["mode"], "auto")
        self.assertEqual(updated_payload["routing"]["policy"]["name"], "api_auto_route")
        self.assertEqual(updated_payload["routing"]["selected"]["execute_bash"], "fake")
        self.assertEqual(updated_payload["routing"]["selected"]["execute_python"], "local")

        fetched = self.client.get("/api/threads/thread-auto/runtime")
        self.assertEqual(fetched.status_code, 200)
        fetched_payload = fetched.json()
        self.assertEqual(fetched_payload["backend"], "auto")
        self.assertEqual(fetched_payload["effective_backend"], "fake")
        self.assertEqual(fetched_payload["routing"]["policy"]["name"], "api_auto_route")


if __name__ == "__main__":
    unittest.main()
