import os
import tempfile
import unittest
from pathlib import Path

from app.sandbox.manager import SandboxExecutor


class ShadowWorkspaceReplayTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        self.executor = SandboxExecutor()
        self.base_thread_id = "thread-main"
        self.shadow_thread_id = "thread-shadow"

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()

    def _workspace_path(self, thread_id: str, rel_path: str) -> Path:
        return Path(self.executor.get_workspace_dir(thread_id)) / rel_path

    def _write_workspace_file(self, thread_id: str, rel_path: str, content: str):
        target = self._workspace_path(thread_id, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _write_workspace_bytes(self, thread_id: str, rel_path: str, content: bytes):
        target = self._workspace_path(thread_id, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def _read_workspace_file(self, thread_id: str, rel_path: str) -> str:
        return self._workspace_path(thread_id, rel_path).read_text(encoding="utf-8")

    def test_accept_shadow_workspace_applies_all_changes(self):
        self._write_workspace_file(self.base_thread_id, "app.py", "print('base')\n")
        self.executor.create_shadow_workspace(self.base_thread_id, self.shadow_thread_id)
        self._write_workspace_file(self.shadow_thread_id, "app.py", "print('shadow')\n")

        result = self.executor.accept_shadow_workspace(self.shadow_thread_id)

        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["accepted_all"])
        self.assertEqual(self._read_workspace_file(self.base_thread_id, "app.py"), "print('shadow')\n")
        self.assertEqual(result["remaining_changes"], [])

    def test_accept_shadow_workspace_can_apply_selected_files_only(self):
        self._write_workspace_file(self.base_thread_id, "a.txt", "base-a\n")
        self._write_workspace_file(self.base_thread_id, "b.txt", "base-b\n")
        self.executor.create_shadow_workspace(self.base_thread_id, self.shadow_thread_id)
        self._write_workspace_file(self.shadow_thread_id, "a.txt", "shadow-a\n")
        self._write_workspace_file(self.shadow_thread_id, "b.txt", "shadow-b\n")

        result = self.executor.accept_shadow_workspace(self.shadow_thread_id, paths=["a.txt"])

        self.assertEqual(result["status"], "accepted")
        self.assertFalse(result["accepted_all"])
        self.assertEqual(self._read_workspace_file(self.base_thread_id, "a.txt"), "shadow-a\n")
        self.assertEqual(self._read_workspace_file(self.base_thread_id, "b.txt"), "base-b\n")
        self.assertEqual([change["path"] for change in result["remaining_changes"]], ["b.txt"])

        follow_up = self.executor.accept_shadow_workspace(self.shadow_thread_id, paths=["b.txt"])

        self.assertTrue(follow_up["accepted_all"])
        self.assertEqual(self._read_workspace_file(self.base_thread_id, "b.txt"), "shadow-b\n")

    def test_accept_shadow_workspace_can_apply_selected_hunks_only(self):
        before = "alpha\nctx1\nctx2\nctx3\nctx4\nctx5\nctx6\nomega\n"
        after = "alpha changed\nctx1\nctx2\nctx3\nctx4\nctx5\nctx6\nomega\nnew tail\n"
        self._write_workspace_file(self.base_thread_id, "notes.txt", before)
        self.executor.create_shadow_workspace(self.base_thread_id, self.shadow_thread_id)
        self._write_workspace_file(self.shadow_thread_id, "notes.txt", after)

        shadow_diff = self.executor.get_shadow_diff(self.shadow_thread_id)
        entry = shadow_diff["diffs"][0]
        hunk_ids = [hunk["id"] for hunk in entry["hunks"]]

        self.assertEqual(len(hunk_ids), 2)

        result = self.executor.accept_shadow_workspace(
            self.shadow_thread_id,
            hunks=[{"path": "notes.txt", "ids": [hunk_ids[0]]}],
        )

        self.assertEqual(result["status"], "accepted")
        self.assertFalse(result["accepted_all"])
        self.assertEqual(
            self._read_workspace_file(self.base_thread_id, "notes.txt"),
            "alpha changed\nctx1\nctx2\nctx3\nctx4\nctx5\nctx6\nomega\n",
        )
        self.assertEqual(len(result["remaining_changes"]), 1)

        remaining_diff = self.executor.get_shadow_diff(self.shadow_thread_id)
        remaining_entry = next(item for item in remaining_diff["diffs"] if item["path"] == "notes.txt")
        remaining_ids = [hunk["id"] for hunk in remaining_entry["hunks"]]

        self.assertEqual(len(remaining_ids), 1)

        follow_up = self.executor.accept_shadow_workspace(
            self.shadow_thread_id,
            hunks=[{"path": "notes.txt", "ids": remaining_ids}],
        )

        self.assertTrue(follow_up["accepted_all"])
        self.assertEqual(self._read_workspace_file(self.base_thread_id, "notes.txt"), after)

    def test_get_shadow_diff_marks_binary_files_and_rejects_hunk_accept(self):
        self._write_workspace_bytes(self.base_thread_id, "blob.bin", b"\x00\x01base")
        self.executor.create_shadow_workspace(self.base_thread_id, self.shadow_thread_id)
        self._write_workspace_bytes(self.shadow_thread_id, "blob.bin", b"\x00\x01shadow")

        shadow_diff = self.executor.get_shadow_diff(self.shadow_thread_id)

        self.assertEqual(len(shadow_diff["diffs"]), 1)
        entry = shadow_diff["diffs"][0]
        self.assertTrue(entry["binary"])
        self.assertEqual(entry["hunks"], [])

        result = self.executor.accept_shadow_workspace(
            self.shadow_thread_id,
            hunks=[{"path": "blob.bin", "ids": ["hunk-0"]}],
        )

        self.assertIn("error", result)
        self.assertEqual(result["error"], "Binary files do not support hunk acceptance")

    def test_accept_shadow_workspace_can_delete_file_via_hunk_selection(self):
        before = "alpha\nctx1\nctx2\nctx3\nctx4\nctx5\nctx6\nomega\n"
        self._write_workspace_file(self.base_thread_id, "delete-me.txt", before)
        self.executor.create_shadow_workspace(self.base_thread_id, self.shadow_thread_id)
        self._workspace_path(self.shadow_thread_id, "delete-me.txt").unlink()

        shadow_diff = self.executor.get_shadow_diff(self.shadow_thread_id)
        entry = shadow_diff["diffs"][0]
        hunk_ids = [hunk["id"] for hunk in entry["hunks"]]

        self.assertGreaterEqual(len(hunk_ids), 1)

        result = self.executor.accept_shadow_workspace(
            self.shadow_thread_id,
            hunks=[{"path": "delete-me.txt", "ids": hunk_ids}],
        )

        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["accepted_all"])
        self.assertFalse(self._workspace_path(self.base_thread_id, "delete-me.txt").exists())

    def test_accept_shadow_workspace_rejects_conflicted_changes(self):
        self._write_workspace_file(self.base_thread_id, "conflict.txt", "base\n")
        self.executor.create_shadow_workspace(self.base_thread_id, self.shadow_thread_id)
        self._write_workspace_file(self.shadow_thread_id, "conflict.txt", "shadow\n")
        self._write_workspace_file(self.base_thread_id, "conflict.txt", "base changed externally\n")

        shadow_changes = self.executor.list_shadow_changes(self.shadow_thread_id)

        self.assertTrue(shadow_changes["changes"][0]["conflict"])

        result = self.executor.accept_shadow_workspace(self.shadow_thread_id, paths=["conflict.txt"])

        self.assertIn("error", result)
        self.assertEqual(result["error"], "Shadow workspace has conflicts")

    def test_get_shadow_diff_truncates_large_text_diff(self):
        before = "".join(f"line {i}\n" for i in range(5000))
        after = "".join(f"updated line {i}\n" for i in range(5000))
        self._write_workspace_file(self.base_thread_id, "large.txt", before)
        self.executor.create_shadow_workspace(self.base_thread_id, self.shadow_thread_id)
        self._write_workspace_file(self.shadow_thread_id, "large.txt", after)

        shadow_diff = self.executor.get_shadow_diff(self.shadow_thread_id)

        self.assertEqual(len(shadow_diff["diffs"]), 1)
        entry = shadow_diff["diffs"][0]
        self.assertTrue(entry["truncated"])
        self.assertIn("diff truncated", entry["diff"])


if __name__ == "__main__":
    unittest.main()
