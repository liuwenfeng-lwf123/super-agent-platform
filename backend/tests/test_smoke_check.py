import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("smoke_check", ROOT / "scripts" / "smoke_check.py")
smoke_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = smoke_check
SPEC.loader.exec_module(smoke_check)


class TestSmokeCheck:
    def test_base_url_adds_trailing_slash(self):
        assert smoke_check._base_url("http://localhost:8001") == "http://localhost:8001/"
        assert smoke_check._base_url("http://localhost:8001/") == "http://localhost:8001/"

    def test_ready_is_warning_by_default_when_not_ready(self):
        message = smoke_check._validate_ready({"ready": False, "checks": {"api_key": False}}, strict=False)
        assert "not ready" in message

    def test_ready_fails_in_strict_mode(self):
        try:
            smoke_check._validate_ready({"ready": False, "checks": {"api_key": False}}, strict=True)
        except ValueError as exc:
            assert "not ready" in str(exc)
        else:
            raise AssertionError("strict ready check should fail")

    def test_json_check_passes_http_method(self, monkeypatch):
        calls = []

        def fake_request(url, timeout, method="GET"):
            calls.append((url, timeout, method))
            return 200, {"summary": {}}, 3

        monkeypatch.setattr(smoke_check, "_request_json", fake_request)
        result = smoke_check._run_json_check(
            "demo dry-run",
            "http://backend/api/demo/seed?dry_run=true",
            5,
            True,
            lambda payload: smoke_check._validate_dict(payload, "summary"),
            method="POST",
        )
        assert result.ok is True
        assert calls == [("http://backend/api/demo/seed?dry_run=true", 5, "POST")]

    def test_run_checks_include_security_and_demo_dry_run(self, monkeypatch):
        names = []

        def fake_run_json_check(name, url, timeout, required, validator, method="GET"):
            names.append((name, method))
            return smoke_check.CheckResult(name, url, required, True, 200, "ok", 1)

        monkeypatch.setattr(smoke_check, "_run_json_check", fake_run_json_check)
        results = smoke_check.run_checks(
            backend_url="http://backend",
            frontend_url="http://frontend",
            timeout=5,
            skip_frontend=True,
            strict_ready=False,
        )
        assert all(result.ok for result in results)
        assert ("api /api/security/policy", "GET") in names
        assert ("api /api/security/audit", "GET") in names
        assert ("api /api/demo/seed dry-run", "POST") in names
        assert ("api /api/hooks", "GET") in names
        assert ("api /api/subagents", "GET") in names
        assert ("api /api/plugins", "GET") in names
        assert ("api /api/cron", "GET") in names
        assert ("api /api/elicitation/pending", "GET") in names
        assert ("api /api/soul", "GET") in names

    def test_run_checks_include_frontend_sections(self, monkeypatch):
        frontend_names = []

        def fake_run_json_check(name, url, timeout, required, validator, method="GET"):
            return smoke_check.CheckResult(name, url, required, True, 200, "ok", 1)

        def fake_run_text_check(name, url, timeout, required):
            frontend_names.append(name)
            return smoke_check.CheckResult(name, url, required, True, 200, "ok", 1)

        monkeypatch.setattr(smoke_check, "_run_json_check", fake_run_json_check)
        monkeypatch.setattr(smoke_check, "_run_text_check", fake_run_text_check)
        smoke_check.run_checks(
            backend_url="http://backend",
            frontend_url="http://frontend",
            timeout=5,
            skip_frontend=False,
            strict_ready=False,
        )
        assert frontend_names == [name for name, _ in smoke_check.FRONTEND_PATHS]

    def test_exit_code_fails_only_required_checks(self, monkeypatch, capsys):
        monkeypatch.setattr(
            smoke_check,
            "run_checks",
            lambda **_: [
                smoke_check.CheckResult("required", "http://x", True, False, None, "down", 1),
                smoke_check.CheckResult("optional", "http://x", False, False, None, "not ready", 1),
            ],
        )
        assert smoke_check.main(["--skip-frontend"]) == 1
        captured = capsys.readouterr()
        assert "FAIL" in captured.out
        assert "WARN" in captured.out

    def test_exit_code_ignores_optional_warning(self, monkeypatch):
        monkeypatch.setattr(
            smoke_check,
            "run_checks",
            lambda **_: [
                smoke_check.CheckResult("required", "http://x", True, True, 200, "ok", 1),
                smoke_check.CheckResult("optional", "http://x", False, False, None, "not ready", 1),
            ],
        )
        assert smoke_check.main(["--skip-frontend"]) == 0
