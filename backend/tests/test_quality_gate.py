import argparse
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("quality_gate", ROOT / "scripts" / "quality_gate.py")
quality_gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = quality_gate
SPEC.loader.exec_module(quality_gate)


def _args(**overrides):
    defaults = {
        "frontend_only": False,
        "backend_only": False,
        "full_backend": False,
        "skip_build": False,
        "json": False,
        "quiet": True,
        "fail_fast": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestQualityGate:
    def test_fast_steps_include_backend_subset_and_frontend_build(self):
        steps = quality_gate.build_steps(_args())
        names = [step.name for step in steps]
        assert names == [
            "backend compile",
            "backend fast pytest",
            "frontend typecheck",
            "frontend validation regression",
            "frontend production build",
        ]
        assert "tests/test_core_modules.py" in steps[1].command
        assert steps[-1].env == {"NEXT_DIST_DIR": ".next-quality-gate"}

    def test_full_backend_uses_entire_test_suite(self):
        steps = quality_gate.build_steps(_args(full_backend=True, backend_only=True))
        names = [step.name for step in steps]
        assert names == ["backend compile", "backend full pytest"]
        assert "tests/" in steps[1].command

    def test_skip_build_removes_next_build_step(self):
        steps = quality_gate.build_steps(_args(skip_build=True, frontend_only=True))
        assert [step.name for step in steps] == ["frontend typecheck", "frontend validation regression"]

    def test_frontend_tsc_prefers_local_binary(self):
        command = quality_gate._frontend_tsc_command()
        assert command[-1] == "--noEmit"
        assert "tsc" in Path(command[0]).name

    def test_main_rejects_backend_only_and_frontend_only(self):
        try:
            quality_gate.main(["--backend-only", "--frontend-only"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected argparse SystemExit")

    def test_main_returns_failure_when_any_step_fails(self, monkeypatch):
        monkeypatch.setattr(
            quality_gate,
            "run_gate",
            lambda _: [
                quality_gate.GateResult("ok", "cmd", "cwd", True, 0, 1),
                quality_gate.GateResult("bad", "cmd", "cwd", False, 1, 1),
            ],
        )
        assert quality_gate.main(["--quiet"]) == 1

    def test_main_returns_success_when_all_steps_pass(self, monkeypatch):
        monkeypatch.setattr(
            quality_gate,
            "run_gate",
            lambda _: [quality_gate.GateResult("ok", "cmd", "cwd", True, 0, 1)],
        )
        assert quality_gate.main(["--quiet"]) == 0
