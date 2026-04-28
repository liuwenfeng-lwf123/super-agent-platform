from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from app.task_eval import (
    TASK_EVAL_RUNS_DIR,
    TaskEvalAgentConfig,
    TaskEvalRunner,
    evaluate_task_case_with_agent,
    list_task_eval_runs,
    load_task_cases,
    load_task_eval_run,
    render_task_eval_report,
    seed_dataset_overview,
)


def _split_names(values: list[str] | None) -> list[str]:
    if not values:
        return []
    names: list[str] = []
    for value in values:
        if not value:
            continue
        for item in value.split(","):
            name = item.strip()
            if name:
                names.append(name)
    return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Task eval runner and reporting CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("overview")

    runs_parser = subparsers.add_parser("runs")
    runs_parser.add_argument("--limit", type=int, default=20)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("run_id")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--dataset")
    run_parser.add_argument("--label", default="")
    run_parser.add_argument("--case-id", dest="case_ids", action="append", default=[])
    run_parser.add_argument("--mode", default="standard")
    run_parser.add_argument("--model")
    run_parser.add_argument("--skill", dest="skills", action="append", default=[])
    run_parser.add_argument("--allowed-tools", dest="allowed_tools", action="append", default=[])
    run_parser.add_argument("--disallowed-tools", dest="disallowed_tools", action="append", default=[])
    run_parser.add_argument("--enable-speculation", action="store_true")
    run_parser.add_argument("--disable-speculation", action="store_true")
    return parser


async def _run_eval(args: argparse.Namespace) -> dict[str, Any]:
    speculation: bool | None = None
    if args.enable_speculation:
        speculation = True
    elif args.disable_speculation:
        speculation = False
    agent_config = TaskEvalAgentConfig(
        mode=args.mode,
        model=args.model,
        skills=[value for value in args.skills if value],
        allowed_tools=_split_names(args.allowed_tools) or None,
        disallowed_tools=_split_names(args.disallowed_tools),
        enable_speculation=speculation,
    )
    runner = TaskEvalRunner(dataset_path=args.dataset)
    run = await runner.arun(
        lambda case: evaluate_task_case_with_agent(case, agent_config),
        case_ids=[value for value in args.case_ids if value],
        label=args.label,
        persist=True,
    )
    return {
        "run": run.to_dict(),
        "run_path": str(TASK_EVAL_RUNS_DIR / f"{run.run_id}.json"),
        "report_path": str(TASK_EVAL_RUNS_DIR / f"{run.run_id}.md"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "overview":
        payload = {
            **seed_dataset_overview(),
            "runs": list_task_eval_runs(limit=10),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "runs":
        payload = {"runs": list_task_eval_runs(limit=args.limit)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "report":
        run = load_task_eval_run(args.run_id)
        if run is None:
            print(f"Task eval run not found: {args.run_id}", file=sys.stderr)
            return 1
        print(render_task_eval_report(run, load_task_cases(run.dataset_path or None)))
        return 0

    if args.command == "run":
        payload = asyncio.run(_run_eval(args))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Unsupported command: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
