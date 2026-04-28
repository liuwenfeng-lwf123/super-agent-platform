#!/usr/bin/env python3
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urljoin


@dataclass
class CheckResult:
    name: str
    url: str
    required: bool
    ok: bool
    status: int | None
    message: str
    elapsed_ms: int


FRONTEND_PATHS = [
    ("frontend /", ""),
    ("frontend /dashboard", "dashboard"),
    ("frontend /evolution", "evolution"),
    ("frontend /hermes", "hermes"),
    ("frontend /history", "history"),
    ("frontend /memory", "memory"),
    ("frontend /settings", "settings"),
]


def _base_url(value: str) -> str:
    value = value.strip()
    if not value.endswith("/"):
        value += "/"
    return value


def _read_json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"))


def _request_json(url: str, timeout: float, method: str = "GET") -> tuple[int, Any, int]:
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return response.status, _read_json(body), elapsed_ms


def _request_text(url: str, timeout: float) -> tuple[int, str, int]:
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"Accept": "text/html,*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return response.status, body.decode("utf-8", errors="replace"), elapsed_ms


def _validate_dict(payload: Any, key: str | None = None) -> str:
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    if key and key not in payload:
        raise ValueError(f"missing key: {key}")
    return "ok"


def _validate_list(payload: Any) -> str:
    if not isinstance(payload, list):
        raise ValueError("expected JSON array")
    return f"{len(payload)} items"


def _validate_ready(payload: Any, strict: bool) -> str:
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    ready = bool(payload.get("ready"))
    checks = payload.get("checks", {})
    if strict and not ready:
        raise ValueError(f"not ready: {checks}")
    return "ready" if ready else f"not ready: {checks}"


def _run_json_check(
    name: str,
    url: str,
    timeout: float,
    required: bool,
    validator: Callable[[Any], str],
    method: str = "GET",
) -> CheckResult:
    started = time.perf_counter()
    try:
        status, payload, elapsed_ms = _request_json(url, timeout, method=method)
        if status < 200 or status >= 300:
            raise ValueError(f"HTTP {status}")
        message = validator(payload)
        return CheckResult(name, url, required, True, status, message, elapsed_ms)
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return CheckResult(name, url, required, False, exc.code, str(exc), elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return CheckResult(name, url, required, False, None, str(exc), elapsed_ms)


def _run_text_check(name: str, url: str, timeout: float, required: bool) -> CheckResult:
    started = time.perf_counter()
    try:
        status, body, elapsed_ms = _request_text(url, timeout)
        if status < 200 or status >= 300:
            raise ValueError(f"HTTP {status}")
        if not body.strip():
            raise ValueError("empty response")
        return CheckResult(name, url, required, True, status, "ok", elapsed_ms)
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return CheckResult(name, url, required, False, exc.code, str(exc), elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return CheckResult(name, url, required, False, None, str(exc), elapsed_ms)


def run_checks(
    backend_url: str,
    frontend_url: str,
    timeout: float,
    skip_frontend: bool,
    strict_ready: bool,
) -> list[CheckResult]:
    backend = _base_url(backend_url)
    results = [
        _run_json_check("backend /health", urljoin(backend, "health"), timeout, True, lambda payload: _validate_dict(payload, "status")),
        _run_json_check("backend /ready", urljoin(backend, "ready"), timeout, strict_ready, lambda payload: _validate_ready(payload, strict_ready)),
        _run_json_check("api /api/health", urljoin(backend, "api/health"), timeout, True, lambda payload: _validate_dict(payload, "status")),
        _run_json_check("api /api/providers", urljoin(backend, "api/providers"), timeout, True, lambda payload: _validate_dict(payload, "providers")),
        _run_json_check("api /api/models", urljoin(backend, "api/models"), timeout, True, _validate_list),
        _run_json_check("api /api/skills", urljoin(backend, "api/skills"), timeout, True, _validate_list),
        _run_json_check("api /api/runtimes", urljoin(backend, "api/runtimes"), timeout, True, lambda payload: _validate_dict(payload, "backends")),
        _run_json_check("api /api/statusline", urljoin(backend, "api/statusline"), timeout, True, lambda payload: _validate_dict(payload, "threads")),
        _run_json_check("api /api/security/policy", urljoin(backend, "api/security/policy"), timeout, True, lambda payload: _validate_dict(payload, "matrix")),
        _run_json_check("api /api/security/audit", urljoin(backend, "api/security/audit?limit=5"), timeout, True, lambda payload: _validate_dict(payload, "events")),
        _run_json_check("api /api/demo/seed dry-run", urljoin(backend, "api/demo/seed?dry_run=true"), timeout, True, lambda payload: _validate_dict(payload, "summary"), method="POST"),
        _run_json_check("api /api/hooks", urljoin(backend, "api/hooks"), timeout, True, lambda payload: _validate_dict(payload, "hooks")),
        _run_json_check("api /api/hooks/history", urljoin(backend, "api/hooks/history"), timeout, True, lambda payload: _validate_dict(payload, "history")),
        _run_json_check("api /api/subagents", urljoin(backend, "api/subagents"), timeout, True, lambda payload: _validate_dict(payload, "agents")),
        _run_json_check("api /api/subagents/instances", urljoin(backend, "api/subagents/instances"), timeout, True, lambda payload: _validate_dict(payload, "instances")),
        _run_json_check("api /api/subagents/teams", urljoin(backend, "api/subagents/teams"), timeout, True, lambda payload: _validate_dict(payload, "teams")),
        _run_json_check("api /api/plugins", urljoin(backend, "api/plugins"), timeout, True, lambda payload: _validate_dict(payload, "plugins")),
        _run_json_check("api /api/cron", urljoin(backend, "api/cron"), timeout, True, lambda payload: _validate_dict(payload, "jobs")),
        _run_json_check("api /api/elicitation/pending", urljoin(backend, "api/elicitation/pending"), timeout, True, lambda payload: _validate_dict(payload, "pending")),
        _run_json_check("api /api/soul", urljoin(backend, "api/soul"), timeout, True, lambda payload: _validate_dict(payload, "content")),
    ]
    if not skip_frontend:
        frontend = _base_url(frontend_url)
        for name, path in FRONTEND_PATHS:
            results.append(_run_text_check(name, urljoin(frontend, path), timeout, True))
    return results


def _print_results(results: list[CheckResult]) -> None:
    width = max(len(result.name) for result in results) if results else 0
    for result in results:
        if result.ok:
            label = "PASS"
        elif result.required:
            label = "FAIL"
        else:
            label = "WARN"
        status = result.status if result.status is not None else "-"
        print(f"[{label}] {result.name:<{width}} {status} {result.elapsed_ms}ms - {result.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run production smoke checks against Super Agent Platform.")
    parser.add_argument("--backend-url", default=os.getenv("BACKEND_URL", "http://localhost:8001"))
    parser.add_argument("--frontend-url", default=os.getenv("FRONTEND_URL", "http://localhost:3001"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("SMOKE_TIMEOUT", "5")))
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--strict-ready", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results = run_checks(
        backend_url=args.backend_url,
        frontend_url=args.frontend_url,
        timeout=args.timeout,
        skip_frontend=args.skip_frontend,
        strict_ready=args.strict_ready,
    )
    required = [result for result in results if result.required]
    failed = [result for result in required if not result.ok]
    if args.json:
        print(json.dumps([result.__dict__ for result in results], ensure_ascii=False, indent=2))
    else:
        _print_results(results)
        print(f"summary: {len(required) - len(failed)}/{len(required)} required checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
