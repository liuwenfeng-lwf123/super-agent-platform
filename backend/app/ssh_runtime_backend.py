import asyncio
import logging
import os
import posixpath
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Any, Optional

from app.runtime_backend import SandboxDelegatingRuntimeBackend
from app.runtime_observability import RuntimeObservability
from app.sandbox.manager import SandboxExecutor



logger = logging.getLogger(__name__)
def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception as e:
        logger.debug("Suppressed error in ssh_runtime_backend: %s", e)
        return default


class SSHRuntimeBackend(SandboxDelegatingRuntimeBackend):
    _CWD_SENTINEL = "__HERMES_CWD__"
    _HEALTH_CHECK_TIMEOUT_SECONDS = 3

    def __init__(
        self,
        *,
        sandbox: SandboxExecutor | None = None,
        host: str | None = None,
        user: str | None = None,
        port: int | None = None,
        identity_file: str | None = None,
        remote_base_dir: str | None = None,
        connect_timeout_seconds: int | None = None,
        strict_host_key_checking: str | None = None,
    ):
        super().__init__(
            name="ssh",
            kind="remote",
            description="SSH CLI remote runtime backend",
            sandbox=sandbox,
        )
        self.host = (host if host is not None else os.getenv("SSH_RUNTIME_HOST", "")).strip()
        self.user = (user if user is not None else os.getenv("SSH_RUNTIME_USER", "")).strip()
        self.port = port if port is not None else _env_int("SSH_RUNTIME_PORT", 22)
        self.identity_file = (identity_file if identity_file is not None else os.getenv("SSH_RUNTIME_IDENTITY_FILE", "")).strip()
        self.remote_base_dir = (
            remote_base_dir if remote_base_dir is not None else os.getenv("SSH_RUNTIME_REMOTE_BASE_DIR", "~/hermes-runtime")
        ).strip()
        self.connect_timeout_seconds = connect_timeout_seconds if connect_timeout_seconds is not None else _env_int(
            "SSH_RUNTIME_CONNECT_TIMEOUT_SECONDS", 5
        )
        self.strict_host_key_checking = (
            strict_host_key_checking
            if strict_host_key_checking is not None
            else os.getenv("SSH_RUNTIME_STRICT_HOST_KEY_CHECKING", "accept-new")
        ).strip()
        self._health_cache_ttl_seconds = max(_env_int("SSH_RUNTIME_HEALTH_CACHE_TTL_SECONDS", 10), 0)
        self._health_cache_at = 0.0
        self._health_cache: dict[str, Any] | None = None
        self._thread_cwd: dict[str, str] = {}
        self._observability = RuntimeObservability(component=f"runtime_backend:{self.name}")

    def ssh_cli_path(self) -> str | None:
        return shutil.which("ssh")

    def rsync_cli_path(self) -> str | None:
        return shutil.which("rsync")

    def observability_snapshot(self) -> dict[str, Any]:
        return self._observability.snapshot_for_backend(self.name)

    def _normalized_thread_id(self, thread_id: Optional[str]) -> str:
        return (thread_id or "_default").strip() or "_default"

    def _target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def _ssh_common_args(self) -> list[str]:
        ssh_path = self.ssh_cli_path() or "ssh"
        args = [
            ssh_path,
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.connect_timeout_seconds}",
        ]
        if self.strict_host_key_checking:
            args.extend(["-o", f"StrictHostKeyChecking={self.strict_host_key_checking}"])
        if self.identity_file:
            args.extend(["-i", self.identity_file])
        if self.port > 0:
            args.extend(["-p", str(self.port)])
        return args

    def _ssh_base_args(self) -> list[str]:
        return [*self._ssh_common_args(), self._target()]

    def _run_ssh_probe(self, *remote_args: str) -> subprocess.CompletedProcess[str] | None:
        ssh_path = self.ssh_cli_path()
        if not ssh_path or not self.host:
            return None
        try:
            return subprocess.run(
                [*self._ssh_base_args(), *remote_args],
                capture_output=True,
                text=True,
                timeout=self._HEALTH_CHECK_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.debug("Suppressed error in ssh_runtime_backend: %s", e)
            return None

    def _probe_health(self) -> dict[str, Any]:
        ssh_path = self.ssh_cli_path()
        health = {
            "available": False,
            "cli_available": bool(ssh_path),
            "host_configured": bool(self.host),
            "target": self._target() if self.host else None,
            "connection_available": False,
            "remote_capabilities": {
                "python": False,
                "javascript": False,
                "bash": False,
                "tar": False,
                "rsync": False,
            },
            "error": "",
        }
        if not ssh_path:
            health["error"] = "SSH CLI not available"
            return health
        if not self.host:
            health["error"] = "SSH runtime host not configured"
            return health
        probe = self._run_ssh_probe("printf", "ok")
        if probe is None:
            health["error"] = "Failed to probe SSH connection"
            return health
        if probe.returncode != 0:
            health["error"] = (probe.stderr or probe.stdout or "SSH connection unavailable").strip()
            return health
        health["connection_available"] = True
        health["available"] = True
        capability_checks = {
            "python": "command -v python3 >/dev/null 2>&1",
            "javascript": "command -v node >/dev/null 2>&1",
            "bash": "command -v bash >/dev/null 2>&1",
            "tar": "command -v tar >/dev/null 2>&1",
            "rsync": "command -v rsync >/dev/null 2>&1",
        }
        for name, command in capability_checks.items():
            result = self._run_ssh_probe("bash", "-lc", command)
            health["remote_capabilities"][name] = bool(result and result.returncode == 0)
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
                "host_configured": status.get("host_configured"),
                "connection_available": status.get("connection_available"),
                "remote_capabilities": status.get("remote_capabilities"),
            },
        )
        return status

    def describe(self) -> dict[str, Any]:
        health = self.health_status()
        remote_capabilities = health.get("remote_capabilities") or {}
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "available": health.get("available", False),
            "ssh_cli": self.ssh_cli_path(),
            "health": health,
            "connection": {
                "host": self.host or None,
                "user": self.user or None,
                "port": self.port,
                "identity_file": self.identity_file or None,
                "remote_base_dir": self.remote_base_dir,
                "strict_host_key_checking": self.strict_host_key_checking or None,
            },
            "sync": {
                "strategy": "rsync" if self._rsync_supported(health) else "tar",
                "local_rsync_available": bool(self.rsync_cli_path()),
                "remote_rsync_available": bool(remote_capabilities.get("rsync")),
            },
            "capabilities": {
                "execute": ["python", "javascript", "bash"],
                "workspace": True,
                "outputs": True,
                "file_history": True,
                "shadow_workspace": True,
            },
        }

    def _remote_thread_root(self, thread_id: Optional[str]) -> str:
        return posixpath.join(self.remote_base_dir.rstrip("/"), self._normalized_thread_id(thread_id))

    def _remote_workspace_dir(self, thread_id: Optional[str]) -> str:
        return posixpath.join(self._remote_thread_root(thread_id), "workspace")

    def _remote_env_bridge(self, thread_id: Optional[str]) -> str:
        return posixpath.join(self._remote_thread_root(thread_id), "claude_env.sh")

    def _local_thread_root(self, thread_id: Optional[str]) -> str:
        return os.path.realpath(self.get_thread_workspace(self._normalized_thread_id(thread_id)))

    def _remote_shell_path(self, path: str) -> str:
        normalized = (path or "").strip() or "."
        if normalized in {"~", "$HOME", "${HOME}"}:
            return '"$HOME"'
        for prefix in ("~/", "$HOME/", "${HOME}/"):
            if normalized.startswith(prefix):
                remainder = normalized[len(prefix):].strip("/")
                if not remainder:
                    return '"$HOME"'
                parts = [part for part in remainder.split("/") if part]
                return '"$HOME"/' + "/".join(shlex.quote(part) for part in parts)
        return shlex.quote(normalized)

    def _rsync_remote_spec(self, path: str, *, directory: bool = False) -> str:
        suffix = "/" if directory else ""
        return f"{self._target()}:{self._remote_shell_path(path)}{suffix}"

    def _rsync_supported(self, health: dict[str, Any] | None = None) -> bool:
        status = health or self.health_status()
        return bool(self.rsync_cli_path()) and bool((status.get("remote_capabilities") or {}).get("rsync"))

    def _ssh_shell_invocation(self, remote_command: str) -> str:
        return " ".join(shlex.quote(part) for part in [*self._ssh_base_args(), remote_command])

    async def _run_local_process(self, args: list[str], timeout: Optional[int]) -> dict[str, Any]:
        timeout_value = timeout or getattr(self._sandbox, "timeout", 60)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return {
                "success": False,
                "output": "",
                "error": f"Command not available: {args[0]}",
                "exit_code": -1,
            }
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_value)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {
                "success": False,
                "output": "",
                "error": f"Execution timed out after {timeout_value}s",
                "exit_code": -1,
            }
        result = {
            "success": proc.returncode == 0,
            "output": stdout.decode("utf-8", errors="replace")[:20000],
            "error": stderr.decode("utf-8", errors="replace")[:5000],
            "exit_code": proc.returncode,
        }
        return result

    async def _run_shell_pipeline(self, command: str, timeout: Optional[int]) -> dict[str, Any]:
        timeout_value = timeout or getattr(self._sandbox, "timeout", 60)
        proc = await asyncio.create_subprocess_shell(
            f"set -o pipefail; {command}",
            executable="/bin/bash",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_value)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {
                "success": False,
                "output": "",
                "error": f"Execution timed out after {timeout_value}s",
                "exit_code": -1,
            }
        return {
            "success": proc.returncode == 0,
            "output": stdout.decode("utf-8", errors="replace")[:20000],
            "error": stderr.decode("utf-8", errors="replace")[:5000],
            "exit_code": proc.returncode,
        }

    async def _sync_to_remote_via_rsync(self, thread_id: Optional[str]) -> dict[str, Any]:
        rsync_path = self.rsync_cli_path()
        if not rsync_path:
            return {"success": False, "output": "", "error": "rsync CLI not available", "exit_code": -1}
        local_root = self._local_thread_root(thread_id)
        os.makedirs(local_root, exist_ok=True)
        remote_root = self._remote_thread_root(thread_id)
        args = [
            rsync_path,
            "-az",
            "--delete",
            "-e",
            shlex.join(self._ssh_common_args()),
            "--rsync-path",
            f"mkdir -p {self._remote_shell_path(remote_root)} && rsync",
            os.path.join(local_root, ""),
            self._rsync_remote_spec(remote_root, directory=True),
        ]
        return await self._run_local_process(args, timeout=None)

    async def _sync_to_remote_via_tar(self, thread_id: Optional[str]) -> dict[str, Any]:
        local_root = self._local_thread_root(thread_id)
        os.makedirs(local_root, exist_ok=True)
        remote_root = self._remote_thread_root(thread_id)
        remote_command = (
            f"rm -rf {self._remote_shell_path(remote_root)} && "
            f"mkdir -p {self._remote_shell_path(remote_root)} && "
            f"tar -xf - -C {self._remote_shell_path(remote_root)}"
        )
        return await self._run_shell_pipeline(
            f"tar -C {shlex.quote(local_root)} -cf - . | {self._ssh_shell_invocation(remote_command)}",
            timeout=None,
        )

    async def _sync_to_remote(self, thread_id: Optional[str]) -> dict[str, Any]:
        started = time.monotonic()
        health = self.health_status()
        if not health.get("available"):
            result = {
                "success": False,
                "output": "",
                "error": health.get("error", "SSH runtime unavailable"),
                "exit_code": -1,
            }
            self._observability.record(
                backend=self.name,
                operation="sync_to_remote",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                thread_id=self._normalized_thread_id(thread_id),
                error=result["error"],
                metadata={"strategy": "unavailable"},
            )
            return result
        rsync_error = ""
        if self._rsync_supported(health):
            rsync_result = await self._sync_to_remote_via_rsync(thread_id)
            if rsync_result.get("success"):
                rsync_result["sync_strategy"] = "rsync"
                self._observability.record(
                    backend=self.name,
                    operation="sync_to_remote",
                    success=True,
                    duration_ms=(time.monotonic() - started) * 1000,
                    thread_id=self._normalized_thread_id(thread_id),
                    metadata={"strategy": "rsync", "exit_code": rsync_result.get("exit_code")},
                )
                return rsync_result
            rsync_error = rsync_result.get("error", "rsync sync to remote failed")
        tar_result = await self._sync_to_remote_via_tar(thread_id)
        tar_result["sync_strategy"] = "tar"
        if rsync_error:
            tar_result["fallback_from"] = "rsync"
            if tar_result.get("success"):
                tar_result["fallback_error"] = rsync_error
            else:
                tar_result["error"] = "\n".join(part for part in [f"rsync: {rsync_error}", tar_result.get("error", "")] if part)
        self._observability.record(
            backend=self.name,
            operation="sync_to_remote",
            success=bool(tar_result.get("success")),
            duration_ms=(time.monotonic() - started) * 1000,
            thread_id=self._normalized_thread_id(thread_id),
            error=str(tar_result.get("error") or ""),
            metadata={
                "strategy": tar_result.get("sync_strategy"),
                "fallback_from": tar_result.get("fallback_from"),
                "exit_code": tar_result.get("exit_code"),
            },
        )
        return tar_result

    async def _sync_from_remote_via_rsync(self, thread_id: Optional[str]) -> dict[str, Any]:
        rsync_path = self.rsync_cli_path()
        if not rsync_path:
            return {"success": False, "output": "", "error": "rsync CLI not available", "exit_code": -1}
        local_root = self._local_thread_root(thread_id)
        os.makedirs(local_root, exist_ok=True)
        args = [
            rsync_path,
            "-az",
            "--delete",
            "-e",
            shlex.join(self._ssh_common_args()),
            self._rsync_remote_spec(self._remote_thread_root(thread_id), directory=True),
            os.path.join(local_root, ""),
        ]
        return await self._run_local_process(args, timeout=None)

    async def _sync_from_remote_via_tar(self, thread_id: Optional[str]) -> dict[str, Any]:
        local_root = self._local_thread_root(thread_id)
        os.makedirs(os.path.dirname(local_root), exist_ok=True)
        remote_root = self._remote_thread_root(thread_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            stage_root = os.path.join(temp_dir, "thread")
            os.makedirs(stage_root, exist_ok=True)
            remote_command = f"test -d {self._remote_shell_path(remote_root)} && tar -cf - -C {self._remote_shell_path(remote_root)} ."
            sync_result = await self._run_shell_pipeline(
                f"{self._ssh_shell_invocation(remote_command)} | tar -xf - -C {shlex.quote(stage_root)}",
                timeout=None,
            )
            if not sync_result.get("success"):
                return sync_result
            backup_root = f"{local_root}.backup-sync"
            if os.path.isdir(backup_root):
                shutil.rmtree(backup_root)
            restore_needed = os.path.isdir(local_root)
            if restore_needed:
                os.rename(local_root, backup_root)
            try:
                shutil.copytree(stage_root, local_root)
            except Exception as exc:
                if os.path.isdir(local_root):
                    shutil.rmtree(local_root)
                if os.path.isdir(backup_root):
                    os.rename(backup_root, local_root)
                return {"success": False, "output": "", "error": str(exc), "exit_code": -1}
            if os.path.isdir(backup_root):
                shutil.rmtree(backup_root)
            return sync_result

    async def _sync_from_remote(self, thread_id: Optional[str]) -> dict[str, Any]:
        started = time.monotonic()
        health = self.health_status()
        if not health.get("available"):
            result = {
                "success": False,
                "output": "",
                "error": health.get("error", "SSH runtime unavailable"),
                "exit_code": -1,
            }
            self._observability.record(
                backend=self.name,
                operation="sync_from_remote",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                thread_id=self._normalized_thread_id(thread_id),
                error=result["error"],
                metadata={"strategy": "unavailable"},
            )
            return result
        rsync_error = ""
        if self._rsync_supported(health):
            rsync_result = await self._sync_from_remote_via_rsync(thread_id)
            if rsync_result.get("success"):
                rsync_result["sync_strategy"] = "rsync"
                self._observability.record(
                    backend=self.name,
                    operation="sync_from_remote",
                    success=True,
                    duration_ms=(time.monotonic() - started) * 1000,
                    thread_id=self._normalized_thread_id(thread_id),
                    metadata={"strategy": "rsync", "exit_code": rsync_result.get("exit_code")},
                )
                return rsync_result
            rsync_error = rsync_result.get("error", "rsync sync from remote failed")
        tar_result = await self._sync_from_remote_via_tar(thread_id)
        tar_result["sync_strategy"] = "tar"
        if rsync_error:
            tar_result["fallback_from"] = "rsync"
            if tar_result.get("success"):
                tar_result["fallback_error"] = rsync_error
            else:
                tar_result["error"] = "\n".join(part for part in [f"rsync: {rsync_error}", tar_result.get("error", "")] if part)
        self._observability.record(
            backend=self.name,
            operation="sync_from_remote",
            success=bool(tar_result.get("success")),
            duration_ms=(time.monotonic() - started) * 1000,
            thread_id=self._normalized_thread_id(thread_id),
            error=str(tar_result.get("error") or ""),
            metadata={
                "strategy": tar_result.get("sync_strategy"),
                "fallback_from": tar_result.get("fallback_from"),
                "exit_code": tar_result.get("exit_code"),
            },
        )
        return tar_result

    async def _run_remote_script(self, script: str, timeout: Optional[int]) -> dict[str, Any]:
        started = time.monotonic()
        health = self.health_status()
        if not health.get("available"):
            result = {
                "success": False,
                "output": "",
                "error": health.get("error", "SSH runtime unavailable"),
                "exit_code": -1,
            }
            self._observability.record(
                backend=self.name,
                operation="run_remote_script",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                error=result["error"],
                metadata={"timeout": timeout},
            )
            return result
        timeout_value = timeout or getattr(self._sandbox, "timeout", 60)
        proc = await asyncio.create_subprocess_exec(
            *self._ssh_base_args(),
            f"bash -lc {shlex.quote(script)}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_value)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {
                "success": False,
                "output": "",
                "error": f"Execution timed out after {timeout_value}s",
                "exit_code": -1,
            }
        return {
            "success": proc.returncode == 0,
            "output": stdout.decode("utf-8", errors="replace")[:20000],
            "error": stderr.decode("utf-8", errors="replace")[:5000],
            "exit_code": proc.returncode,
        }

    def _remote_cwd(self, thread_id: Optional[str]) -> str:
        normalized_thread_id = self._normalized_thread_id(thread_id)
        local_workspace = os.path.realpath(self.get_workspace_dir(normalized_thread_id))
        saved = self._thread_cwd.get(normalized_thread_id)
        if not saved:
            return self._remote_workspace_dir(normalized_thread_id)
        saved_real = os.path.realpath(saved)
        try:
            rel_path = os.path.relpath(saved_real, local_workspace)
        except Exception as e:
            logger.debug("Suppressed error in ssh_runtime_backend: %s", e)
            return self._remote_workspace_dir(normalized_thread_id)
        if rel_path in {".", ""} or rel_path.startswith(".."):
            return self._remote_workspace_dir(normalized_thread_id)
        return posixpath.join(self._remote_workspace_dir(normalized_thread_id), *rel_path.split(os.sep))

    def _apply_remote_cwd(self, thread_id: Optional[str], remote_cwd: str):
        normalized_thread_id = self._normalized_thread_id(thread_id)
        local_workspace = os.path.realpath(self.get_workspace_dir(normalized_thread_id))
        remote_workspace = self._remote_workspace_dir(normalized_thread_id)
        normalized_remote_cwd = remote_cwd.strip()
        if normalized_remote_cwd == remote_workspace:
            self._thread_cwd[normalized_thread_id] = local_workspace
            return
        prefix = f"{remote_workspace}/"
        if not normalized_remote_cwd.startswith(prefix):
            return
        rel_path = normalized_remote_cwd[len(prefix):]
        local_path = os.path.realpath(os.path.join(local_workspace, *rel_path.split("/")))
        if os.path.isdir(local_path):
            self._thread_cwd[normalized_thread_id] = local_path

    async def execute_python(
        self,
        code: str,
        timeout: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> dict:
        normalized_thread_id = self._normalized_thread_id(thread_id)
        work_dir = self.get_workspace_dir(normalized_thread_id)
        exec_path = os.path.join(work_dir, "_sandbox_exec.py")
        with open(exec_path, "w", encoding="utf-8") as handle:
            handle.write(code)
        sync_result = await self._sync_to_remote(normalized_thread_id)
        if not sync_result.get("success"):
            return sync_result
        result = await self._run_remote_script(
            f"cd {self._remote_shell_path(self._remote_workspace_dir(normalized_thread_id))} && python3 _sandbox_exec.py",
            timeout=timeout,
        )
        sync_back = await self._sync_from_remote(normalized_thread_id)
        if not sync_back.get("success"):
            result["success"] = False
            result["error"] = "\n".join(part for part in [result.get("error", ""), sync_back.get("error", "")] if part)
        return result

    async def execute_javascript(
        self,
        code: str,
        timeout: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> dict:
        normalized_thread_id = self._normalized_thread_id(thread_id)
        work_dir = self.get_workspace_dir(normalized_thread_id)
        exec_path = os.path.join(work_dir, "_sandbox_exec.js")
        with open(exec_path, "w", encoding="utf-8") as handle:
            handle.write(code)
        sync_result = await self._sync_to_remote(normalized_thread_id)
        if not sync_result.get("success"):
            return sync_result
        result = await self._run_remote_script(
            f"cd {self._remote_shell_path(self._remote_workspace_dir(normalized_thread_id))} && node _sandbox_exec.js",
            timeout=timeout,
        )
        sync_back = await self._sync_from_remote(normalized_thread_id)
        if not sync_back.get("success"):
            result["success"] = False
            result["error"] = "\n".join(part for part in [result.get("error", ""), sync_back.get("error", "")] if part)
        return result

    async def execute_bash(
        self,
        command: str,
        timeout: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> dict:
        normalized_thread_id = self._normalized_thread_id(thread_id)
        sync_result = await self._sync_to_remote(normalized_thread_id)
        if not sync_result.get("success"):
            return sync_result
        prelude = (
            f'if [ -n "{self._remote_env_bridge(normalized_thread_id)}" ] '
            f'&& [ -f {self._remote_shell_path(self._remote_env_bridge(normalized_thread_id))} ]; then '
            f'set -a; . {self._remote_shell_path(self._remote_env_bridge(normalized_thread_id))} >/dev/null 2>&1; set +a; fi'
        )
        wrapped = (
            f"cd {self._remote_shell_path(self._remote_cwd(normalized_thread_id))}\n"
            f"{prelude}\n"
            f"{command}\n"
            "__hermes_rc=$?\n"
            '__hermes_pwd=$(pwd)\n'
            'case "$__hermes_pwd" in "$HOME") __hermes_pwd="~" ;; "$HOME"/*) __hermes_pwd="~/${__hermes_pwd#\"$HOME\"/}" ;; esac\n'
            f'echo "\\n{self._CWD_SENTINEL}$__hermes_pwd"; exit $__hermes_rc'
        )
        result = await self._run_remote_script(wrapped, timeout=timeout)
        remote_cwd = None
        raw_output = result.get("output", "")
        if self._CWD_SENTINEL in raw_output:
            marker_index = raw_output.rfind(self._CWD_SENTINEL)
            remote_cwd = raw_output[marker_index + len(self._CWD_SENTINEL):].strip().split("\n")[0].strip()
            result["output"] = raw_output[:marker_index].rstrip("\n")
        sync_back = await self._sync_from_remote(normalized_thread_id)
        if remote_cwd:
            self._apply_remote_cwd(normalized_thread_id, remote_cwd)
        if not sync_back.get("success"):
            result["success"] = False
            result["error"] = "\n".join(part for part in [result.get("error", ""), sync_back.get("error", "")] if part)
        return result
