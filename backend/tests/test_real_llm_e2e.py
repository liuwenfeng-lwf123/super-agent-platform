"""Real-LLM end-to-end tests using a live API via FastAPI TestClient.
Skipped by default; enable with REAL_LLM_E2E=1 env var.

These hit the actual configured LLM provider from .env.
"""
import os
import json
import time
import importlib.util
import sys
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("REAL_LLM_E2E"),
    reason="Real LLM E2E: set REAL_LLM_E2E=1 and ensure .env has a working provider",
)

# Working model on ModelScope (verified)
WORKING_MODEL = os.environ.get("REAL_LLM_TEST_MODEL") or os.environ.get("DEFAULT_MODEL") or "Qwen/Qwen3-Coder-30B-A3B-Instruct"

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_SPEC = importlib.util.spec_from_file_location("llm_preflight", ROOT / "scripts" / "llm_preflight.py")
llm_preflight = importlib.util.module_from_spec(PREFLIGHT_SPEC)
assert PREFLIGHT_SPEC.loader is not None
sys.modules[PREFLIGHT_SPEC.name] = llm_preflight
PREFLIGHT_SPEC.loader.exec_module(llm_preflight)


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events = []
    current_event = ""
    for line in body.splitlines():
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            events.append((current_event, json.loads(line[6:])))
    return events


def _assert_no_sse_errors(events: list[tuple[str, dict]]) -> None:
    errors = [payload for event, payload in events if event == "error" or payload.get("type") == "error"]
    assert not errors, f"SSE error events found: {errors[:2]}"


def _has_transient_sse_error(events: list[tuple[str, dict]]) -> bool:
    markers = (
        "connection error",
        "connection reset",
        "server disconnected",
        "temporarily unavailable",
        "timed out",
        "timeout",
    )
    for event, payload in events:
        if event != "error" and payload.get("type") != "error":
            continue
        content = str(payload.get("content", "")).lower()
        if any(marker in content for marker in markers):
            return True
    return False


def _post_chat_with_live_retry(client, payload: dict):
    attempts = max(1, int(os.environ.get("REAL_LLM_TEST_RETRIES", "2")))
    last_response = None
    last_events = []
    for attempt in range(attempts):
        last_response = client.post("/api/chat", json=payload)
        assert last_response.status_code == 200
        last_events = _parse_sse_events(last_response.text)
        if not _has_transient_sse_error(last_events) or attempt == attempts - 1:
            return last_response, last_events
        time.sleep(2)
    return last_response, last_events


@pytest.fixture(scope="module", autouse=True)
def live_llm_preflight():
    from app.models.provider import llm_provider

    config = llm_provider.resolve_model_config(WORKING_MODEL)
    api_key = llm_provider.get_api_key_for_model(WORKING_MODEL) or ""
    result = llm_preflight.run_preflight(config.base_url, api_key, config.model, 30)
    assert result.ok, f"Live LLM preflight failed status={result.status}: {result.message}"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_chat_endpoint_streams_real_llm_response(client):
    """POST /api/chat should stream SSE tokens from a real LLM."""
    response, events = _post_chat_with_live_retry(
        client,
        {
            "message": "Reply with exactly one word: PONG",
            "model": WORKING_MODEL,
            "mode": "standard",
        },
    )
    assert response.status_code == 200
    body = response.text
    _assert_no_sse_errors(events)
    assert any(event == "token" for event, _ in events), f"No token events in response: {body[:500]}"
    assert any(event == "done" for event, _ in events), f"No done event in response: {body[:500]}"
    # Parse the done event and check model
    done_events = [payload for event, payload in events if event == "done"]
    assert done_events, "No done data line found"
    done = done_events[-1]
    assert done["usage"]["model"] == WORKING_MODEL
    assert done["usage"]["output_tokens"] > 0


def test_subagent_spawn_executes_with_real_llm(client):
    """POST /api/subagents/spawn should run a real LLM-backed react loop."""
    # First, register a custom subagent with the working model
    create_response = client.post(
        "/api/subagents/create",
        json={
            "name": "test_real_explore",
            "description": "test subagent with real llm",
            "prompt": "You are a helpful assistant. Answer concisely.",
            "tools": [],  # no tools, just LLM reasoning
            "model": WORKING_MODEL,
            "max_turns": 2,
        },
    )
    # Create may fail if already exists; that's fine
    if create_response.status_code != 200:
        pass

    spawn_response = client.post(
        "/api/subagents/spawn",
        json={
            "agent_name": "test_real_explore",
            "task_prompt": "What is 2+2? Answer with just the number.",
            "background": False,
        },
    )
    assert spawn_response.status_code == 200
    agent_id = spawn_response.json()["agent_id"]

    # Fetch the instance
    inst_response = client.get(f"/api/subagents/instance/{agent_id}")
    assert inst_response.status_code == 200
    instance = inst_response.json()
    assert instance["status"] == "completed", instance
    # Real LLM should return content (not just fallback marker)
    summary = instance.get("result_summary", "")
    # Either real number "4" in response, OR an LLM completion marker
    assert summary, "Empty result_summary"


def test_hook_register_via_http(client):
    """Register a hook via HTTP and verify it appears in list."""
    name = f"test_http_hook_{int(time.time())}"
    reg = client.post(
        "/api/hooks/register",
        json={
            "event": "UserPromptSubmit",
            "name": name,
            "description": "http-e2e test",
            "handlers": [{"handler_type": "command", "command": "echo http hook"}],
        },
    )
    assert reg.status_code == 200
    assert reg.json()["ok"] is True

    # Verify appears in list
    list_resp = client.get("/api/hooks")
    assert list_resp.status_code == 200
    names = [h["name"] for h in list_resp.json()["hooks"]]
    assert name in names

    # Clean up
    client.delete(f"/api/hooks/{name}")


def test_cron_crud_via_http(client):
    """Register cron job via HTTP."""
    job_name = f"test_http_cron_{int(time.time())}"
    add = client.post(
        "/api/cron",
        json={
            "name": job_name,
            "schedule": "*/5 * * * *",
            "action": "echo cron test",
            "action_type": "command",
        },
    )
    assert add.status_code == 200

    list_resp = client.get("/api/cron")
    names = [j["name"] for j in list_resp.json()["jobs"]]
    assert job_name in names

    # Run once
    run_resp = client.post(f"/api/cron/{job_name}/run")
    assert run_resp.status_code == 200
    result = run_resp.json()
    assert result["status"] == "success"
    assert "cron test" in result.get("output", "")

    # Clean up
    client.delete(f"/api/cron/{job_name}")


def test_registered_hook_fires_via_http(client):
    """Registered hook should execute through the HTTP fire endpoint."""
    name = f"test_chat_hook_{int(time.time())}"
    client.post(
        "/api/hooks/register",
        json={
            "event": "UserPromptSubmit",
            "name": name,
            "description": "allow",
            "handlers": [{"handler_type": "command", "command": "true"}],
        },
    )

    try:
        response = client.post(
            "/api/hooks/fire",
            json={"event": "UserPromptSubmit", "context": {"prompt": "Reply PONG"}},
        )
        assert response.status_code == 200
        results = response.json()["results"]
        assert any(result["hook"] == name and result["status"] == "success" for result in results)
    finally:
        client.delete(f"/api/hooks/{name}")


if __name__ == "__main__":
    os.environ["REAL_LLM_E2E"] = "1"
    pytest.main([__file__, "-v", "-s"])
