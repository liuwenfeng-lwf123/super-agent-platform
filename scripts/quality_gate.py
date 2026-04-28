#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


@dataclass
class GateStep:
    name: str
    command: list[str]
    cwd: Path
    required: bool = True
    env: dict[str, str] | None = None


@dataclass
class GateResult:
    name: str
    command: str
    cwd: str
    ok: bool
    returncode: int
    elapsed_ms: int


def _python() -> str:
    return sys.executable or "python3"


def _frontend_tsc_command() -> list[str]:
    local_tsc = FRONTEND / "node_modules" / ".bin" / "tsc"
    if local_tsc.exists():
        return [str(local_tsc), "--noEmit"]
    return ["npx", "--no-install", "tsc", "--noEmit"]


def _npm_command(script: str) -> list[str]:
    return ["npm", "run", script]


def build_steps(args: argparse.Namespace) -> list[GateStep]:
    steps: list[GateStep] = []
    if not args.frontend_only:
        steps.append(GateStep("backend compile", [_python(), "-m", "compileall", "-q", "app"], BACKEND))
        if args.full_backend:
            steps.append(GateStep("backend full pytest", [_python(), "-m", "pytest", "tests/", "-q", "--tb=short"], BACKEND))
        else:
            steps.append(
                GateStep(
                    "backend fast pytest",
                    [
                        _python(),
                        "-m",
                        "pytest",
                        "tests/test_demo_seed.py",
                        "tests/test_demo_seed_api.py",
                        "tests/test_quality_gate.py",
                        "tests/test_security_audit.py",
                        "tests/test_security_policy.py",
                        "tests/test_llm_preflight.py",
                        "tests/test_llm_provider_readiness.py",
                        "tests/test_smoke_check.py",
                        "tests/test_api_endpoints_live.py",
                        "tests/test_core_modules.py",
                        "-q",
                        "--tb=short",
                    ],
                    BACKEND,
                )
            )
    if not args.backend_only:
        steps.append(GateStep("frontend typecheck", _frontend_tsc_command(), FRONTEND))
        steps.append(GateStep("frontend validation regression", _npm_command("check:tool-validation"), FRONTEND))
        if not args.skip_build:
            steps.append(GateStep("frontend production build", _npm_command("build"), FRONTEND, env={"NEXT_DIST_DIR": ".next-quality-gate"}))
    return steps


def _command_display(command: list[str]) -> str:
    return " ".join(command)


def run_step(step: GateStep, stream: bool) -> GateResult:
    started = time.perf_counter()
    env = os.environ.copy()
    if step.env:
        env.update(step.env)
    if stream:
        completed = subprocess.run(step.command, cwd=step.cwd, env=env)
    else:
        completed = subprocess.run(step.command, cwd=step.cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if completed.stdout:
            print(completed.stdout.rstrip())
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return GateResult(
        name=step.name,
        command=_command_display(step.command),
        cwd=str(step.cwd),
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        elapsed_ms=elapsed_ms,
    )


def _ensure_paths() -> list[str]:
    missing = []
    for path in (BACKEND, FRONTEND):
        if not path.is_dir():
            missing.append(str(path))
    if shutil.which("npm") is None:
        missing.append("npm executable")
    return missing


def run_gate(args: argparse.Namespace) -> list[GateResult]:
    missing = _ensure_paths()
    if missing:
        raise RuntimeError("missing required paths/tools: " + ", ".join(missing))
    results = []
    for index, step in enumerate(build_steps(args), start=1):
        if not args.json:
            print(f"[{index}] {step.name}")
            print(f"    cwd: {step.cwd}")
            print(f"    cmd: {_command_display(step.command)}")
        result = run_step(step, stream=not args.quiet and not args.json)
        results.append(result)
        if not args.json:
            label = "PASS" if result.ok else "FAIL"
            print(f"    {label} {result.elapsed_ms}ms")
        if step.required and not result.ok and args.fail_fast:
            break
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local quality gate checks for Super Agent Platform.")
    parser.add_argument("--full-backend", action="store_true", help="Run the entire backend pytest suite instead of the fast smoke/core subset.")
    parser.add_argument("--skip-build", action="store_true", help="Skip Next.js production build.")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--frontend-only", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.backend_only and args.frontend_only:
        parser.error("--backend-only and --frontend-only cannot be used together")

    try:
        results = run_gate(args)
    except RuntimeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}")
        return 2

    failed = [result for result in results if not result.ok]
    if args.json:
        print(json.dumps([result.__dict__ for result in results], ensure_ascii=False, indent=2))
    else:
        print(f"summary: {len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
