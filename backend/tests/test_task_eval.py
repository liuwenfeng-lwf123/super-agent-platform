import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router
from app.task_eval import (
    EvalTaskCase,
    EvalTaskResult,
    TaskEvalAgentConfig,
    TaskEvalRunner,
    build_task_eval_run,
    build_task_eval_summary,
    evaluate_task_case_with_agent,
    list_task_eval_runs,
    load_task_cases,
    load_task_eval_run,
    render_task_eval_report,
    save_task_cases,
    save_task_eval_run,
    seed_dataset_overview,
)


class TestTaskEvalModule(unittest.TestCase):
    def test_save_and_load_task_cases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cases.json"
            cases = [
                EvalTaskCase(
                    case_id="case-1",
                    title="Read code",
                    category="read_code",
                    difficulty="easy",
                    prompt="Explain this function",
                    expected_outcome="Find the function and explain it",
                    success_signals=["find definition"],
                    tags=["analysis"],
                )
            ]

            save_task_cases(cases, path)
            loaded = load_task_cases(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].case_id, "case-1")
        self.assertEqual(loaded[0].category, "read_code")

    def test_build_task_eval_summary(self):
        cases = [
            EvalTaskCase(
                case_id="case-1",
                title="Read code",
                category="read_code",
                difficulty="easy",
                prompt="Explain this function",
                expected_outcome="Find and explain it",
            ),
            EvalTaskCase(
                case_id="case-2",
                title="Fix bug",
                category="bug_fix",
                difficulty="medium",
                prompt="Fix the crash",
                expected_outcome="Root-cause fix with validation",
            ),
        ]
        results = [
            EvalTaskResult(case_id="case-1", success=True, human_interventions=0, rework_count=0, turns=4),
            EvalTaskResult(case_id="case-2", success=False, human_interventions=2, rework_count=1, turns=7),
        ]

        summary = build_task_eval_summary(results, cases)

        self.assertEqual(summary["total_cases"], 2)
        self.assertEqual(summary["successes"], 1)
        self.assertEqual(summary["success_rate"], 0.5)
        self.assertEqual(summary["autonomous_success_rate"], 0.5)
        self.assertEqual(summary["human_interventions"], 2)
        self.assertEqual(summary["rework_count"], 1)
        self.assertAlmostEqual(summary["avg_turns"], 5.5)
        self.assertEqual(summary["by_category"]["read_code"]["success_rate"], 1.0)
        self.assertEqual(summary["by_category"]["bug_fix"]["success_rate"], 0.0)

    def test_seed_dataset_overview(self):
        fake_cases = [
            EvalTaskCase(
                case_id="case-1",
                title="Read code",
                category="read_code",
                difficulty="easy",
                prompt="Explain this function",
                expected_outcome="Find and explain it",
            ),
            EvalTaskCase(
                case_id="case-2",
                title="Fix bug",
                category="bug_fix",
                difficulty="hard",
                prompt="Fix the crash",
                expected_outcome="Root-cause fix with validation",
            ),
        ]

        with patch("app.task_eval.load_task_cases", return_value=fake_cases):
            overview = seed_dataset_overview()

        self.assertEqual(overview["total_cases"], 2)
        self.assertEqual(overview["categories"], ["bug_fix", "read_code"])
        self.assertEqual(overview["difficulties"], ["easy", "hard"])

    def test_build_run_roundtrip_and_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_path = Path(temp_dir) / "run.json"
            cases = [
                EvalTaskCase(
                    case_id="case-1",
                    title="Read code",
                    category="read_code",
                    difficulty="easy",
                    prompt="Explain this function",
                    expected_outcome="Find and explain it",
                )
            ]
            results = [EvalTaskResult(case_id="case-1", success=True, notes="good")] 

            run = build_task_eval_run(results, cases, label="smoke", dataset_path="/tmp/cases.json", run_id="run-1", created_at="2026-01-01T00:00:00+00:00")
            from app.task_eval import save_task_eval_run

            save_task_eval_run(run, run_path)
            loaded = load_task_eval_run(run_path)
            report = render_task_eval_report(run, cases)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.run_id, "run-1")
        self.assertEqual(loaded.summary["success_rate"], 1.0)
        self.assertIn("[PASS] Read code", report)
        self.assertIn("Success Rate: 1.0", report)

    def test_task_eval_runner_runs_and_persists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "cases.json"
            runs_dir = Path(temp_dir) / "runs"
            cases = [
                EvalTaskCase(
                    case_id="case-1",
                    title="Read code",
                    category="read_code",
                    difficulty="easy",
                    prompt="Explain this function",
                    expected_outcome="Find and explain it",
                ),
                EvalTaskCase(
                    case_id="case-2",
                    title="Fix bug",
                    category="bug_fix",
                    difficulty="medium",
                    prompt="Fix the crash",
                    expected_outcome="Root-cause fix with validation",
                ),
            ]
            save_task_cases(cases, dataset_path)

            async def evaluator(case: EvalTaskCase):
                if case.case_id == "case-1":
                    return {"success": True, "turns": 3}
                return {"success": False, "human_interventions": 1, "rework_count": 1, "notes": "needs work"}

            with patch("app.task_eval.TASK_EVAL_RUNS_DIR", runs_dir):
                with patch("app.task_eval.TASK_EVAL_DIR", Path(temp_dir)):
                    runner = TaskEvalRunner(dataset_path=dataset_path)
                    run = asyncio.run(runner.arun(evaluator, label="seed-run", persist=True))
                    listed = list_task_eval_runs(limit=5)

        self.assertEqual(run.label, "seed-run")
        self.assertEqual(run.summary["total_cases"], 2)
        self.assertEqual(run.summary["success_rate"], 0.5)
        self.assertEqual(run.summary["human_interventions"], 1)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["run_id"], run.run_id)
        self.assertEqual(listed[0]["summary"]["success_rate"], 0.5)

    def test_evaluate_task_case_with_agent(self):
        case = EvalTaskCase(
            case_id="case-1",
            title="Read code",
            category="read_code",
            difficulty="easy",
            prompt="Explain this function",
            expected_outcome="Find and explain it",
            success_signals=["helper", "function"],
        )

        async def fake_run_headless(config):
            self.assertEqual(config.mode, "standard")
            self.assertTrue(config.thread_id.startswith("eval-case-1-"))
            return {"output": "This helper function parses the payload.", "error": None}

        with patch("app.headless_cli.run_headless", side_effect=fake_run_headless):
            result = asyncio.run(
                evaluate_task_case_with_agent(
                    case,
                    TaskEvalAgentConfig(thread_id_prefix="eval"),
                )
            )

        self.assertTrue(result.success)
        self.assertIn("matched_signals=2/2", result.notes)


