from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.tool_runtime import clear_runtime_context, evaluate_tool_permission, set_runtime_context
from app.api.chat import router
from app.security.policy import (
    RiskLevel,
    SecurityDecision,
    classify_tool,
    disabled_tools,
    evaluate_tool_security,
    permission_matrix,
)


class TestSecurityPolicy:
    def teardown_method(self):
        clear_runtime_context()

    def test_classifies_core_tool_risks(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("SECURITY_DISABLED_TOOLS", "")
        assert classify_tool("read_file", "file", is_read_only=True) == RiskLevel.SAFE
        assert classify_tool("write_file", "file", is_destructive=True) == RiskLevel.APPROVAL_REQUIRED
        assert classify_tool("execute_bash", "execution") == RiskLevel.DANGEROUS

    def test_configured_disabled_tool_applies_in_all_environments(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("SECURITY_DISABLED_TOOLS", "write_file,custom_tool")
        assert "write_file" in disabled_tools()
        result = evaluate_tool_security("write_file", {}, category="file", is_destructive=True)
        assert result is not None
        assert result.decision == SecurityDecision.DENY
        assert result.risk_level == RiskLevel.DISABLED

    def test_development_policy_is_observational_unless_tool_disabled(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("SECURITY_DISABLED_TOOLS", "")
        assert evaluate_tool_security("write_file", {}, category="file", is_destructive=True) is None

    def test_production_policy_allows_read_only_requires_approval_and_denies_dangerous(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SECURITY_DISABLED_TOOLS", "")
        read_result = evaluate_tool_security("read_file", {"path": "README.md"}, category="file", is_read_only=True)
        write_result = evaluate_tool_security("write_file", {"path": "README.md"}, category="file", is_destructive=True)
        shell_result = evaluate_tool_security("execute_bash", {"command": "echo hi"}, category="execution")
        assert read_result is not None
        assert read_result.decision == SecurityDecision.ALLOW
        assert write_result is not None
        assert write_result.decision == SecurityDecision.ASK
        assert shell_result is not None
        assert shell_result.decision == SecurityDecision.DENY

    def test_tool_runtime_enforces_production_policy_before_bypass_mode(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SECURITY_DISABLED_TOOLS", "")
        token = set_runtime_context(thread_id="t1", mode="bypassPermissions")
        try:
            result = evaluate_tool_permission("execute_bash", {"command": "echo hi"})
        finally:
            clear_runtime_context(token)
        assert result.decision == "deny"
        assert result.source == "security_policy"

    def test_tool_runtime_requires_approval_for_production_write(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SECURITY_DISABLED_TOOLS", "")
        token = set_runtime_context(thread_id="t1", mode="standard")
        try:
            result = evaluate_tool_permission("write_file", {"path": "README.md", "content": "x"})
        finally:
            clear_runtime_context(token)
        assert result.decision == "ask"
        assert result.source == "security_policy"

    def test_permission_matrix_contains_expected_groups(self):
        matrix = permission_matrix()
        assert "read_file" in matrix["safe"]
        assert "write_file" in matrix["approval_required"]
        assert "execute_bash" in matrix["dangerous"]
        assert "execute_bash" in matrix["disabled"]

    def test_security_policy_api(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        try:
            response = client.get("/api/security/policy")
        finally:
            client.close()
        assert response.status_code == 200
        payload = response.json()
        assert payload["production"] is True
        assert "execute_bash" in payload["disabled_tools"]
        assert "safe" in payload["matrix"]
