import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("demo_seed", ROOT / "scripts" / "demo_seed.py")
demo_seed = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = demo_seed
SPEC.loader.exec_module(demo_seed)


class TestDemoSeed:
    def test_base_url_and_url_encoding(self):
        assert demo_seed._base_url("http://localhost:8001") == "http://localhost:8001/"
        url = demo_seed._url("http://localhost:8001", "/api/memory", {"key": "demo key", "value": "中文"})
        assert url.startswith("http://localhost:8001/api/memory?")
        assert "demo+key" in url
        assert "%E4%B8%AD%E6%96%87" in url

    def test_demo_trajectory_import_shape(self):
        trajectory = demo_seed.build_demo_trajectory()
        assert trajectory["format"] == "sap.trajectory.v1"
        assert trajectory["message_count"] == 4
        assert trajectory["thread"]["title"].startswith(demo_seed.DEMO_PREFIX)
        assert len(trajectory["thread"]["messages"]) == 4
        assert all(message["metadata"]["demo"] is True for message in trajectory["thread"]["messages"])

    def test_planned_actions_include_clean_and_seed_steps(self):
        actions = demo_seed.planned_actions(clean=True)
        names = [action.action for action in actions]
        assert names[:3] == ["delete_memory", "delete_knowledge", "delete_thread"]
        assert names.count("add_memory") == len(demo_seed.DEMO_MEMORY)
        assert names.count("add_knowledge") == len(demo_seed.DEMO_KNOWLEDGE)
        assert names[-1] == "add_thread"

    def test_dry_run_does_not_call_backend(self, monkeypatch):
        def fail_request(*_, **__):
            raise AssertionError("dry-run should not call backend")

        monkeypatch.setattr(demo_seed, "_request_json", fail_request)
        assert demo_seed.main(["--dry-run", "--json"]) == 0

    def test_seed_demo_data_calls_backend_demo_seed_api(self, monkeypatch):
        calls = []

        def fake_request(method, base_url, path, **kwargs):
            calls.append((method, path, kwargs.get("params")))
            return {
                "ok": True,
                "results": [
                    {"action": "add_memory", "ok": True, "target": "demo_platform_positioning", "message": "memory-id"},
                    {"action": "add_thread", "ok": True, "target": "thread-id", "message": "created"},
                ],
            }

        monkeypatch.setattr(demo_seed, "_request_json", fake_request)
        results = demo_seed.seed_demo_data("http://backend", timeout=1, clean=True)
        assert any(result.action == "add_memory" and result.ok for result in results)
        assert any(result.action == "add_thread" and result.ok for result in results)
        assert calls == [("POST", "/api/demo/seed", {"clean": "true", "dry_run": "false"})]

    def test_seed_demo_data_reports_invalid_backend_response(self, monkeypatch):
        monkeypatch.setattr(demo_seed, "_request_json", lambda *_, **__: {"bad": "shape"})
        results = demo_seed.seed_demo_data("http://backend", timeout=1, clean=True)
        assert len(results) == 1
        assert results[0].action == "seed_demo"
        assert results[0].ok is False
