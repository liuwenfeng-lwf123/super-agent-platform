from __future__ import annotations
import logging

import inspect
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4


logger = logging.getLogger(__name__)
TASK_EVAL_DIR = Path(__file__).resolve().parents[1] / "data" / "task_eval"
SEED_TASKS_PATH = TASK_EVAL_DIR / "seed_tasks.json"
TASK_EVAL_RUNS_DIR = TASK_EVAL_DIR / "runs"


@dataclass(slots=True)
class EvalTaskCase:
    case_id: str
    title: str
    category: str
    difficulty: str
    prompt: str
    expected_outcome: str
    success_signals: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalTaskCase":
        return cls(
            case_id=str(payload.get("case_id") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            category=str(payload.get("category") or "general").strip() or "general",
            difficulty=str(payload.get("difficulty") or "medium").strip() or "medium",
            prompt=str(payload.get("prompt") or "").strip(),
            expected_outcome=str(payload.get("expected_outcome") or "").strip(),
            success_signals=[str(item).strip() for item in payload.get("success_signals") or [] if str(item).strip()],
            tags=[str(item).strip() for item in payload.get("tags") or [] if str(item).strip()],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskEvalAgentConfig:
    mode: str = "standard"
    model: str | None = None
    skills: list[str] = field(default_factory=list)
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=list)
    enable_speculation: bool | None = None
    thread_id_prefix: str = "task-eval"


@dataclass(slots=True)
class EvalTaskResult:
    case_id: str
    success: bool
    human_interventions: int = 0
    rework_count: int = 0
    turns: int | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalTaskResult":
        raw_turns = payload.get("turns")
        turns = int(raw_turns) if isinstance(raw_turns, int) else None
        return cls(
            case_id=str(payload.get("case_id") or "").strip(),
            success=bool(payload.get("success")),
            human_interventions=max(0, int(payload.get("human_interventions") or 0)),
            rework_count=max(0, int(payload.get("rework_count") or 0)),
            turns=turns,
            notes=str(payload.get("notes") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvalTaskRun:
    run_id: str
    created_at: str
    label: str = ""
    dataset_path: str = ""
    results: list[EvalTaskResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvalTaskRun":
        raw_results = payload.get("results") or []
        results = [
            item if isinstance(item, EvalTaskResult) else EvalTaskResult.from_dict(item)
            for item in raw_results
            if isinstance(item, (dict, EvalTaskResult))
        ]
        return cls(
            run_id=str(payload.get("run_id") or "").strip(),
            created_at=str(payload.get("created_at") or "").strip(),
            label=str(payload.get("label") or "").strip(),
            dataset_path=str(payload.get("dataset_path") or "").strip(),
            results=results,
            summary=dict(payload.get("summary") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_task_eval_dir() -> Path:
    TASK_EVAL_DIR.mkdir(parents=True, exist_ok=True)
    TASK_EVAL_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return TASK_EVAL_DIR


def load_task_cases(path: str | Path | None = None) -> list[EvalTaskCase]:
    target = Path(path) if path is not None else SEED_TASKS_PATH
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("Suppressed error in task_eval: %s", e)
        return []
    if not isinstance(payload, list):
        return []
    cases: list[EvalTaskCase] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        case = EvalTaskCase.from_dict(item)
        if case.case_id and case.prompt:
            cases.append(case)
    return cases


def save_task_cases(cases: list[EvalTaskCase], path: str | Path | None = None) -> Path:
    ensure_task_eval_dir()
    target = Path(path) if path is not None else SEED_TASKS_PATH
    serialized = [case.to_dict() for case in cases]
    target.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def build_task_eval_summary(
    results: list[EvalTaskResult],
    cases: list[EvalTaskCase] | None = None,
) -> dict[str, Any]:
    normalized_results = [result if isinstance(result, EvalTaskResult) else EvalTaskResult.from_dict(result) for result in results]
    case_map = {case.case_id: case for case in (cases or [])}
    total_cases = len(normalized_results)
    successes = sum(1 for result in normalized_results if result.success)
    total_interventions = sum(result.human_interventions for result in normalized_results)
    total_rework = sum(result.rework_count for result in normalized_results)
    total_turns = sum(result.turns for result in normalized_results if result.turns is not None)
    turns_count = sum(1 for result in normalized_results if result.turns is not None)
    autonomous_successes = sum(1 for result in normalized_results if result.success and result.human_interventions == 0)

    by_category: dict[str, dict[str, Any]] = {}
    for result in normalized_results:
        category = case_map.get(result.case_id).category if result.case_id in case_map else "unknown"
        bucket = by_category.setdefault(
            category,
            {
                "total_cases": 0,
                "successes": 0,
                "human_interventions": 0,
                "rework_count": 0,
            },
        )
        bucket["total_cases"] += 1
        bucket["successes"] += 1 if result.success else 0
        bucket["human_interventions"] += result.human_interventions
        bucket["rework_count"] += result.rework_count

    for bucket in by_category.values():
        bucket["success_rate"] = round(bucket["successes"] / max(1, bucket["total_cases"]), 3)

    return {
        "total_cases": total_cases,
        "successes": successes,
        "success_rate": round(successes / max(1, total_cases), 3),
        "autonomous_success_rate": round(autonomous_successes / max(1, total_cases), 3),
        "human_interventions": total_interventions,
        "avg_human_interventions": round(total_interventions / max(1, total_cases), 3),
        "rework_count": total_rework,
        "avg_rework_count": round(total_rework / max(1, total_cases), 3),
        "avg_turns": round(total_turns / max(1, turns_count), 3) if turns_count else 0.0,
        "by_category": dict(sorted(by_category.items())),
    }


def build_task_eval_run(
    results: list[EvalTaskResult],
    cases: list[EvalTaskCase] | None = None,
    *,
    label: str = "",
    dataset_path: str | Path | None = None,
    run_id: str | None = None,
    created_at: str | None = None,
) -> EvalTaskRun:
    normalized_results = [result if isinstance(result, EvalTaskResult) else EvalTaskResult.from_dict(result) for result in results]
    normalized_cases = [case if isinstance(case, EvalTaskCase) else EvalTaskCase.from_dict(case) for case in (cases or [])]
    return EvalTaskRun(
        run_id=(run_id or uuid4().hex[:12]),
        created_at=(created_at or datetime.now(timezone.utc).isoformat()),
        label=label.strip(),
        dataset_path=str(Path(dataset_path) if dataset_path is not None else SEED_TASKS_PATH),
        results=normalized_results,
        summary=build_task_eval_summary(normalized_results, normalized_cases),
    )


def save_task_eval_run(run: EvalTaskRun, path: str | Path | None = None) -> Path:
    ensure_task_eval_dir()
    target = Path(path) if path is not None else TASK_EVAL_RUNS_DIR / f"{run.run_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_task_eval_run(path_or_run_id: str | Path) -> EvalTaskRun | None:
    raw = str(path_or_run_id).strip()
    if not raw:
        return None
    target = Path(raw)
    if target.suffix != ".json":
        target = TASK_EVAL_RUNS_DIR / f"{raw}.json"
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("Suppressed error in task_eval: %s", e)
        return None
    if not isinstance(payload, dict):
        return None
    run = EvalTaskRun.from_dict(payload)
    return run if run.run_id else None


def list_task_eval_runs(limit: int = 20) -> list[dict[str, Any]]:
    ensure_task_eval_dir()
    entries: list[dict[str, Any]] = []
    for path in sorted(TASK_EVAL_RUNS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[: max(0, limit)]:
        run = load_task_eval_run(path)
        if run is None:
            continue
        entries.append(
            {
                "run_id": run.run_id,
                "created_at": run.created_at,
                "label": run.label,
                "dataset_path": run.dataset_path,
                "summary": dict(run.summary),
                "path": str(path),
                "report_path": str(path.with_suffix(".md")),
            }
        )
    return entries


def render_task_eval_report(run: EvalTaskRun, cases: list[EvalTaskCase] | None = None) -> str:
    case_map = {case.case_id: case for case in (cases or [])}
    summary = dict(run.summary)
    lines = [
        f"# Task Eval Run {run.run_id}",
        "",
        f"- Label: {run.label or run.run_id}",
        f"- Created At: {run.created_at}",
        f"- Dataset: {run.dataset_path or 'unknown'}",
        f"- Success Rate: {summary.get('success_rate', 0.0)}",
        f"- Autonomous Success Rate: {summary.get('autonomous_success_rate', 0.0)}",
        f"- Human Interventions: {summary.get('human_interventions', 0)}",
        f"- Rework Count: {summary.get('rework_count', 0)}",
        "",
        "## Results",
        "",
    ]
    for result in run.results:
        case = case_map.get(result.case_id)
        title = case.title if case is not None else result.case_id
        status = "PASS" if result.success else "FAIL"
        detail = result.notes or ""
        lines.append(f"- [{status}] {title} ({result.case_id})")
        if detail:
            lines.append(f"  - Notes: {detail}")
    if not run.results:
        lines.append("- No results")
    return "\n".join(lines)


def save_task_eval_report(run: EvalTaskRun, cases: list[EvalTaskCase] | None = None, path: str | Path | None = None) -> Path:
    ensure_task_eval_dir()
    target = Path(path) if path is not None else TASK_EVAL_RUNS_DIR / f"{run.run_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_task_eval_report(run, cases), encoding="utf-8")
    return target


class TaskEvalRunner:
    def __init__(self, cases: list[EvalTaskCase] | None = None, dataset_path: str | Path | None = None):
        self.dataset_path = str(Path(dataset_path) if dataset_path is not None else SEED_TASKS_PATH)
        self.cases = list(cases) if cases is not None else load_task_cases(self.dataset_path)

    def _selected_cases(self, case_ids: list[str] | None = None) -> list[EvalTaskCase]:
        if not case_ids:
            return list(self.cases)
        wanted = {str(case_id).strip() for case_id in case_ids if str(case_id).strip()}
        return [case for case in self.cases if case.case_id in wanted]

    async def arun(
        self,
        evaluator: Callable[[EvalTaskCase], EvalTaskResult | dict[str, Any] | bool | str | Awaitable[EvalTaskResult | dict[str, Any] | bool | str]],
        *,
        case_ids: list[str] | None = None,
        label: str = "",
        persist: bool = True,
    ) -> EvalTaskRun:
        selected_cases = self._selected_cases(case_ids)
        results: list[EvalTaskResult] = []
        for case in selected_cases:
            try:
                outcome = evaluator(case)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
            except Exception as exc:
                result = EvalTaskResult(case_id=case.case_id, success=False, notes=str(exc))
            else:
                if isinstance(outcome, EvalTaskResult):
                    result = outcome
                elif isinstance(outcome, dict):
                    payload = dict(outcome)
                    payload.setdefault("case_id", case.case_id)
                    result = EvalTaskResult.from_dict(payload)
                elif isinstance(outcome, bool):
                    result = EvalTaskResult(case_id=case.case_id, success=outcome)
                else:
                    result = EvalTaskResult(case_id=case.case_id, success=False, notes=str(outcome))
            if not result.case_id:
                result.case_id = case.case_id
            results.append(result)
        run = build_task_eval_run(results, selected_cases, label=label, dataset_path=self.dataset_path)
        if persist:
            save_task_eval_run(run)
            save_task_eval_report(run, selected_cases)
        return run


async def evaluate_task_case_with_agent(case: EvalTaskCase, config: TaskEvalAgentConfig | None = None) -> EvalTaskResult:
    from app.headless_cli import HeadlessCLIConfig, run_headless

    effective = config or TaskEvalAgentConfig()
    headless_config = HeadlessCLIConfig(
        message=case.prompt,
        thread_id=f"{effective.thread_id_prefix}-{case.case_id}-{uuid4().hex[:8]}",
        model=effective.model,
        mode=effective.mode,
        skills=list(effective.skills),
        allowed_tools=list(effective.allowed_tools) if effective.allowed_tools else None,
        disallowed_tools=list(effective.disallowed_tools),
        enable_speculation=effective.enable_speculation,
    )
    payload = await run_headless(headless_config)
    output = str(payload.get("output") or "").strip()
    error = str(payload.get("error") or "").strip()
    matched_signals = [signal for signal in case.success_signals if signal and signal.lower() in output.lower()]
    note_parts: list[str] = []
    if case.success_signals:
        note_parts.append(f"matched_signals={len(matched_signals)}/{len(case.success_signals)}")
    if error:
        note_parts.append(f"error={error[:240]}")
    if output:
        note_parts.append(f"output={output[:240]}")
    return EvalTaskResult(
        case_id=case.case_id,
        success=bool(output) and not error,
        notes=" | ".join(note_parts)[:500],
    )


def seed_dataset_overview() -> dict[str, Any]:
    cases = load_task_cases()
    categories = sorted({case.category for case in cases})
    difficulties = sorted({case.difficulty for case in cases})
    return {
        "total_cases": len(cases),
        "categories": categories,
        "difficulties": difficulties,
    }
