#!/usr/bin/env python3
import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


DEMO_PREFIX = "[Demo]"
DEMO_CATEGORY = "demo"

DEMO_MEMORY = [
    {
        "key": "demo_platform_positioning",
        "value": "This project is an Agent platform with chat, subagents, tools, runtime routing, memory, RAG, evaluation, and Hermes-style skills.",
        "category": DEMO_CATEGORY,
    },
    {
        "key": "demo_operator_goal",
        "value": "When presenting the platform, show chat streaming, tool validation, memory recall, knowledge search, and task evaluation before adding new features.",
        "category": DEMO_CATEGORY,
    },
]

DEMO_KNOWLEDGE = [
    {
        "name": f"{DEMO_PREFIX} Platform Tour",
        "content": """Super Agent Platform demo tour:\n1. Open the chat page and ask about the project architecture.\n2. Open Hermes to inspect skills, subagents, plugins, cron, and evolution.\n3. Open Memory to see seeded demo memories.\n4. Use the runtime and tool panels to inspect validation results.\n5. Run task evaluation to verify agent behavior with reproducible cases.""",
    },
    {
        "name": f"{DEMO_PREFIX} Production Checklist",
        "content": """Production readiness checklist:\n- Run scripts/quality_gate.py before merging changes.\n- Start services and run scripts/smoke_check.py before demos.\n- Enable REAL_LLM_E2E, RUN_DOCKER_LIVE, and RUN_SSH_LIVE only when real credentials and infrastructure are available.\n- Keep dangerous tools behind permission approval.\n- Use /health and /ready for deployment probes.""",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SeedResult:
    action: str
    ok: bool
    target: str
    message: str


def _base_url(value: str) -> str:
    value = value.strip()
    if not value.endswith("/"):
        value += "/"
    return value


def _url(base: str, path: str, params: dict[str, Any] | None = None) -> str:
    url = urllib.parse.urljoin(_base_url(base), path.lstrip("/"))
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def _read_json(response: Any) -> Any:
    return json.loads(response.read().decode("utf-8"))


def _request_json(
    method: str,
    base_url: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: Any | None = None,
    timeout: float = 5.0,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif method.upper() in {"POST", "PUT"}:
        data = b""
    request = urllib.request.Request(_url(base_url, path, params), data=data, headers=headers, method=method.upper())
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _read_json(response)


def build_demo_trajectory() -> dict[str, Any]:
    thread_id = "demo-source-" + uuid.uuid4().hex[:8]
    messages = [
        {
            "role": "user",
            "content": "请用人话介绍这个 Agent 平台现在能做什么。",
            "metadata": {"demo": True},
            "created_at": _now(),
        },
        {
            "role": "assistant",
            "content": "它不是单一聊天页，而是一套 Agent 平台：有主聊天、多 Agent、工具调用、权限安全、记忆、知识库、Runtime、评测、Hermes 技能系统和生产验收脚本。",
            "metadata": {"demo": True},
            "created_at": _now(),
        },
        {
            "role": "user",
            "content": "上线前我应该先看什么？",
            "metadata": {"demo": True},
            "created_at": _now(),
        },
        {
            "role": "assistant",
            "content": "先跑 scripts/quality_gate.py，再启动服务跑 scripts/smoke_check.py。真实生产前再打开 REAL_LLM_E2E、RUN_DOCKER_LIVE、RUN_SSH_LIVE 这些外部依赖测试。",
            "metadata": {"demo": True},
            "created_at": _now(),
        },
    ]
    return {
        "format": "sap.trajectory.v1",
        "exported_at": _now(),
        "message_count": len(messages),
        "thread": {
            "id": thread_id,
            "title": f"{DEMO_PREFIX} Agent Platform Walkthrough",
            "messages": messages,
            "metadata": {"demo_seed": True},
            "created_at": _now(),
            "updated_at": _now(),
            "parent_id": None,
            "compact_summary": "Demo walkthrough for platform capabilities and production readiness.",
        },
        "lineage": [],
        "children": [],
    }


def _result(action: str, ok: bool, target: str, message: str) -> SeedResult:
    return SeedResult(action=action, ok=ok, target=target, message=message)


def clean_demo_data(base_url: str, timeout: float) -> list[SeedResult]:
    results: list[SeedResult] = []
    try:
        memories = _request_json("GET", base_url, "/api/memory", timeout=timeout)
        for entry in memories if isinstance(memories, list) else []:
            if entry.get("category") == DEMO_CATEGORY or str(entry.get("key", "")).startswith("demo_"):
                entry_id = entry.get("id")
                _request_json("DELETE", base_url, f"/api/memory/{entry_id}", timeout=timeout)
                results.append(_result("delete_memory", True, str(entry_id), "deleted"))
    except Exception as exc:
        results.append(_result("delete_memory", False, "demo memory", str(exc)))

    try:
        docs = _request_json("GET", base_url, "/api/knowledge", timeout=timeout)
        for doc in docs if isinstance(docs, list) else []:
            if str(doc.get("name", "")).startswith(DEMO_PREFIX):
                doc_id = doc.get("doc_id")
                _request_json("DELETE", base_url, f"/api/knowledge/{doc_id}", timeout=timeout)
                results.append(_result("delete_knowledge", True, str(doc_id), "deleted"))
    except Exception as exc:
        results.append(_result("delete_knowledge", False, "demo knowledge", str(exc)))

    try:
        threads = _request_json("GET", base_url, "/api/threads", timeout=timeout)
        for thread in threads if isinstance(threads, list) else []:
            if str(thread.get("title", "")).startswith(DEMO_PREFIX):
                thread_id = thread.get("id")
                _request_json("DELETE", base_url, f"/api/threads/{thread_id}", timeout=timeout)
                results.append(_result("delete_thread", True, str(thread_id), "deleted"))
    except Exception as exc:
        results.append(_result("delete_thread", False, "demo thread", str(exc)))
    return results


def seed_demo_data(base_url: str, timeout: float, clean: bool) -> list[SeedResult]:
    try:
        payload = _request_json(
            "POST",
            base_url,
            "/api/demo/seed",
            params={"clean": str(clean).lower(), "dry_run": "false"},
            timeout=timeout,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            return [_result("seed_demo", False, "demo data", "invalid response")]
        return [
            _result(
                str(item.get("action", "seed_demo")),
                bool(item.get("ok")),
                str(item.get("target", "")),
                str(item.get("message", "")),
            )
            for item in payload["results"]
            if isinstance(item, dict)
        ]
    except Exception as exc:
        return [_result("seed_demo", False, "demo data", str(exc))]


def planned_actions(clean: bool) -> list[SeedResult]:
    results: list[SeedResult] = []
    if clean:
        results.extend([
            _result("delete_memory", True, "demo memory", "planned"),
            _result("delete_knowledge", True, "demo knowledge", "planned"),
            _result("delete_thread", True, "demo thread", "planned"),
        ])
    results.extend(_result("add_memory", True, entry["key"], "planned") for entry in DEMO_MEMORY)
    results.extend(_result("add_knowledge", True, doc["name"], "planned") for doc in DEMO_KNOWLEDGE)
    results.append(_result("add_thread", True, f"{DEMO_PREFIX} Agent Platform Walkthrough", "planned"))
    return results


def print_results(results: list[SeedResult]) -> None:
    width = max(len(item.action) for item in results) if results else 0
    for item in results:
        label = "PASS" if item.ok else "FAIL"
        print(f"[{label}] {item.action:<{width}} {item.target} - {item.message}")
    failed = [item for item in results if not item.ok]
    print(f"summary: {len(results) - len(failed)}/{len(results)} actions successful")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a demo walkthrough into a running Super Agent Platform backend.")
    parser.add_argument("--backend-url", default=os.getenv("BACKEND_URL", "http://localhost:8001"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("DEMO_SEED_TIMEOUT", "5")))
    parser.add_argument("--no-clean", action="store_true", help="Keep existing demo data instead of replacing it.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    if args.dry_run:
        results = planned_actions(clean=not args.no_clean)
    else:
        results = seed_demo_data(args.backend_url, args.timeout, clean=not args.no_clean)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if args.json:
        print(json.dumps({"elapsed_ms": elapsed_ms, "results": [item.__dict__ for item in results]}, ensure_ascii=False, indent=2))
    else:
        print_results(results)
        print(f"elapsed_ms: {elapsed_ms}")
    return 1 if any(not item.ok for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
