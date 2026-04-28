import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.store import ThreadStore
from app.api.chat import router
from app.models.schemas import Message


class TestThreadTrajectoryStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.store = ThreadStore(storage_path=os.path.join(self._tempdir.name, "threads"))

    async def asyncTearDown(self):
        self._tempdir.cleanup()

    async def test_export_and_import_thread_roundtrip(self):
        parent = await self.store.create(title="Original", parent_id="root-1", compact_summary="summary")
        await self.store.add_message(parent.id, Message(role="user", content="hello", metadata={"source": "test"}))
        await self.store.add_message(parent.id, Message(role="assistant", content="hi there"))
        child = await self.store.create(title="Child", parent_id=parent.id)

        exported = await self.store.export_thread(parent.id)
        self.assertEqual(exported["format"], "sap.trajectory.v1")
        self.assertEqual(exported["message_count"], 2)
        self.assertEqual(exported["thread"]["title"], "hello")
        self.assertEqual(exported["children"][0]["id"], child.id)

        imported = await self.store.import_thread(exported, title="Replay Copy", parent_id="fork-parent")
        self.assertIsNotNone(imported)
        self.assertNotEqual(imported.id, parent.id)
        self.assertEqual(imported.title, "Replay Copy")
        self.assertEqual(imported.parent_id, "fork-parent")
        self.assertEqual(len(imported.messages), 2)
        self.assertEqual(imported.messages[0].content, "hello")
        self.assertEqual(imported.metadata["trajectory"]["source_thread_id"], parent.id)
        self.assertEqual(imported.metadata["trajectory"]["source_parent_id"], "root-1")

    async def test_import_thread_rejects_invalid_payload(self):
        imported = await self.store.import_thread({"bad": "payload"})
        self.assertIsNone(imported)


class TestTrajectoryApi(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.store = ThreadStore(storage_path=os.path.join(self._tempdir.name, "threads"))
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self._thread_store_patch = patch("app.api.chat.thread_store", self.store)
        self._thread_store_patch.start()

    def tearDown(self):
        self._thread_store_patch.stop()
        try:
            self.client.close()
        except Exception:
            pass
        self._tempdir.cleanup()

    def test_export_and_replay_trajectory_endpoints(self):
        import asyncio

        async def seed():
            thread = await self.store.create(title="API Thread")
            await self.store.add_message(thread.id, Message(role="user", content="hello api"))
            await self.store.add_message(thread.id, Message(role="assistant", content="hello back"))
            return thread

        thread = asyncio.run(seed())

        exported = self.client.get(f"/api/threads/{thread.id}/trajectory")
        self.assertEqual(exported.status_code, 200)
        exported_payload = exported.json()
        self.assertEqual(exported_payload["thread"]["id"], thread.id)
        self.assertEqual(exported_payload["message_count"], 2)

        replay = self.client.post(
            "/api/trajectories/replay",
            json={
                "trajectory": exported_payload,
                "title": "Replayed Thread",
                "parent_id": "replay-root",
            },
        )
        self.assertEqual(replay.status_code, 200)
        replay_payload = replay.json()
        self.assertEqual(replay_payload["title"], "Replayed Thread")
        self.assertEqual(replay_payload["message_count"], 2)
        self.assertEqual(replay_payload["parent_id"], "replay-root")
        self.assertEqual(replay_payload["trajectory"]["source_thread_id"], thread.id)


if __name__ == "__main__":
    unittest.main()