class TestTaskEvalApi(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self._tempdir.name)
        self.seed_path = self.temp_root / "seed_tasks.json"
        self.runs_dir = self.temp_root / "runs"
        self.cases = [
            EvalTaskCase(
                case_id="case-1",
                title="Read code",
                category="read_code",
                difficulty="easy",
                prompt="Explain this function",
                expected_outcome="Find and explain it",
            )
        ]
        save_task_cases(self.cases, self.seed_path)
        self.run = build_task_eval_run(
            [EvalTaskResult(case_id="case-1", success=True, notes="good")],
            self.cases,
            label="api-smoke",
            dataset_path=self.seed_path,
            run_id="api-run-1",
            created_at="2026-01-01T00:00:00+00:00",
        )
        save_task_eval_run(self.run, self.runs_dir / "api-run-1.json")
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)
        self.seed_patch = patch("app.task_eval.SEED_TASKS_PATH", self.seed_path)
        self.dir_patch = patch("app.task_eval.TASK_EVAL_DIR", self.temp_root)
        self.runs_patch = patch("app.task_eval.TASK_EVAL_RUNS_DIR", self.runs_dir)
        self.seed_patch.start()
        self.dir_patch.start()
        self.runs_patch.start()

    def tearDown(self):
        self.seed_patch.stop()
        self.dir_patch.stop()
        self.runs_patch.stop()
        try:
            self.client.close()
        except Exception:
            pass
        self._tempdir.cleanup()

    def test_task_eval_api_endpoints(self):
        overview = self.client.get("/api/task-evals/overview")
        self.assertEqual(overview.status_code, 200)
        overview_payload = overview.json()
        self.assertEqual(overview_payload["total_cases"], 1)
        self.assertEqual(len(overview_payload["runs"]), 1)

        runs = self.client.get("/api/task-evals/runs")
        self.assertEqual(runs.status_code, 200)
        runs_payload = runs.json()
        self.assertEqual(runs_payload["runs"][0]["run_id"], "api-run-1")

        detail = self.client.get("/api/task-evals/runs/api-run-1")
        self.assertEqual(detail.status_code, 200)
        detail_payload = detail.json()
        self.assertEqual(detail_payload["run_id"], "api-run-1")
        self.assertEqual(detail_payload["summary"]["success_rate"], 1.0)

        report = self.client.get("/api/task-evals/runs/api-run-1/report")
        self.assertEqual(report.status_code, 200)
        report_payload = report.json()
        self.assertEqual(report_payload["run_id"], "api-run-1")
        self.assertIn("[PASS] Read code", report_payload["report"])

    def test_task_eval_run_creation_endpoint(self):
        created_run = build_task_eval_run(
            [EvalTaskResult(case_id="case-1", success=True, notes="created")],
            self.cases,
            label="created-run",
            dataset_path=self.seed_path,
            run_id="api-run-created",
            created_at="2026-01-02T00:00:00+00:00",
        )

        class FakeRunner:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.arun = AsyncMock(return_value=created_run)

            def _selected_cases(self, case_ids=None):
                return list(self.kwargs.get("cases") or self_cases)

        self_cases = self.cases
        fake_runner = FakeRunner(dataset_path=self.seed_path)

        with patch("app.api.chat.TaskEvalRunner", return_value=fake_runner):
            with patch("app.api.chat.render_task_eval_report", return_value="# report"):
                response = self.client.post(
                    "/api/task-evals/runs",
                    json={
                        "dataset": str(self.seed_path),
                        "label": "created-run",
                        "case_ids": ["case-1"],
                        "mode": "standard",
                        "skills": ["debug"],
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["run"]["run_id"], "api-run-created")
        self.assertEqual(payload["report"], "# report")
        fake_runner.arun.assert_awaited_once()
        self.assertEqual(fake_runner.arun.await_args.kwargs["label"], "created-run")
        self.assertEqual(fake_runner.arun.await_args.kwargs["case_ids"], ["case-1"])


if __name__ == "__main__":
    unittest.main()
