import asyncio
import logging
import os
import posixpath
import shutil
import subprocess
import time
from typing import Any, Optional

from app.runtime_backend import SandboxDelegatingRuntimeBackend
from app.runtime_observability import RuntimeObservability
from app.sandbox.manager import SandboxExecutor



logger = logging.getLogger(__name__)
def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception as e:
        logger.debug("Suppressed error in docker_runtime_backend: %s", e)
        return default


class DockerRuntimeBackend(SandboxDelegatingRuntimeBackend):
    _CWD_SENTINEL = "__HERMES_CWD__"
    _THREAD_ROOT = "/thread"
    _WORKSPACE_ROOT = "/thread/workspace"
    _ENV_BRIDGE = "/thread/claude_env.sh"
    _HEALTH_CHECK_TIMEOUT_SECONDS = 2

    def __init__(
        self,
        *,
        sandbox: SandboxExecutor | None = None,
        python_image: str | None = None,
        node_image: str | None = None,
        bash_image: str | None = None,
        network: str | None = None,
        memory: str | None = None,
        cpus: str | float | None = None,
        pids_limit: int | None = None,
        read_only_root: bool | None = None,
        tmpfs_size_mb: int | None = None,
        cap_drop_all: bool | None = None,
        no_new_privileges: bool | None = None,
    ):
        super().__init__(
            name="docker",
            kind="container",
            description="Docker CLI runtime backend",
            sandbox=sandbox,
        )
        self._thread_cwd: dict[str, str] = {}
        self.python_image = python_image or os.getenv("DOCKER_RUNTIME_PYTHON_IMAGE", "python:3.11-slim")
        self.node_image = node_image or os.getenv("DOCKER_RUNTIME_NODE_IMAGE", "node:20-bookworm-slim")
        self.bash_image = bash_image or os.getenv("DOCKER_RUNTIME_BASH_IMAGE", self.python_image)
        self.network = (network if network is not None else os.getenv("DOCKER_RUNTIME_NETWORK", "bridge")).strip()
        self.memory = (memory if memory is not None else os.getenv("DOCKER_RUNTIME_MEMORY", "1g")).strip()
        self.cpus = str(cpus if cpus is not None else os.getenv("DOCKER_RUNTIME_CPUS", "1.0")).strip()
        self.pids_limit = pids_limit if pids_limit is not None else _env_int("DOCKER_RUNTIME_PIDS_LIMIT", 256)
        self.read_only_root = read_only_root if read_only_root is not None else _env_flag("DOCKER_RUNTIME_READ_ONLY_ROOT", True)
        self.tmpfs_size_mb = tmpfs_size_mb if tmpfs_size_mb is not None else _env_int("DOCKER_RUNTIME_TMPFS_SIZE_MB", 256)
        self.cap_drop_all = cap_drop_all if cap_drop_all is not None else _env_flag("DOCKER_RUNTIME_CAP_DROP_ALL", True)
        self.no_new_privileges = no_new_privileges if no_new_privileges is not None else _env_flag("DOCKER_RUNTIME_NO_NEW_PRIVILEGES", True)
        self._health_cache_ttl_seconds = max(_env_int("DOCKER_RUNTIME_HEALTH_CACHE_TTL_SECONDS", 10), 0)
        self._health_cache_at = 0.0
        self._health_cache: dict[str, Any] | None = None
        self._pull_timeout_seconds = max(_env_int("DOCKER_RUNTIME_PREWARM_TIMEOUT_SECONDS", 900), 1)
        self._observability = RuntimeObservability(component=f"runtime_backend:{self.name}")

    def docker_cli_path(self) -> str | None:
        return shutil.which("docker")

    def observability_snapshot(self) -> dict[str, Any]:
        return self._observability.snapshot_for_backend(self.name)

    def _run_docker_probe(self, docker_path: str, *args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                [docker_path, *args],
                capture_output=True,
                text=True,
                timeout=self._HEALTH_CHECK_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.debug("Suppressed error in docker_runtime_backend: %s", e)
            return None

    def _probe_health(self) -> dict[str, Any]:
        docker_path = self.docker_cli_path()
        health = {
            "available": False,
            "cli_available": bool(docker_path),
            "daemon_available": False,
            "server_version": None,
            "error": "Docker CLI not available" if not docker_path else "",
            "images_local": {
                "python": False,
                "javascript": False,
                "bash": False,
            },
        }
        if not docker_path:
            return health
        info_result = self._run_docker_probe(docker_path, "info", "--format", "{{.ServerVersion}}")
        if info_result is None:
            health["error"] = "Failed to probe Docker daemon"
            return health
        if info_result.returncode != 0:
            health["error"] = (info_result.stderr or info_result.stdout or "Docker daemon unavailable").strip()
            return health
        health["daemon_available"] = True
        health["available"] = True
        health["server_version"] = info_result.stdout.strip() or None
        image_map = {
            "python": self.python_image,
            "javascript": self.node_image,
            "bash": self.bash_image,
        }
        for name, image in image_map.items():
            inspect_result = self._run_docker_probe(docker_path, "image", "inspect", image)
            health["images_local"][name] = bool(inspect_result and inspect_result.returncode == 0)
        return health

    def health_status(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force_refresh and self._health_cache is not None:
            if self._health_cache_ttl_seconds <= 0 or now - self._health_cache_at < self._health_cache_ttl_seconds:
                return dict(self._health_cache)
        started = time.monotonic()
        status = self._probe_health()
        self._health_cache = dict(status)
        self._health_cache_at = now
        self._observability.record(
            backend=self.name,
            operation="health_status",
            success=bool(status.get("available")),
            duration_ms=(time.monotonic() - started) * 1000,
            error=str(status.get("error") or ""),
            metadata={
                "force_refresh": force_refresh,
                "cli_available": status.get("cli_available"),
                "daemon_available": status.get("daemon_available"),
                "images_local": status.get("images_local"),
            },
        )
        return status

    def _run_docker_pull(self, docker_path: str, image: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                [docker_path, "pull", image],
                capture_output=True,
                text=True,
                timeout=self._pull_timeout_seconds,
            )
        except Exception as e:
            logger.debug("Suppressed error in docker_runtime_backend: %s", e)
            return None

    def prewarm_images(self, *, force_refresh: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        health = self.health_status(force_refresh=force_refresh)
        docker_path = self.docker_cli_path()
        if not docker_path:
            result = {
                "success": False,
                "backend": self.name,
                "status": "unavailable",
                "error": health.get("error") or "Docker CLI not available",
            }
            self._observability.record(
                backend=self.name,
                operation="prewarm_images",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                error=result["error"],
                metadata={"force_refresh": force_refresh, "status": result["status"]},
            )
            return result
        if not health.get("daemon_available"):
            result = {
                "success": False,
                "backend": self.name,
                "status": "unavailable",
                "error": health.get("error") or "Docker daemon unavailable",
            }
            self._observability.record(
                backend=self.name,
                operation="prewarm_images",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                error=result["error"],
                metadata={"force_refresh": force_refresh, "status": result["status"]},
            )
            return result
        image_map = {
            "python": self.python_image,
            "javascript": self.node_image,
            "bash": self.bash_image,
        }
        missing_roles = [role for role, present in (health.get("images_local") or {}).items() if not present]
        missing_images = []
        seen_images: set[str] = set()
        for role in missing_roles:
            image = image_map.get(role, "")
            if image and image not in seen_images:
                missing_images.append(image)
                seen_images.add(image)
        if not missing_images:
            result = {
                "success": True,
                "backend": self.name,
                "status": "ready",
                "pulled_images": [],
                "images_local": dict(health.get("images_local") or {}),
            }
            self._observability.record(
                backend=self.name,
                operation="prewarm_images",
                success=True,
                duration_ms=(time.monotonic() - started) * 1000,
                metadata={"force_refresh": force_refresh, "status": result["status"], "pulled_images": []},
            )
            return result
        pulled_images: list[str] = []
        pull_errors: dict[str, str] = {}
        for image in missing_images:
            result = self._run_docker_pull(docker_path, image)
            if result is None:
                pull_errors[image] = "Failed to start docker pull"
                continue
            if result.returncode != 0:
                pull_errors[image] = (result.stderr or result.stdout or "docker pull failed").strip()
                continue
            pulled_images.append(image)
        refreshed = self.health_status(force_refresh=True)
        remaining_missing = [role for role, present in (refreshed.get("images_local") or {}).items() if not present]
        success = not pull_errors and not remaining_missing
        result = {
            "success": success,
            "backend": self.name,
            "status": "ready" if success else ("partial" if pulled_images else "failed"),
            "pulled_images": pulled_images,
            "pull_errors": pull_errors,
            "images_local": dict(refreshed.get("images_local") or {}),
            "missing_roles": remaining_missing,
        }
        self._observability.record(
            backend=self.name,
            operation="prewarm_images",
            success=success,
            duration_ms=(time.monotonic() - started) * 1000,
            error="; ".join(f"{image}: {error}" for image, error in sorted(pull_errors.items())),
            metadata={
                "force_refresh": force_refresh,
                "status": result["status"],
                "pulled_images": pulled_images,
                "missing_roles": remaining_missing,
            },
        )
        return result

    def describe(self) -> dict[str, Any]:
        health = self.health_status()
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "available": health.get("available", False),
            "docker_cli": self.docker_cli_path(),
            "health": health,
            "images": {
                "python": self.python_image,
                "javascript": self.node_image,
                "bash": self.bash_image,
            },
            "constraints": {
                "network": self.network,
                "memory": self.memory,
                "cpus": self.cpus,
                "pids_limit": self.pids_limit,
                "read_only_root": self.read_only_root,
                "tmpfs_size_mb": self.tmpfs_size_mb,
                "cap_drop_all": self.cap_drop_all,
                "no_new_privileges": self.no_new_privileges,
            },
            "capabilities": {
                "execute": ["python", "javascript", "bash"],
                "workspace": True,
                "outputs": True,
                "file_history": True,
                "shadow_workspace": True,
            },
        }

    def _normalized_thread_id(self, thread_id: Optional[str]) -> str:
        return (thread_id or "_default").strip() or "_default"

    def _is_within_root(self, root_dir: str, candidate: str) -> bool:
        try:
            root_real = os.path.normcase(os.path.realpath(root_dir))
            candidate_real = os.path.normcase(os.path.realpath(candidate))
            return os.path.commonpath([root_real, candidate_real]) == root_real
        except Exception as e:
            logger.debug("Suppressed error in docker_runtime_backend: %s", e)
            return False

    def _thread_root(self, thread_id: Optional[str]) -> str:
        return os.path.realpath(self.get_thread_workspace(self._normalized_thread_id(thread_id)))

    def _workspace_dir(self, thread_id: Optional[str]) -> str:
        return os.path.realpath(self.get_workspace_dir(self._normalized_thread_id(thread_id)))

    def _host_cwd(self, thread_id: Optional[str], work_dir: str) -> str:
        normalized_thread_id = self._normalized_thread_id(thread_id)
        saved = self._thread_cwd.get(normalized_thread_id)
        if saved and os.path.isdir(saved) and self._is_within_root(work_dir, saved):
            return os.path.realpath(saved)
        return work_dir

    def _container_cwd(self, host_cwd: str, work_dir: str) -> str:
        host_real = os.path.realpath(host_cwd)
        work_real = os.path.realpath(work_dir)
        if not self._is_within_root(work_real, host_real):
            return self._WORKSPACE_ROOT
        rel_path = os.path.relpath(host_real, work_real)
        if rel_path in {".", ""}:
            return self._WORKSPACE_ROOT
        return posixpath.join(self._WORKSPACE_ROOT, *rel_path.split(os.sep))

    def _host_path_from_container(self, container_cwd: str, work_dir: str) -> str | None:
        normalized = container_cwd.strip()
        if normalized == self._WORKSPACE_ROOT:
            return work_dir
        prefix = f"{self._WORKSPACE_ROOT}/"
        if not normalized.startswith(prefix):
            return None
        rel_path = normalized[len(prefix):]
        candidate = os.path.realpath(os.path.join(work_dir, *rel_path.split("/")))
        if not self._is_within_root(work_dir, candidate):
            return None
        return candidate

    def _docker_unavailable_result(self) -> dict[str, Any]:
        return {
            "success": False,
            "output": "",
            "error": "Docker CLI not available",
            "exit_code": -1,
        }

    def _container_env(self, env: dict[str, str] | None = None) -> dict[str, str]:
        container_env = {
            "HOME": self._THREAD_ROOT,
            "TMPDIR": "/tmp",
            "XDG_CACHE_HOME": f"{self._THREAD_ROOT}/.cache",
            "PIP_CACHE_DIR": f"{self._THREAD_ROOT}/.cache/pip",
            "PYTHONPYCACHEPREFIX": f"{self._THREAD_ROOT}/.cache/pycache",
            "npm_config_cache": f"{self._THREAD_ROOT}/.cache/npm",
            "npm_config_userconfig": f"{self._THREAD_ROOT}/.npmrc",
        }
        container_env.update(env or {})
        return container_env

    def _docker_run_args(
        self,
        *,
        docker_path: str,
        thread_root: str,
        container_cwd: str,
        env: dict[str, str],
    ) -> list[str]:
        args = [docker_path, "run", "--rm", "-i"]
        if self.network:
            args.extend(["--network", self.network])
        if self.memory:
            args.extend(["--memory", self.memory])
        if self.cpus:
            args.extend(["--cpus", self.cpus])
        if self.pids_limit > 0:
            args.extend(["--pids-limit", str(self.pids_limit)])
        if self.read_only_root:
            args.append("--read-only")
        if self.cap_drop_all:
            args.extend(["--cap-drop", "ALL"])
        if self.no_new_privileges:
            args.extend(["--security-opt", "no-new-privileges"])
        if self.tmpfs_size_mb > 0:
            args.extend(["--tmpfs", f"/tmp:rw,noexec,nosuid,size={self.tmpfs_size_mb * 1024 * 1024}"])
        args.extend(["-v", f"{thread_root}:{self._THREAD_ROOT}:rw", "-w", container_cwd])
        for key, value in sorted(env.items()):
            args.extend(["-e", f"{key}={value}"])
        return args

    async def _run_container(
        self,
        *,
        image: str,
        command_args: list[str],
        timeout: Optional[int],
        thread_id: Optional[str],
        env: dict[str, str] | None = None,
        capture_cwd: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()
        docker_path = self.docker_cli_path()
        if not docker_path:
            result = self._docker_unavailable_result()
            self._observability.record(
                backend=self.name,
                operation="run_container",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                thread_id=thread_id,
                error=result["error"],
                metadata={"image": image, "capture_cwd": capture_cwd},
            )
            return result
        normalized_thread_id = self._normalized_thread_id(thread_id)
        work_dir = self._workspace_dir(normalized_thread_id)
        thread_root = self._thread_root(normalized_thread_id)
        host_cwd = self._host_cwd(normalized_thread_id, work_dir)
        container_cwd = self._container_cwd(host_cwd, work_dir)
        timeout_value = timeout or getattr(self._sandbox, "timeout", 60)
        container_env = self._container_env(env)
        args = self._docker_run_args(
            docker_path=docker_path,
            thread_root=thread_root,
            container_cwd=container_cwd,
            env=container_env,
        )
        args.extend([image, *command_args])
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=dict(os.environ),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_value)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                result = {
                    "success": False,
                    "output": "",
                    "error": f"Execution timed out after {timeout_value}s",
                    "exit_code": -1,
                }
                self._observability.record(
                    backend=self.name,
                    operation="run_container",
                    success=False,
                    duration_ms=(time.monotonic() - started) * 1000,
                    thread_id=normalized_thread_id,
                    error=result["error"],
                    metadata={"image": image, "capture_cwd": capture_cwd, "container_cwd": container_cwd, "exit_code": -1},
                )
                return result
        except FileNotFoundError:
            result = self._docker_unavailable_result()
            self._observability.record(
                backend=self.name,
                operation="run_container",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                thread_id=normalized_thread_id,
                error=result["error"],
                metadata={"image": image, "capture_cwd": capture_cwd, "container_cwd": container_cwd},
            )
            return result
        raw_output = stdout.decode("utf-8", errors="replace")
        if capture_cwd and self._CWD_SENTINEL in raw_output:
            marker_index = raw_output.rfind(self._CWD_SENTINEL)
            container_path = raw_output[marker_index + len(self._CWD_SENTINEL):].strip().split("\n")[0].strip()
            raw_output = raw_output[:marker_index].rstrip("\n")
            host_path = self._host_path_from_container(container_path, work_dir)
            if host_path and os.path.isdir(host_path):
                self._thread_cwd[normalized_thread_id] = host_path
        result = {
            "success": proc.returncode == 0,
            "output": raw_output[:20000],
            "error": stderr.decode("utf-8", errors="replace")[:5000],
            "exit_code": proc.returncode,
        }
        self._observability.record(
            backend=self.name,
            operation="run_container",
            success=bool(result.get("success")),
            duration_ms=(time.monotonic() - started) * 1000,
            thread_id=normalized_thread_id,
            error=str(result.get("error") or ""),
            metadata={
                "image": image,
                "capture_cwd": capture_cwd,
                "container_cwd": container_cwd,
                "exit_code": result.get("exit_code"),
            },
        )
        return result

    async def execute_python(
        self,
        code: str,
        timeout: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> dict[str, Any]:
        work_dir = self._workspace_dir(thread_id)
        exec_path = os.path.join(work_dir, "_sandbox_exec.py")
        with open(exec_path, "w", encoding="utf-8") as handle:
            handle.write(code)
        return await self._run_container(
            image=self.python_image,
            command_args=["python3", "_sandbox_exec.py"],
            timeout=timeout,
            thread_id=thread_id,
        )

    async def execute_javascript(
        self,
        code: str,
        timeout: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> dict[str, Any]:
        work_dir = self._workspace_dir(thread_id)
        exec_path = os.path.join(work_dir, "_sandbox_exec.js")
        with open(exec_path, "w", encoding="utf-8") as handle:
            handle.write(code)
        return await self._run_container(
            image=self.node_image,
            command_args=["node", "_sandbox_exec.js"],
            timeout=timeout,
            thread_id=thread_id,
        )

    async def execute_bash(
        self,
        command: str,
        timeout: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> dict[str, Any]:
        prelude = 'if [ -n "$CLAUDE_ENV_FILE" ] && [ -f "$CLAUDE_ENV_FILE" ]; then set -a; . "$CLAUDE_ENV_FILE" >/dev/null 2>&1; set +a; fi'
        wrapped = (
            f"{prelude}\n"
            f"{command}\n"
            f'__hermes_rc=$?; echo "\\n{self._CWD_SENTINEL}$(pwd)"; exit $__hermes_rc'
        )
        return await self._run_container(
            image=self.bash_image,
            command_args=["bash", "-lc", wrapped],
            timeout=timeout,
            thread_id=thread_id,
            env={"CLAUDE_ENV_FILE": self._ENV_BRIDGE},
            capture_cwd=True,
        )
