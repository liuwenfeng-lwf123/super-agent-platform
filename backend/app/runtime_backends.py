import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from app.docker_runtime_backend import DockerRuntimeBackend
from app.runtime_observability import RuntimeObservability
from app.ssh_runtime_backend import SSHRuntimeBackend
from app.runtime_backend import RuntimeBackend, SandboxDelegatingRuntimeBackend
from app.sandbox.manager import SandboxExecutor



logger = logging.getLogger(__name__)
class LocalRuntimeBackend(SandboxDelegatingRuntimeBackend):
    def __init__(self, sandbox: SandboxExecutor | None = None):
        super().__init__(
            name="local",
            kind="builtin",
            description="Built-in local sandbox executor",
            sandbox=sandbox,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "capabilities": {
                "execute": ["python", "javascript", "bash"],
                "workspace": True,
                "outputs": True,
                "file_history": True,
                "shadow_workspace": True,
            },
        }


class RuntimeManager:
    def __init__(
        self,
        *,
        mapping_path: str | Path | None = None,
        local_backend: LocalRuntimeBackend | None = None,
        docker_backend: DockerRuntimeBackend | None = None,
        ssh_backend: SSHRuntimeBackend | None = None,
    ):
        self.mapping_path = Path(mapping_path or Path("data") / "thread_runtime_backends.json")
        self.default_backend_name = "local"
        self._backends: dict[str, RuntimeBackend] = {}
        self._thread_backends: dict[str, str] = {}
        self._thread_routing_policies: dict[str, dict[str, Any]] = {}
        self._shadow_thread_backends: dict[str, str] = {}
        self._observability = RuntimeObservability(component="runtime_manager")
        # Determine default sandbox mode from config
        try:
            from app.config import settings as _cfg
            _sandbox_mode = (_cfg.sandbox_mode or "local").strip().lower()
        except Exception as e:
            logger.debug("Suppressed error in runtime_backends: %s", e)
            _sandbox_mode = "local"
        _local = local_backend or LocalRuntimeBackend()
        _docker = docker_backend or DockerRuntimeBackend()
        _ssh = ssh_backend or SSHRuntimeBackend()
        self.register_backend(_local, default=(_sandbox_mode == "local"))
        self.register_backend(_docker, default=(_sandbox_mode == "docker"))
        self.register_backend(_ssh)
        self._load()

    def register_backend(self, backend: RuntimeBackend, *, default: bool = False):
        self._backends[backend.name] = backend
        if default or self.default_backend_name not in self._backends:
            self.default_backend_name = backend.name

    def _load(self):
        if not self.mapping_path.is_file():
            self._thread_backends = {}
            self._thread_routing_policies = {}
            return
        try:
            payload = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Suppressed error in runtime_backends: %s", e)
            self._thread_backends = {}
            self._thread_routing_policies = {}
            return
        if isinstance(payload, dict) and isinstance(payload.get("thread_backends"), dict):
            self._thread_backends = {
                str(thread_id): str(backend_name)
                for thread_id, backend_name in payload["thread_backends"].items()
                if str(thread_id).strip() and str(backend_name).strip()
            }
            raw_thread_routing = payload.get("thread_routing") if isinstance(payload.get("thread_routing"), dict) else {}
            self._thread_routing_policies = {
                str(thread_id): dict(policy)
                for thread_id, policy in raw_thread_routing.items()
                if str(thread_id).strip() and isinstance(policy, dict)
            }
            return
        if isinstance(payload, dict):
            self._thread_backends = {
                str(thread_id): str(backend_name)
                for thread_id, backend_name in payload.items()
                if str(thread_id).strip() and str(backend_name).strip()
            }
            self._thread_routing_policies = {}
            return
        self._thread_backends = {}
        self._thread_routing_policies = {}

    def _save(self):
        self.mapping_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "default_backend": self.default_backend_name,
            "thread_backends": self._thread_backends,
            "thread_routing": self._thread_routing_policies,
        }
        self.mapping_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def observability_snapshot(self) -> dict[str, Any]:
        return self._observability.snapshot()

    def routing_policy_defaults(self) -> dict[str, Any]:
        return self._normalized_routing_policy(None)

    def _normalize_backend_order(self, raw_order: Any, fallback_order: list[str]) -> list[str]:
        requested_order = list(raw_order) if isinstance(raw_order, (list, tuple)) else []
        normalized: list[str] = []
        for item in [*requested_order, *fallback_order, *sorted(self._backends.keys())]:
            name = str(item).strip()
            if not name or name not in self._backends or name in normalized:
                continue
            normalized.append(name)
        return normalized

    def _normalized_routing_policy(self, policy: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(policy or {})
        return {
            "mode": "auto",
            "name": str(payload.get("name") or "availability_first").strip() or "availability_first",
            "execute_order": self._normalize_backend_order(
                payload.get("execute_order") or payload.get("execution_order"),
                ["docker", "ssh", "local"],
            ),
            "workspace_order": self._normalize_backend_order(
                payload.get("workspace_order"),
                ["local", "docker", "ssh"],
            ),
        }

    def _routing_capability(self, operation: str | None, language: str | None = None) -> tuple[str, str | None]:
        if operation == "execute_python":
            return "execute", "python"
        if operation == "execute_javascript":
            return "execute", "javascript"
        if operation == "execute_bash":
            return "execute", "bash"
        if operation == "save_output":
            return "outputs", None
        if operation == "get_file_history":
            return "file_history", None
        if operation in {
            "create_shadow_workspace",
            "get_shadow_info",
            "list_shadow_changes",
            "get_shadow_diff",
            "accept_shadow_workspace",
            "discard_shadow_workspace",
            "shadow_workspace",
        }:
            return "shadow_workspace", None
        if language in {"python", "javascript", "bash"}:
            return "execute", language
        return "workspace", None

    def _backend_is_eligible_for_auto_route(
        self,
        backend: RuntimeBackend,
        *,
        capability_key: str,
        language: str | None,
    ) -> tuple[bool, str, dict[str, Any]]:
        backend_info = backend.describe()
        capabilities = backend_info.get("capabilities") or {}
        if capability_key == "execute":
            execute_capabilities = list(capabilities.get("execute") or [])
            if language and execute_capabilities and language not in execute_capabilities:
                return False, f"unsupported_language:{language}", backend_info
        elif not bool(capabilities.get(capability_key)):
            return False, f"missing_capability:{capability_key}", backend_info
        if not backend_info.get("available", True):
            return False, "unavailable", backend_info
        if backend.name == "ssh" and capability_key == "execute":
            remote_capabilities = ((backend_info.get("health") or {}).get("remote_capabilities") or {})
            if not remote_capabilities.get("tar"):
                return False, "missing_remote_tar", backend_info
            if language and language in remote_capabilities and not remote_capabilities.get(language):
                return False, f"missing_remote_{language}", backend_info
        return True, "eligible", backend_info

    def _resolve_backend_selection(
        self,
        thread_id: str | None = None,
        *,
        operation: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        normalized_thread_id = (thread_id or "").strip() or None
        if normalized_thread_id:
            assigned_backend = self._thread_backends.get(normalized_thread_id)
            if assigned_backend in self._backends:
                return {
                    "mode": "explicit",
                    "requested_backend": assigned_backend,
                    "selected_backend": assigned_backend,
                    "operation": operation,
                    "language": language,
                    "policy": None,
                    "candidates": [{"backend": assigned_backend, "eligible": True, "reason": "explicit_assignment"}],
                    "reason": "explicit_assignment",
                }
            thread_policy = self._thread_routing_policies.get(normalized_thread_id)
            if thread_policy:
                normalized_policy = self._normalized_routing_policy(thread_policy)
                capability_key, resolved_language = self._routing_capability(operation, language)
                candidate_order = (
                    normalized_policy["execute_order"]
                    if capability_key == "execute"
                    else normalized_policy["workspace_order"]
                )
                candidates: list[dict[str, Any]] = []
                for backend_name in candidate_order:
                    backend = self._backends.get(backend_name)
                    if backend is None:
                        continue
                    eligible, reason, backend_info = self._backend_is_eligible_for_auto_route(
                        backend,
                        capability_key=capability_key,
                        language=resolved_language,
                    )
                    candidates.append({
                        "backend": backend_name,
                        "eligible": eligible,
                        "reason": reason,
                        "available": backend_info.get("available", True),
                    })
                    if eligible:
                        return {
                            "mode": "auto",
                            "requested_backend": "auto",
                            "selected_backend": backend_name,
                            "operation": operation,
                            "language": resolved_language,
                            "policy": normalized_policy,
                            "candidates": candidates,
                            "reason": "policy_match",
                        }
                fallback_backend = self.default_backend_name if self.default_backend_name in self._backends else next(iter(self._backends))
                return {
                    "mode": "auto",
                    "requested_backend": "auto",
                    "selected_backend": fallback_backend,
                    "operation": operation,
                    "language": resolved_language,
                    "policy": normalized_policy,
                    "candidates": candidates,
                    "reason": "fallback_default",
                }
        return {
            "mode": "default",
            "requested_backend": None,
            "selected_backend": self.default_backend_name,
            "operation": operation,
            "language": language,
            "policy": None,
            "candidates": [{"backend": self.default_backend_name, "eligible": True, "reason": "default_backend"}],
            "reason": "default_backend",
        }

    def _routing_metadata(self, selection: dict[str, Any]) -> dict[str, Any]:
        metadata = {
            "routing_mode": selection.get("mode"),
            "route_reason": selection.get("reason"),
            "requested_backend": selection.get("requested_backend"),
        }
        policy = selection.get("policy")
        if isinstance(policy, dict):
            metadata["routing_policy"] = policy.get("name")
        return metadata

    def _operation_success(self, result: Any) -> bool:
        if isinstance(result, dict):
            if "success" in result:
                return bool(result.get("success"))
            return "error" not in result
        return True

    def _operation_error(self, result: Any) -> str:
        if isinstance(result, dict):
            return str(result.get("error") or "")
        return ""

    def _operation_metadata(self, result: Any, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        details = dict(metadata or {})
        if isinstance(result, dict):
            for key in ("status", "exit_code", "sync_strategy", "fallback_from", "backend", "requested_backend", "failover_count"):
                if key in result:
                    details[key] = result.get(key)
        return details

    def _candidate_backend_suggestions(self, selection: dict[str, Any], current_backend: str) -> list[str]:
        suggestions: list[str] = []
        for candidate in selection.get("candidates") or []:
            backend_name = str(candidate.get("backend") or "").strip()
            if not backend_name or backend_name == current_backend or backend_name in suggestions:
                continue
            if candidate.get("eligible") or candidate.get("available"):
                suggestions.append(backend_name)
        if suggestions:
            return suggestions[:2]
        for backend_name in sorted(self._backends):
            if backend_name == current_backend or backend_name in suggestions:
                continue
            try:
                if self._backends[backend_name].describe().get("available", True):
                    suggestions.append(backend_name)
            except Exception as e:
                logger.debug("Suppressed error in runtime_backends: %s", e)
                continue
        return suggestions[:2]

    def _build_failure_hint(
        self,
        *,
        backend: RuntimeBackend,
        selection: dict[str, Any],
        operation: str,
        error: str,
    ) -> str:
        backend_info = backend.describe()
        health = backend_info.get("health") or {}
        suggestions = self._candidate_backend_suggestions(selection, backend.name)
        hint = ""
        if backend.name == "docker":
            if not health.get("cli_available"):
                hint = "Docker CLI 不可用。请安装 Docker，或切换到 local/ssh 运行时。"
            elif not health.get("daemon_available"):
                hint = "Docker daemon 不可用。请启动 Docker Desktop/daemon，或切换到 local/ssh 运行时。"
            else:
                missing_roles = [role for role, present in (health.get("images_local") or {}).items() if not present]
                if missing_roles:
                    hint = f"Docker 镜像未就绪（缺少：{', '.join(missing_roles)}）。可先预热镜像，或切换到 local/ssh 运行时。"
        elif backend.name == "ssh":
            if not health.get("host_configured"):
                hint = "SSH 运行时未配置主机。请设置 SSH_RUNTIME_HOST，或切换到 local/docker 运行时。"
            elif not health.get("connection_available"):
                hint = "SSH 连接不可用。请检查 SSH host/user/key，或切换到 local/docker 运行时。"
            else:
                remote_capabilities = health.get("remote_capabilities") or {}
                missing_remote = [name for name in ("python", "javascript", "bash", "tar") if remote_capabilities.get(name) is False]
                if missing_remote:
                    hint = f"SSH 远端缺少能力：{', '.join(missing_remote)}。请补齐远端环境，或切换到 local/docker 运行时。"
        elif backend.name == "local":
            hint = "当前本地运行时执行失败。"
        if not hint:
            action = "执行" if operation.startswith("execute_") else "访问"
            hint = f"{backend.name} 运行时{action}失败。"
        if suggestions:
            readable = "/".join(suggestions)
            if "切换到" not in hint:
                hint = f"{hint} 可尝试切换到 {readable} 运行时后重试。"
        elif error:
            hint = f"{hint} 请根据错误信息排查后重试。"
        return hint.strip()

    def _annotate_operation_result(
        self,
        *,
        result: Any,
        backend: RuntimeBackend,
        selection: dict[str, Any],
        operation: str,
    ) -> Any:
        if not isinstance(result, dict):
            return result
        payload = dict(result)
        payload.setdefault("backend", backend.name)
        requested_backend = selection.get("requested_backend")
        if requested_backend is not None:
            payload.setdefault("requested_backend", requested_backend)
        if "success" not in payload:
            payload["success"] = "error" not in payload
        if not payload.get("success"):
            if operation in {"execute_python", "execute_javascript", "execute_bash"}:
                payload.setdefault("exit_code", -1)
            candidates = selection.get("candidates")
            if candidates:
                payload.setdefault("candidates", list(candidates))
            hint = self._build_failure_hint(
                backend=backend,
                selection=selection,
                operation=operation,
                error=str(payload.get("error") or ""),
            )
            if hint:
                payload.setdefault("hint", hint)
        return payload

    def _merged_operation_candidates(self, selection: dict[str, Any], extra_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in [*(selection.get("candidates") or []), *extra_candidates]:
            backend_name = str(candidate.get("backend") or "").strip()
            if not backend_name or backend_name in seen:
                continue
            merged.append(dict(candidate))
            seen.add(backend_name)
        return merged

    def _failover_candidates(
        self,
        *,
        selection: dict[str, Any],
        operation: str,
        current_backend: str,
    ) -> list[dict[str, Any]]:
        capability_key, resolved_language = self._routing_capability(operation, selection.get("language"))
        if capability_key != "execute":
            return []
        policy = selection.get("policy") if isinstance(selection.get("policy"), dict) else None
        if policy:
            candidate_order = self._normalize_backend_order(policy.get("execute_order"), self.routing_policy_defaults()["execute_order"])
        else:
            candidate_order = self._normalize_backend_order([current_backend], self.routing_policy_defaults()["execute_order"])
        candidates: list[dict[str, Any]] = []
        for backend_name in candidate_order:
            if backend_name == current_backend:
                continue
            backend = self._backends.get(backend_name)
            if backend is None:
                continue
            eligible, reason, backend_info = self._backend_is_eligible_for_auto_route(
                backend,
                capability_key=capability_key,
                language=resolved_language,
            )
            candidates.append({
                "backend": backend_name,
                "eligible": eligible,
                "reason": reason,
                "available": backend_info.get("available", True),
            })
        return candidates

    def _should_failover_backend_result(
        self,
        *,
        result: Any,
        backend: RuntimeBackend,
        operation: str,
    ) -> bool:
        if operation not in {"execute_python", "execute_javascript", "execute_bash"}:
            return False
        if not isinstance(result, dict) or result.get("success"):
            return False
        error = str(result.get("error") or "").strip().lower()
        if not error or "timed out" in error:
            return False
        try:
            backend_available = bool(backend.describe().get("available", True))
        except Exception as e:
            logger.debug("Suppressed error in runtime_backends: %s", e)
            backend_available = True
        if not backend_available:
            return True
        if result.get("exit_code") not in (-1, None):
            return False
        return any(
            marker in error
            for marker in (
                "docker cli not available",
                "docker daemon",
                "failed to probe docker daemon",
                "ssh runtime unavailable",
                "ssh runtime host not configured",
                "ssh connection unavailable",
                "failed to probe ssh connection",
                "permission denied (publickey",
                "command not available",
                "connection refused",
                "network is unreachable",
                "cannot connect",
                "unavailable",
            )
        )

    async def _run_single_backend_operation(
        self,
        *,
        selection: dict[str, Any],
        backend: RuntimeBackend,
        operation: str,
        thread_id: str | None,
        awaitable_factory: Callable[[RuntimeBackend], Any],
        metadata: dict[str, Any] | None = None,
    ):
        try:
            result = await self._observe_async_operation(
                backend_name=backend.name,
                operation=operation,
                thread_id=thread_id,
                awaitable=awaitable_factory(backend),
                metadata=metadata,
            )
        except Exception as exc:
            result = {"success": False, "error": str(exc), "exit_code": -1}
        return self._annotate_operation_result(
            result=result,
            backend=backend,
            selection=selection,
            operation=operation,
        )

    async def _run_backend_operation(
        self,
        *,
        selection: dict[str, Any],
        backend: RuntimeBackend,
        operation: str,
        thread_id: str | None,
        awaitable_factory: Callable[[RuntimeBackend], Any],
        metadata: dict[str, Any] | None = None,
    ):
        failover_candidates = self._failover_candidates(
            selection=selection,
            operation=operation,
            current_backend=backend.name,
        )
        effective_selection = dict(selection)
        if failover_candidates:
            effective_selection["candidates"] = self._merged_operation_candidates(selection, failover_candidates)
        result = await self._run_single_backend_operation(
            selection=effective_selection,
            backend=backend,
            operation=operation,
            thread_id=thread_id,
            awaitable_factory=awaitable_factory,
            metadata=metadata,
        )
        if not self._should_failover_backend_result(result=result, backend=backend, operation=operation):
            return result
        attempted_backends = [backend.name]
        origin_backend = backend.name
        for candidate in failover_candidates:
            if not candidate.get("eligible"):
                continue
            backend_name = str(candidate.get("backend") or "").strip()
            if not backend_name or backend_name in attempted_backends:
                continue
            next_backend = self._backends.get(backend_name)
            if next_backend is None:
                continue
            attempted_backends.append(backend_name)
            result = await self._run_single_backend_operation(
                selection=effective_selection,
                backend=next_backend,
                operation=operation,
                thread_id=thread_id,
                awaitable_factory=awaitable_factory,
                metadata=metadata,
            )
            if result.get("success"):
                result["fallback_from"] = origin_backend
                result["attempted_backends"] = list(attempted_backends)
                result["failover_count"] = len(attempted_backends) - 1
                return result
            if not self._should_failover_backend_result(result=result, backend=next_backend, operation=operation):
                result["fallback_from"] = origin_backend
                result["attempted_backends"] = list(attempted_backends)
                result["failover_count"] = len(attempted_backends) - 1
                return result
        if isinstance(result, dict) and len(attempted_backends) > 1:
            result.setdefault("fallback_from", origin_backend)
            result["attempted_backends"] = list(attempted_backends)
            result["failover_count"] = len(attempted_backends) - 1
        return result

    async def _observe_async_operation(
        self,
        *,
        backend_name: str,
        operation: str,
        thread_id: str | None,
        awaitable,
        metadata: dict[str, Any] | None = None,
    ):
        started = time.monotonic()
        try:
            result = await awaitable
        except Exception as exc:
            self._observability.record(
                backend=backend_name,
                operation=operation,
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                thread_id=thread_id,
                error=str(exc),
                metadata=metadata,
            )
            raise
        self._observability.record(
            backend=backend_name,
            operation=operation,
            success=self._operation_success(result),
            duration_ms=(time.monotonic() - started) * 1000,
            thread_id=thread_id,
            error=self._operation_error(result),
            metadata=self._operation_metadata(result, metadata),
        )
        return result

    def _backend_observability(self, backend: RuntimeBackend) -> dict[str, Any]:
        payload = {"manager": self._observability.snapshot_for_backend(backend.name)}
        backend_snapshot = getattr(backend, "observability_snapshot", None)
        if callable(backend_snapshot):
            payload["backend"] = backend_snapshot()
        return payload

    def list_backends(self) -> list[dict[str, Any]]:
        assigned_counts = {name: 0 for name in self._backends}
        for backend_name in self._thread_backends.values():
            if backend_name in assigned_counts:
                assigned_counts[backend_name] += 1
        results = []
        for name in sorted(self._backends):
            backend = self._backends[name]
            results.append({
                **backend.describe(),
                "default": name == self.default_backend_name,
                "assigned_threads": assigned_counts.get(name, 0),
                "observability": self._backend_observability(backend),
            })
        return results

    def get_backend_name(
        self,
        thread_id: str | None = None,
        *,
        operation: str | None = None,
        language: str | None = None,
    ) -> str:
        selection = self._resolve_backend_selection(thread_id, operation=operation, language=language)
        return str(selection.get("selected_backend") or self.default_backend_name)

    def get_backend(
        self,
        thread_id: str | None = None,
        *,
        operation: str | None = None,
        language: str | None = None,
    ):
        return self._backends[self.get_backend_name(thread_id, operation=operation, language=language)]

    def get_backend_by_name(self, backend_name: str) -> RuntimeBackend | None:
        return self._backends.get(backend_name.strip())

    def get_thread_runtime(self, thread_id: str) -> dict[str, Any]:
        assigned_name = self._thread_backends.get(thread_id)
        thread_policy = self._thread_routing_policies.get(thread_id)
        execute_python_route = self._resolve_backend_selection(thread_id, operation="execute_python", language="python")
        execute_javascript_route = self._resolve_backend_selection(thread_id, operation="execute_javascript", language="javascript")
        execute_bash_route = self._resolve_backend_selection(thread_id, operation="execute_bash", language="bash")
        workspace_route = self._resolve_backend_selection(thread_id, operation="workspace")
        outputs_route = self._resolve_backend_selection(thread_id, operation="save_output")
        effective_backend = self._backends[execute_bash_route["selected_backend"]]
        effective_backend_info = effective_backend.describe()
        workspace_backend = self._backends[workspace_route["selected_backend"]]
        outputs_backend = self._backends[outputs_route["selected_backend"]]
        routing_mode = "auto" if thread_policy else ("explicit" if assigned_name in self._backends else "default")
        return {
            "thread_id": thread_id,
            "backend": assigned_name if assigned_name in self._backends else ("auto" if thread_policy else effective_backend.name),
            "effective_backend": effective_backend.name,
            "workspace_backend": workspace_backend.name,
            "outputs_backend": outputs_backend.name,
            "kind": effective_backend_info.get("kind"),
            "available": effective_backend_info.get("available", True),
            "assigned": bool(assigned_name in self._backends or thread_policy),
            "requested_backend": assigned_name if assigned_name in self._backends else ("auto" if thread_policy else None),
            "default_backend": self.default_backend_name,
            "workspace_dir": workspace_backend.get_workspace_dir(thread_id),
            "outputs_dir": outputs_backend.get_outputs_dir(thread_id),
            "uploads_dir": workspace_backend.get_uploads_dir(thread_id),
            "routing": {
                "mode": routing_mode,
                "policy": self._normalized_routing_policy(thread_policy) if thread_policy else None,
                "selected": {
                    "execute_python": execute_python_route.get("selected_backend"),
                    "execute_javascript": execute_javascript_route.get("selected_backend"),
                    "execute_bash": execute_bash_route.get("selected_backend"),
                    "workspace": workspace_route.get("selected_backend"),
                    "outputs": outputs_route.get("selected_backend"),
                },
            },
            "observability": self._backend_observability(effective_backend),
        }

    def prewarm_backend(self, backend_name: str) -> dict[str, Any]:
        started = time.monotonic()
        normalized_backend_name = backend_name.strip()
        backend = self.get_backend_by_name(normalized_backend_name)
        if backend is None:
            result = {
                "success": False,
                "backend": normalized_backend_name,
                "error": f"Unknown runtime backend: {normalized_backend_name}",
                "available_backends": sorted(self._backends.keys()),
            }
            self._observability.record(
                backend=normalized_backend_name or self.default_backend_name,
                operation="prewarm_backend",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                error=result["error"],
                metadata={"reason": "unknown_backend"},
            )
            return result
        prewarm = getattr(backend, "prewarm_images", None)
        if not callable(prewarm):
            result = {
                "success": False,
                "backend": normalized_backend_name,
                "error": f"Runtime backend does not support prewarm: {normalized_backend_name}",
            }
            self._observability.record(
                backend=normalized_backend_name,
                operation="prewarm_backend",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                error=result["error"],
                metadata={"reason": "unsupported"},
            )
            return result
        try:
            result = prewarm(force_refresh=True)
        except Exception as exc:
            self._observability.record(
                backend=normalized_backend_name,
                operation="prewarm_backend",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                error=str(exc),
            )
            raise
        self._observability.record(
            backend=normalized_backend_name,
            operation="prewarm_backend",
            success=self._operation_success(result),
            duration_ms=(time.monotonic() - started) * 1000,
            error=self._operation_error(result),
            metadata=self._operation_metadata(result),
        )
        return result

    def set_thread_backend(self, thread_id: str, backend_name: str, *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.monotonic()
        normalized_thread_id = thread_id.strip()
        normalized_backend_name = backend_name.strip()
        if not normalized_thread_id:
            result = {"error": "thread_id is required"}
            self._observability.record(
                backend=normalized_backend_name or self.default_backend_name,
                operation="set_thread_backend",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                thread_id=thread_id,
                error=result["error"],
            )
            return result
        if normalized_backend_name == "auto":
            normalized_policy = self._normalized_routing_policy(policy)
            self._thread_backends.pop(normalized_thread_id, None)
            self._thread_routing_policies[normalized_thread_id] = normalized_policy
            self._save()
            result = self.get_thread_runtime(normalized_thread_id)
            result["status"] = "updated"
            self._observability.record(
                backend=str(result.get("effective_backend") or self.default_backend_name),
                operation="set_thread_backend",
                success=True,
                duration_ms=(time.monotonic() - started) * 1000,
                thread_id=normalized_thread_id,
                metadata=self._operation_metadata(
                    result,
                    {
                        "requested_backend": "auto",
                        "routing_mode": "auto",
                        "routing_policy": normalized_policy.get("name"),
                    },
                ),
            )
            return result
        if normalized_backend_name not in self._backends:
            result = {
                "error": f"Unknown runtime backend: {normalized_backend_name}",
                "available_backends": sorted(self._backends.keys()),
            }
            self._observability.record(
                backend=normalized_backend_name or self.default_backend_name,
                operation="set_thread_backend",
                success=False,
                duration_ms=(time.monotonic() - started) * 1000,
                thread_id=normalized_thread_id,
                error=result["error"],
                metadata={"requested_backend": normalized_backend_name},
            )
            return result
        self._thread_routing_policies.pop(normalized_thread_id, None)
        self._thread_backends[normalized_thread_id] = normalized_backend_name
        self._save()
        result = self.get_thread_runtime(normalized_thread_id)
        result["status"] = "updated"
        self._observability.record(
            backend=normalized_backend_name,
            operation="set_thread_backend",
            success=True,
            duration_ms=(time.monotonic() - started) * 1000,
            thread_id=normalized_thread_id,
            metadata=self._operation_metadata(result, {"requested_backend": normalized_backend_name}),
        )
        return result

    def clear_thread_backend(self, thread_id: str) -> dict[str, Any]:
        started = time.monotonic()
        normalized_thread_id = thread_id.strip()
        previous_selection = self._resolve_backend_selection(normalized_thread_id, operation="execute_bash", language="bash")
        previous_backend_name = str(previous_selection.get("selected_backend") or self.default_backend_name)
        self._thread_backends.pop(normalized_thread_id, None)
        self._thread_routing_policies.pop(normalized_thread_id, None)
        self._save()
        result = self.get_thread_runtime(normalized_thread_id)
        result["status"] = "cleared"
        self._observability.record(
            backend=previous_backend_name,
            operation="clear_thread_backend",
            success=True,
            duration_ms=(time.monotonic() - started) * 1000,
            thread_id=normalized_thread_id,
            metadata=self._operation_metadata(result, {"cleared_to": result.get("backend")}),
        )
        return result

    def _shadow_backend(self, shadow_thread_id: str):
        backend_name = self._shadow_thread_backends.get(shadow_thread_id)
        if backend_name in self._backends:
            return self._backends[backend_name]
        for backend in self._backends.values():
            try:
                if hasattr(backend, "is_shadow_thread") and backend.is_shadow_thread(shadow_thread_id):
                    self._shadow_thread_backends[shadow_thread_id] = backend.name
                    return backend
            except Exception as e:
                logger.debug("Suppressed error in runtime_backends: %s", e)
                continue
        return self.get_backend(None, operation="shadow_workspace")

    def get_thread_workspace(self, thread_id: str) -> str:
        return self.get_backend(thread_id, operation="workspace").get_thread_workspace(thread_id)

    def get_workspace_dir(self, thread_id: str) -> str:
        return self.get_backend(thread_id, operation="workspace").get_workspace_dir(thread_id)

    def get_outputs_dir(self, thread_id: str) -> str:
        return self.get_backend(thread_id, operation="save_output").get_outputs_dir(thread_id)

    def get_uploads_dir(self, thread_id: str) -> str:
        return self.get_backend(thread_id, operation="workspace").get_uploads_dir(thread_id)

    def resolve_workspace_path(self, thread_id: str, rel_path: str) -> str | None:
        return self.get_backend(thread_id, operation="workspace").resolve_workspace_path(thread_id, rel_path)

    def resolve_outputs_path(self, thread_id: str, rel_path: str) -> str | None:
        return self.get_backend(thread_id, operation="save_output").resolve_outputs_path(thread_id, rel_path)

    def get_file_history(self, thread_id: str, path: str | None = None, limit: int = 50) -> list[dict]:
        return self.get_backend(thread_id, operation="get_file_history").get_file_history(thread_id, path=path, limit=limit)

    async def execute_python(
        self,
        code: str,
        timeout: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> dict:
        selection = self._resolve_backend_selection(thread_id, operation="execute_python", language="python")
        backend = self._backends[str(selection.get("selected_backend") or self.default_backend_name)]
        return await self._run_backend_operation(
            selection=selection,
            backend=backend,
            operation="execute_python",
            thread_id=thread_id,
            awaitable_factory=lambda candidate_backend: candidate_backend.execute_python(code, timeout=timeout, thread_id=thread_id),
            metadata={"timeout": timeout, **self._routing_metadata(selection)},
        )

    async def execute_javascript(
        self,
        code: str,
        timeout: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> dict:
        selection = self._resolve_backend_selection(thread_id, operation="execute_javascript", language="javascript")
        backend = self._backends[str(selection.get("selected_backend") or self.default_backend_name)]
        return await self._run_backend_operation(
            selection=selection,
            backend=backend,
            operation="execute_javascript",
            thread_id=thread_id,
            awaitable_factory=lambda candidate_backend: candidate_backend.execute_javascript(code, timeout=timeout, thread_id=thread_id),
            metadata={"timeout": timeout, **self._routing_metadata(selection)},
        )

    async def execute_bash(
        self,
        command: str,
        timeout: Optional[int] = None,
        thread_id: Optional[str] = None,
    ) -> dict:
        selection = self._resolve_backend_selection(thread_id, operation="execute_bash", language="bash")
        backend = self._backends[str(selection.get("selected_backend") or self.default_backend_name)]
        return await self._run_backend_operation(
            selection=selection,
            backend=backend,
            operation="execute_bash",
            thread_id=thread_id,
            awaitable_factory=lambda candidate_backend: candidate_backend.execute_bash(command, timeout=timeout, thread_id=thread_id),
            metadata={"timeout": timeout, **self._routing_metadata(selection)},
        )

    async def write_file(self, path: str, content: str, thread_id: str) -> dict:
        selection = self._resolve_backend_selection(thread_id, operation="write_file")
        backend = self._backends[str(selection.get("selected_backend") or self.default_backend_name)]
        return await self._run_backend_operation(
            selection=selection,
            backend=backend,
            operation="write_file",
            thread_id=thread_id,
            awaitable_factory=lambda candidate_backend: candidate_backend.write_file(path, content, thread_id=thread_id),
            metadata={"path": path, "size": len(content), **self._routing_metadata(selection)},
        )

    async def read_file(self, path: str, thread_id: str) -> dict:
        selection = self._resolve_backend_selection(thread_id, operation="read_file")
        backend = self._backends[str(selection.get("selected_backend") or self.default_backend_name)]
        return await self._run_backend_operation(
            selection=selection,
            backend=backend,
            operation="read_file",
            thread_id=thread_id,
            awaitable_factory=lambda candidate_backend: candidate_backend.read_file(path, thread_id=thread_id),
            metadata={"path": path, **self._routing_metadata(selection)},
        )

    async def list_files(self, path: str = ".", thread_id: str = "") -> dict:
        selection = self._resolve_backend_selection(thread_id or None, operation="list_files")
        backend = self._backends[str(selection.get("selected_backend") or self.default_backend_name)]
        return await self._run_backend_operation(
            selection=selection,
            backend=backend,
            operation="list_files",
            thread_id=thread_id or None,
            awaitable_factory=lambda candidate_backend: candidate_backend.list_files(path, thread_id=thread_id),
            metadata={"path": path, **self._routing_metadata(selection)},
        )

    async def write_file_bytes(self, path: str, data: bytes, thread_id: str) -> dict:
        selection = self._resolve_backend_selection(thread_id, operation="write_file_bytes")
        backend = self._backends[str(selection.get("selected_backend") or self.default_backend_name)]
        return await self._run_backend_operation(
            selection=selection,
            backend=backend,
            operation="write_file_bytes",
            thread_id=thread_id,
            awaitable_factory=lambda candidate_backend: candidate_backend.write_file_bytes(path, data, thread_id=thread_id),
            metadata={"path": path, "size": len(data), **self._routing_metadata(selection)},
        )

    async def read_file_bytes(self, path: str, thread_id: str) -> dict:
        selection = self._resolve_backend_selection(thread_id, operation="read_file_bytes")
        backend = self._backends[str(selection.get("selected_backend") or self.default_backend_name)]
        return await self._run_backend_operation(
            selection=selection,
            backend=backend,
            operation="read_file_bytes",
            thread_id=thread_id,
            awaitable_factory=lambda candidate_backend: candidate_backend.read_file_bytes(path, thread_id=thread_id),
            metadata={"path": path, **self._routing_metadata(selection)},
        )

    async def save_output(self, filename: str, content: str, thread_id: str) -> dict:
        selection = self._resolve_backend_selection(thread_id, operation="save_output")
        backend = self._backends[str(selection.get("selected_backend") or self.default_backend_name)]
        return await self._run_backend_operation(
            selection=selection,
            backend=backend,
            operation="save_output",
            thread_id=thread_id,
            awaitable_factory=lambda candidate_backend: candidate_backend.save_output(filename, content, thread_id=thread_id),
            metadata={"filename": filename, "size": len(content), **self._routing_metadata(selection)},
        )

    def is_shadow_thread(self, thread_id: str) -> bool:
        backend = self._shadow_backend(thread_id)
        if not hasattr(backend, "is_shadow_thread"):
            return False
        return bool(backend.is_shadow_thread(thread_id))

    def create_shadow_workspace(self, base_thread_id: str, shadow_thread_id: str) -> dict:
        backend = self.get_backend(base_thread_id, operation="shadow_workspace")
        result = backend.create_shadow_workspace(base_thread_id, shadow_thread_id)
        self._shadow_thread_backends[shadow_thread_id] = backend.name
        return result

    def get_shadow_info(self, shadow_thread_id: str) -> dict | None:
        return self._shadow_backend(shadow_thread_id).get_shadow_info(shadow_thread_id)

    def list_shadow_changes(self, shadow_thread_id: str) -> dict:
        return self._shadow_backend(shadow_thread_id).list_shadow_changes(shadow_thread_id)

    def get_shadow_diff(
        self,
        shadow_thread_id: str,
        *,
        context_lines: int = 3,
        max_diff_chars: int = 20_000,
    ) -> dict:
        return self._shadow_backend(shadow_thread_id).get_shadow_diff(
            shadow_thread_id,
            context_lines=context_lines,
            max_diff_chars=max_diff_chars,
        )

    def accept_shadow_workspace(
        self,
        shadow_thread_id: str,
        paths: list[str] | None = None,
        hunks: list[dict] | None = None,
    ) -> dict:
        return self._shadow_backend(shadow_thread_id).accept_shadow_workspace(shadow_thread_id, paths=paths, hunks=hunks)

    def discard_shadow_workspace(self, shadow_thread_id: str) -> bool:
        backend = self._shadow_backend(shadow_thread_id)
        self._shadow_thread_backends.pop(shadow_thread_id, None)
        return backend.discard_shadow_workspace(shadow_thread_id)


runtime_manager = RuntimeManager()
