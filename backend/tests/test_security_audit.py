from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents import tool_runtime
from app.agents.tool_runtime import clear_runtime_context, evaluate_tool_permission, set_runtime_context
from app.api.chat import router
from app.security.audit import SecurityAuditLog, build_audit_event


class TestSecurityAudit:
    def teardown_method(self):
        clear_runtime_context()

    def test_audit_log_appends_and_filters_events(self, tmp_path: Path):
        log = SecurityAuditLog(tmp_path / "audit.jsonl")
        log.append(
            build_audit_event(
                thread_id="t1",
                agent_id="a1",
                mode="standard",
                tool="read_file",
                category="file",
                risk_level="safe",
                decision="allow",
                source="default",
                reason="ok",
                matched_rule=None,
                input_preview='{"path":"README.md"}',
            )
        )
        log.append(
            build_audit_event(
                thread_id="t2",
                agent_id="a2",
                mode="standard",
                tool="execute_bash",
                category="execution",
                risk_level="dangerous",
                decision="deny",
                source="security_policy",
                reason="blocked",
                matched_rule=None,
                input_preview='{"command":"rm -rf /"}',
            )
        )
        assert len(log.list_events()) == 2
        assert len(log.list_events(thread_id="t1")) == 1
        assert len(log.list_events(tool="execute_bash")) == 1
        assert len(log.list_events(decision="deny")) == 1

    def test_audit_log_trims_to_max_entries(self, tmp_path: Path):
        log = SecurityAuditLog(tmp_path / "audit.jsonl", max_entries=2)
        for index in range(3):
            log.append(
                build_audit_event(
                    thread_id=f"t{index}",
                    agent_id="a1",
                    mode="standard",
                    tool="read_file",
                    category="file",
                    risk_level="safe",
                    decision="allow",
                    source="default",
                    reason="ok",
                    matched_rule=None,
                    input_preview="{}",
                )
            )
        events = log.list_events(limit=10)
        assert len(events) == 2
        assert events[0]["thread_id"] == "t1"
        assert events[1]["thread_id"] == "t2"

    def test_tool_permission_writes_security_audit(self, tmp_path: Path, monkeypatch):
        audit_log = SecurityAuditLog(tmp_path / "audit.jsonl")
        monkeypatch.setattr("app.security.audit.security_audit_log", audit_log)
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SECURITY_DISABLED_TOOLS", "")
        token = set_runtime_context(thread_id="thread-1", agent_id="agent-1", mode="standard")
        try:
            result = evaluate_tool_permission("execute_bash", {"command": "echo hi"})
        finally:
            clear_runtime_context(token)
        assert result.decision == "deny"
        events = audit_log.list_events()
        assert len(events) == 1
        assert events[0]["thread_id"] == "thread-1"
        assert events[0]["agent_id"] == "agent-1"
        assert events[0]["tool"] == "execute_bash"
        assert events[0]["decision"] == "deny"
        assert events[0]["risk_level"] == "disabled"

    def test_security_audit_api_filters_events(self, tmp_path: Path, monkeypatch):
        audit_log = SecurityAuditLog(tmp_path / "audit.jsonl")
        audit_log.append(
            build_audit_event(
                thread_id="t1",
                agent_id="a1",
                mode="standard",
                tool="write_file",
                category="file",
                risk_level="approval_required",
                decision="ask",
                source="security_policy",
                reason="approval required",
                matched_rule=None,
                input_preview='{"path":"README.md"}',
            )
        )
        audit_log.append(
            build_audit_event(
                thread_id="t2",
                agent_id="a1",
                mode="standard",
                tool="read_file",
                category="file",
                risk_level="safe",
                decision="allow",
                source="security_policy",
                reason="allowed",
                matched_rule=None,
                input_preview='{"path":"README.md"}',
            )
        )
        monkeypatch.setattr("app.security.audit.security_audit_log", audit_log)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        try:
            response = client.get("/api/security/audit?decision=ask")
        finally:
            client.close()
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["events"]) == 1
        assert payload["events"][0]["tool"] == "write_file"
