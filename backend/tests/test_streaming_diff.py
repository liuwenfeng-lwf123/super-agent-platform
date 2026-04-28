"""Tests for streaming diff infrastructure: track_file_before, compute_file_diff, emit/consume."""
import os
import tempfile
import unittest

from app.sandbox.manager import SandboxExecutor
from app.agents.tool_runtime import emit_file_diff, consume_file_diffs


class TestFileDiffBuffer(unittest.TestCase):
    def test_emit_and_consume(self):
        consume_file_diffs()  # drain any leftovers
        payload = {"path": "test.py", "status": "modified", "diff": "+foo"}
        emit_file_diff(payload)
        emit_file_diff({"path": "bar.py", "status": "added", "diff": "+bar"})
        diffs = consume_file_diffs()
        self.assertEqual(len(diffs), 2)
        self.assertEqual(diffs[0]["path"], "test.py")
        self.assertEqual(diffs[1]["path"], "bar.py")
        # Second consume should be empty
        self.assertEqual(consume_file_diffs(), [])

    def test_consume_empty(self):
        consume_file_diffs()  # drain
        self.assertEqual(consume_file_diffs(), [])


class TestSandboxStreamingDiff(unittest.TestCase):
    def setUp(self):
        self.executor = SandboxExecutor()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_track_and_compute_new_file(self):
        rel_path = "hello.py"
        full_path = os.path.join(self.tmpdir, rel_path)
        # Track before (file doesn't exist)
        token = self.executor.track_file_before(self.tmpdir, rel_path)
        self.assertIsNotNone(token)
        # Write file
        with open(full_path, "w") as f:
            f.write("print('hello')\n")
        # Compute diff
        diff = self.executor.compute_file_diff(token)
        self.assertIsNotNone(diff)
        self.assertEqual(diff["path"], rel_path)
        self.assertEqual(diff["status"], "added")
        self.assertFalse(diff["binary"])
        self.assertIn("+print('hello')", diff["diff"])
        self.assertEqual(diff["additions"], 1)
        self.assertEqual(diff["deletions"], 0)

    def test_track_and_compute_modified_file(self):
        rel_path = "app.py"
        full_path = os.path.join(self.tmpdir, rel_path)
        # Create initial file
        with open(full_path, "w") as f:
            f.write("x = 1\ny = 2\n")
        # Track before
        token = self.executor.track_file_before(self.tmpdir, rel_path)
        # Modify
        with open(full_path, "w") as f:
            f.write("x = 1\ny = 3\nz = 4\n")
        # Compute diff
        diff = self.executor.compute_file_diff(token)
        self.assertIsNotNone(diff)
        self.assertEqual(diff["status"], "modified")
        self.assertGreater(diff["additions"], 0)
        self.assertGreater(diff["deletions"], 0)

    def test_no_change_returns_none(self):
        rel_path = "same.py"
        full_path = os.path.join(self.tmpdir, rel_path)
        with open(full_path, "w") as f:
            f.write("x = 1\n")
        token = self.executor.track_file_before(self.tmpdir, rel_path)
        # Don't modify
        diff = self.executor.compute_file_diff(token)
        self.assertIsNone(diff)

    def test_none_token_returns_none(self):
        diff = self.executor.compute_file_diff(None)
        self.assertIsNone(diff)

    def test_track_nonexistent_path(self):
        # Outside workspace — should return None
        token = self.executor.track_file_before(self.tmpdir, "../../etc/passwd")
        self.assertIsNone(token)


class TestDirectMode(unittest.TestCase):
    """Basic import and syntax check for direct mode module."""

    def test_import(self):
        from app.local.direct import run_direct, main
        self.assertTrue(callable(run_direct))
        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
