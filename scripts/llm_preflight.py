#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin


_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
_AUTH_PATTERN = re.compile(r"(Authorization:\s*Bearer\s+)[^\s]+", re.IGNORECASE)


@dataclass
class PreflightResult:
    ok: bool
    status: int | None
    message: str
    elapsed_ms: int


def _redact(value: str) -> str:
    value = _AUTH_PATTERN.sub(r"\1[redacted]", value)
    return _KEY_PATTERN.sub("sk-[redacted]", value)


def _base_url(value: str) -> str:
    value = value.strip()
    if not value.endswith("/"):
        value += "/"
    return value


def _completion_url(base_url: str) -> str:
    return urljoin(_base_url(base_url), "chat/completions")


def _extract_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    text = first.get("text")
    return text if isinstance(text, str) else ""


def run_preflight(base_url: str, api_key: str, model: str, timeout: float) -> PreflightResult:
    if not api_key or api_key == "sk-your-api-key-here":
        return PreflightResult(False, None, "missing or placeholder API key", 0)
    if not base_url:
        return PreflightResult(False, None, "missing base URL", 0)
    if not model:
        return PreflightResult(False, None, "missing model", 0)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly one word: PONG"}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _completion_url(base_url),
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            parsed = json.loads(raw_body)
            text = _extract_text(parsed).strip()
            if not text:
                return PreflightResult(False, response.status, "empty completion response", elapsed_ms)
            return PreflightResult(True, response.status, f"ok: {text[:80]}", elapsed_ms)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        message = _redact(f"HTTP {exc.code}: {body[:500]}")
        return PreflightResult(False, exc.code, message, elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return PreflightResult(False, None, _redact(str(exc)), elapsed_ms)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preflight an OpenAI-compatible chat completions endpoint.")
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--model", default=os.environ.get("REAL_LLM_TEST_MODEL") or os.environ.get("DEFAULT_MODEL", ""))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    api_key = os.environ.get(args.api_key_env, "")
    result = run_preflight(args.base_url, api_key, args.model, args.timeout)
    if args.json:
        print(json.dumps({
            "ok": result.ok,
            "status": result.status,
            "message": result.message,
            "elapsed_ms": result.elapsed_ms,
            "base_url": args.base_url,
            "model": args.model,
            "api_key_env": args.api_key_env,
        }, ensure_ascii=False))
    else:
        status = "PASS" if result.ok else "FAIL"
        print(f"{status} llm preflight status={result.status} elapsed_ms={result.elapsed_ms} model={args.model} message={result.message}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
