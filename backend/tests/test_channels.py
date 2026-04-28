import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router
from app.agents.store import ThreadStore


class ChannelTestBase(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        os.makedirs("data", exist_ok=True)
        import app.skills.channels as channels_mod
        self.channels_mod = channels_mod
        self._old_thread_store = channels_mod.thread_store
        self._old_channel_manager = channels_mod.channel_manager
        channels_mod.thread_store = ThreadStore(storage_path=os.path.join(self._tempdir.name, "threads"))
        channels_mod.channel_manager = channels_mod.ChannelManager()

    def tearDown(self):
        self.channels_mod.thread_store = self._old_thread_store
        self.channels_mod.channel_manager = self._old_channel_manager
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()


class TestChannelManager(ChannelTestBase, unittest.IsolatedAsyncioTestCase):
    async def test_handle_message_creates_and_reuses_session_thread(self):
        payload = self.channels_mod.ChannelMessageRequest(
            user_id="user-1",
            text="hello from channel",
            conversation_id="conv-1",
            metadata={"platform_user": "alice"},
        )
        responses = [
            {"reply": "first reply", "usage": {"input_tokens": 1}, "error": None, "events": [{"type": "tool_call"}]},
            {"reply": "second reply", "usage": {"input_tokens": 2}, "error": None, "events": []},
        ]
        with patch.object(self.channels_mod.channel_manager, "_run_agent", new=AsyncMock(side_effect=responses)):
            first = await self.channels_mod.channel_manager.handle_message("telegram", payload)
            second = await self.channels_mod.channel_manager.handle_message(
                "telegram",
                payload.model_copy(update={"text": "follow up"}),
            )

        self.assertTrue(first["created_thread"])
        self.assertFalse(second["created_thread"])
        self.assertEqual(first["thread_id"], second["thread_id"])
        self.assertEqual(first["reply"], "first reply")
        self.assertEqual(second["reply"], "second reply")
        self.assertEqual(first["session_key"], "telegram:user-1:conv-1")

        thread = await self.channels_mod.thread_store.get(first["thread_id"])
        self.assertIsNotNone(thread)
        self.assertEqual(len(thread.messages), 4)
        self.assertEqual(thread.messages[0].metadata["channel"]["platform_user"], "alice")
        self.assertEqual(thread.messages[1].metadata["delivery"], "channel")
        self.assertIn("telegram:user-1:conv-1", thread.metadata["channels"])

        saved_sessions = json.loads((self.channels_mod.channel_manager._sessions_path()).read_text(encoding="utf-8"))
        self.assertIn("telegram:user-1:conv-1", saved_sessions)
        self.assertEqual(saved_sessions["telegram:user-1:conv-1"]["thread_id"], first["thread_id"])

        listed = self.channels_mod.channel_manager.list_sessions("telegram")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["thread_id"], first["thread_id"])

    async def test_handle_message_rejects_unknown_channel(self):
        payload = self.channels_mod.ChannelMessageRequest(user_id="user-1", text="hello")
        result = await self.channels_mod.channel_manager.handle_message("discord", payload)
        self.assertIn("error", result)
        self.assertIn("supported_channels", result)


class TestChannelApi(ChannelTestBase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        try:
            self.client.close()
        except Exception:
            pass
        super().tearDown()

    def test_channel_message_endpoint_and_session_listing(self):
        with patch.object(
            self.channels_mod.channel_manager,
            "_run_agent",
            new=AsyncMock(return_value={"reply": "api reply", "usage": {"output_tokens": 3}, "error": None, "events": [{"type": "tool_summary"}]}),
        ):
            response = self.client.post(
                "/api/channels/telegram/messages",
                json={
                    "user_id": "user-2",
                    "text": "ping",
                    "conversation_id": "room-9",
                    "skills": ["plan"],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reply"], "api reply")
        self.assertEqual(payload["event_count"], 1)
        self.assertEqual(payload["session_key"], "telegram:user-2:room-9")

        sessions = self.client.get("/api/channels/telegram/sessions")
        self.assertEqual(sessions.status_code, 200)
        sessions_payload = sessions.json()
        self.assertEqual(sessions_payload["channel_type"], "telegram")
        self.assertEqual(len(sessions_payload["sessions"]), 1)
        self.assertEqual(sessions_payload["sessions"][0]["thread_id"], payload["thread_id"])

        status = self.client.get("/api/channels/telegram/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["active_sessions"], 1)
        self.assertEqual(status.json()["transport"], "webhook")
        self.assertEqual(status.json()["webhook_path"], "/api/channels/telegram/webhook")

        channels = self.client.get("/api/channels")
        self.assertEqual(channels.status_code, 200)
        telegram = next(item for item in channels.json() if item["type"] == "telegram")
        self.assertEqual(telegram["transport"], "webhook")
        self.assertEqual(telegram["message_path"], "/api/channels/telegram/messages")


if __name__ == "__main__":
    unittest.main()
