import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.task_eval import EvalTaskCase, EvalTaskResult, build_task_eval_run, save_task_cases, save_task_eval_run
from app.task_eval_cli import main


class TestTaskEvalCli(unittest.TestCase):
    def test_overview_command(self):
        with patch("app.task_eval_cli.seed_dataset_overview", return_value={"total_cases": 3, "categories": ["bug_fix"], "difficulties": ["easy"]}):
            with patch("app.task_eval_cli.list_task_eval_runs", return_value=[{"run_id": "run-1"}]):
                with patch("sys.stdout.write") as stdout_write:
                    exit_code = main(["overview"])

        output = "".join(call.args[0] for call in stdout_write.call_args_list)
        payload = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["total_cases"], 3)
        self.assertEqual(payload["runs"][0]["run_id"], "run-1")

    def test_runs_command(self):
        with patch("app.task_eval_cli.list_task_eval_runs", return_value=[{"run_id": "run-2"}]):
            with patch("sys.stdout.write") as stdout_write:
                exit_code = main(["runs", "--limit", "5"])

        output = "".join(call.args[0] for call in stdout_write.call_args_list)
        payload = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["runs"][0]["run_id"], "run-2")

    def test_report_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            seed_path = Path(temp_dir) / "seed_tasks.json"
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
            save_task_cases(cases, seed_path)
            run = build_task_eval_run(
                [EvalTaskResult(case_id="case-1", success=True)],
                cases,
                dataset_path=seed_path,
                run_id="run-3",
                created_at="2026-01-01T00:00:00+00:00",
            )
            run_path = Path(temp_dir) / "run-3.json"
            save_task_eval_run(run, run_path)

            with patch("app.task_eval_cli.load_task_eval_run", return_value=run):
                with patch("sys.stdout.write") as stdout_write:
                    exit_code = main(["report", "run-3"])

        output = "".join(call.args[0] for call in stdout_write.call_args_list)
        self.assertEqual(exit_code, 0)
        self.assertIn("[PASS] Read code", output)

    def test_run_command(self):
        fake_run = build_task_eval_run(
            [EvalTaskResult(case_id="case-1", success=True)],
            [
                EvalTaskCase(
                    case_id="case-1",
                    title="Read code",
                    category="read_code",
                    difficulty="easy",
                    prompt="Explain this function",
                    expected_outcome="Find and explain it",
                )
            ],
            run_id="run-4",
            created_at="2026-01-01T00:00:00+00:00",
        )
        fake_runner = type("FakeRunner", (), {"arun": AsyncMock(return_value=fake_run)})()

        with patch("app.task_eval_cli.TaskEvalRunner", return_value=fake_runner):
            with patch("sys.stdout.write") as stdout_write:
                exit_code = main(["run", "--label", "smoke"])

        output = "".join(call.args[0] for call in stdout_write.call_args_list)
        payload = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run"]["run_id"], "run-4")
        fake_runner.arun.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
