import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("llm_preflight", ROOT / "scripts" / "llm_preflight.py")
llm_preflight = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = llm_preflight
SPEC.loader.exec_module(llm_preflight)


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps({"choices": [{"message": {"content": "PONG"}}]}).encode("utf-8")


def test_completion_url_handles_base_url_without_trailing_slash():
    assert llm_preflight._completion_url("https://example.com/v1") == "https://example.com/v1/chat/completions"


def test_redact_masks_openai_style_keys_and_authorization_header():
    redacted = llm_preflight._redact(
        "Authorization: Bearer sk-abcdefghijklmnop error for sk-1234567890abcdef"
    )
    assert "sk-abcdefghijklmnop" not in redacted
    assert "sk-1234567890abcdef" not in redacted
    assert "Authorization: Bearer [redacted]" in redacted


def test_run_preflight_success(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(llm_preflight.urllib.request, "urlopen", fake_urlopen)

    result = llm_preflight.run_preflight("https://example.com/v1", "sk-testsecret", "gpt-test", 3)

    assert result.ok is True
    assert result.status == 200
    assert captured == {
        "url": "https://example.com/v1/chat/completions",
        "auth": "Bearer sk-testsecret",
        "timeout": 3,
    }


def test_run_preflight_http_error_is_redacted(monkeypatch):
    def fake_urlopen(_request, timeout):
        raise urllib.error.HTTPError(
            "https://example.com/v1/chat/completions",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"error":{"message":"bad key sk-1234567890abcdef"}}'),
        )

    monkeypatch.setattr(llm_preflight.urllib.request, "urlopen", fake_urlopen)

    result = llm_preflight.run_preflight("https://example.com/v1", "sk-testsecret", "gpt-test", 3)

    assert result.ok is False
    assert result.status == 403
    assert "sk-1234567890abcdef" not in result.message
    assert "sk-[redacted]" in result.message


def test_main_reads_api_key_from_named_env_and_prints_json(monkeypatch, capsys):
    monkeypatch.setenv("CUSTOM_LLM_KEY", "sk-testsecret")
    monkeypatch.setattr(
        llm_preflight,
        "run_preflight",
        lambda base_url, api_key, model, timeout: llm_preflight.PreflightResult(True, 200, "ok: PONG", 7),
    )

    exit_code = llm_preflight.main([
        "--base-url",
        "https://example.com/v1",
        "--api-key-env",
        "CUSTOM_LLM_KEY",
        "--model",
        "gpt-test",
        "--json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["api_key_env"] == "CUSTOM_LLM_KEY"
    assert "sk-testsecret" not in json.dumps(payload)
