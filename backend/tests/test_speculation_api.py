import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router
from app.agents.prompt_features import SpeculationRecord, speculation_store
from app.sandbox.manager import sandbox_executor


class SpeculationApiTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        speculation_store._records.clear()
        speculation_store._subscribers.clear()
        sandbox_executor._thread_workspaces.clear()
        sandbox_executor._shadow_workspaces.clear()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        speculation_store._records.clear()
        speculation_store._subscribers.clear()
        sandbox_executor._thread_workspaces.clear()
        sandbox_executor._shadow_workspaces.clear()
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()

    def _workspace_path(self, thread_id: str, rel_path: str) -> Path:
        return Path(sandbox_executor.get_workspace_dir(thread_id)) / rel_path

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

    def _seed_record(self, thread_id: str, shadow_thread_id: str, status: str = "completed") -> SpeculationRecord:
        record = SpeculationRecord(
            thread_id=thread_id,
            suggestion="继续推进下一步",
            assistant_content="done",
            user_message="继续",
            tool_summary=None,
            model=None,
            preview=None,
            created_at=datetime.now().isoformat(),
            shadow_thread_id=shadow_thread_id,
            status=status,
        )
        speculation_store._records[thread_id] = record
        return record

    def test_get_speculation_diff_route_returns_hunks(self):
        thread_id = "thread-api-diff"
        shadow_thread_id = f"spec-{thread_id}"
        self._write_workspace_file(thread_id, "notes.txt", "alpha\nctx1\nctx2\nctx3\nctx4\nctx5\nctx6\nomega\n")
        sandbox_executor.create_shadow_workspace(thread_id, shadow_thread_id)
        self._write_workspace_file(shadow_thread_id, "notes.txt", "alpha changed\nctx1\nctx2\nctx3\nctx4\nctx5\nctx6\nomega\nnew tail\n")
        self._seed_record(thread_id, shadow_thread_id)

        response = self.client.get(f"/api/speculation/{thread_id}/diff")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["shadow_thread_id"], shadow_thread_id)
        self.assertEqual(len(payload["diffs"]), 1)
        self.assertEqual(payload["diffs"][0]["path"], "notes.txt")
        self.assertGreaterEqual(len(payload["diffs"][0]["hunks"]), 1)

    def test_accept_speculation_route_supports_paths_payload(self):
        thread_id = "thread-api-files"
        shadow_thread_id = f"spec-{thread_id}"
        self._write_workspace_file(thread_id, "a.txt", "base-a\n")
        self._write_workspace_file(thread_id, "b.txt", "base-b\n")
        sandbox_executor.create_shadow_workspace(thread_id, shadow_thread_id)
        self._write_workspace_file(shadow_thread_id, "a.txt", "shadow-a\n")
        self._write_workspace_file(shadow_thread_id, "b.txt", "shadow-b\n")
        self._seed_record(thread_id, shadow_thread_id)

        response = self.client.post(f"/api/speculation/{thread_id}/accept", json={"paths": ["a.txt"]})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "partially_accepted")
        self.assertFalse(payload["accept_result"]["accepted_all"])
        self.assertEqual(self._read_workspace_file(thread_id, "a.txt"), "shadow-a\n")
        self.assertEqual(self._read_workspace_file(thread_id, "b.txt"), "base-b\n")

        follow_up = self.client.post(f"/api/speculation/{thread_id}/accept", json={"paths": ["b.txt"]})

        self.assertEqual(follow_up.status_code, 200)
        follow_up_payload = follow_up.json()
        self.assertEqual(follow_up_payload["status"], "accepted")
        self.assertTrue(follow_up_payload["accept_result"]["accepted_all"])
        self.assertEqual(self._read_workspace_file(thread_id, "b.txt"), "shadow-b\n")

    def test_accept_speculation_route_supports_hunks_payload(self):
        thread_id = "thread-api-hunks"
        shadow_thread_id = f"spec-{thread_id}"
        before = "alpha\nctx1\nctx2\nctx3\nctx4\nctx5\nctx6\nomega\n"
        after = "alpha changed\nctx1\nctx2\nctx3\nctx4\nctx5\nctx6\nomega\nnew tail\n"
        self._write_workspace_file(thread_id, "notes.txt", before)
        sandbox_executor.create_shadow_workspace(thread_id, shadow_thread_id)
        self._write_workspace_file(shadow_thread_id, "notes.txt", after)
        self._seed_record(thread_id, shadow_thread_id)

        diff_response = self.client.get(f"/api/speculation/{thread_id}/diff")
        self.assertEqual(diff_response.status_code, 200)
        hunks = diff_response.json()["diffs"][0]["hunks"]
        self.assertEqual(len(hunks), 2)

        response = self.client.post(
            f"/api/speculation/{thread_id}/accept",
            json={"hunks": [{"path": "notes.txt", "ids": [hunks[0]["id"]]}]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "partially_accepted")
        self.assertEqual(
            self._read_workspace_file(thread_id, "notes.txt"),
            "alpha changed\nctx1\nctx2\nctx3\nctx4\nctx5\nctx6\nomega\n",
        )

        remaining_diff = self.client.get(f"/api/speculation/{thread_id}/diff")
        self.assertEqual(remaining_diff.status_code, 200)
        remaining_hunks = remaining_diff.json()["diffs"][0]["hunks"]
        self.assertEqual(len(remaining_hunks), 1)

        follow_up = self.client.post(
            f"/api/speculation/{thread_id}/accept",
            json={"hunks": [{"path": "notes.txt", "ids": [remaining_hunks[0]["id"]]}]},
        )

        self.assertEqual(follow_up.status_code, 200)
        follow_up_payload = follow_up.json()
        self.assertEqual(follow_up_payload["status"], "accepted")
        self.assertEqual(self._read_workspace_file(thread_id, "notes.txt"), after)

    def test_accept_speculation_route_rejects_binary_hunks_payload(self):
        thread_id = "thread-api-binary"
        shadow_thread_id = f"spec-{thread_id}"
        self._write_workspace_bytes(thread_id, "blob.bin", b"\x00\x01base")
        sandbox_executor.create_shadow_workspace(thread_id, shadow_thread_id)
        self._write_workspace_bytes(shadow_thread_id, "blob.bin", b"\x00\x01shadow")
        self._seed_record(thread_id, shadow_thread_id)

        response = self.client.post(
            f"/api/speculation/{thread_id}/accept",
            json={"hunks": [{"path": "blob.bin", "ids": ["hunk-0"]}]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("error", payload["accept_result"])
        self.assertEqual(payload["accept_result"]["error"], "Binary files do not support hunk acceptance")

    def test_accept_speculation_route_rejects_conflicted_file_payload(self):
        thread_id = "thread-api-conflict"
        shadow_thread_id = f"spec-{thread_id}"
        self._write_workspace_file(thread_id, "conflict.txt", "base\n")
        sandbox_executor.create_shadow_workspace(thread_id, shadow_thread_id)
        self._write_workspace_file(shadow_thread_id, "conflict.txt", "shadow\n")
        self._write_workspace_file(thread_id, "conflict.txt", "changed outside\n")
        self._seed_record(thread_id, shadow_thread_id)

        response = self.client.post(
            f"/api/speculation/{thread_id}/accept",
            json={"paths": ["conflict.txt"]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("error", payload["accept_result"])
        self.assertEqual(payload["accept_result"]["error"], "Shadow workspace has conflicts")


if __name__ == "__main__":
    unittest.main()
