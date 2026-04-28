import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router


class TelegramTransportTestBase(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        os.makedirs("data", exist_ok=True)
        import app.skills.telegram_transport as telegram_mod
        self.telegram_mod = telegram_mod

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()


class TestTelegramTransport(TelegramTransportTestBase):
    def test_parse_update_returns_channel_request(self):
        request = self.telegram_mod.telegram_transport.parse_update(
            {
                "update_id": 1001,
                "message": {
                    "message_id": 99,
                    "text": "hello from telegram",
                    "chat": {"id": 456, "type": "private"},
                    "from": {"id": 123, "username": "alice", "first_name": "Alice"},
                },
            }
        )
        self.assertIsNotNone(request)
        self.assertEqual(request.user_id, "123")
        self.assertEqual(request.conversation_id, "456")
        self.assertEqual(request.text, "hello from telegram")
        self.assertEqual(request.metadata["telegram_message_id"], 99)
        self.assertEqual(request.metadata["username"], "alice")

    def test_parse_update_ignores_unsupported_payload(self):
        request = self.telegram_mod.telegram_transport.parse_update({"update_id": 1002, "callback_query": {"id": "cbq-1"}})
        self.assertIsNone(request)


class TestTelegramWebhookApi(TelegramTransportTestBase):
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

    def test_webhook_forwards_message_and_returns_outbound_payload(self):
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "secret-123"}, clear=False):
            with patch.object(
                self.telegram_mod.channel_manager,
                "handle_message",
                new=AsyncMock(
                    return_value={
                        "thread_id": "thread-1",
                        "session_key": "telegram:123:456",
                        "configured": True,
                        "reply": "hello back",
                        "error": None,
                        "event_count": 0,
                        "events": [],
                    }
                ),
            ) as handle_message:
                response = self.client.post(
                    "/api/channels/telegram/webhook",
                    headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
                    json={
                        "update_id": 2001,
                        "message": {
                            "message_id": 12,
                            "text": "hello bot",
                            "chat": {"id": 456, "type": "private"},
                            "from": {"id": 123, "username": "alice"},
                        },
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["processed"])
        self.assertEqual(payload["thread_id"], "thread-1")
        self.assertEqual(payload["telegram"]["chat_id"], "456")
        self.assertEqual(payload["outbound"]["method"], "sendMessage")
        self.assertEqual(payload["outbound"]["text"], "hello back")
        handle_message.assert_awaited_once()
        called_channel, called_request = handle_message.await_args.args
        self.assertEqual(called_channel, "telegram")
        self.assertEqual(called_request.user_id, "123")
        self.assertEqual(called_request.conversation_id, "456")
        self.assertEqual(called_request.text, "hello bot")

    def test_webhook_rejects_invalid_secret(self):
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "secret-123"}, clear=False):
            response = self.client.post(
                "/api/channels/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
                json={
                    "update_id": 2002,
                    "message": {
                        "message_id": 13,
                        "text": "hello",
                        "chat": {"id": 456, "type": "private"},
                        "from": {"id": 123},
                    },
                },
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Invalid Telegram webhook secret", response.text)

    def test_webhook_ignores_unsupported_update(self):
        with patch.dict(os.environ, {}, clear=False):
            with patch.object(self.telegram_mod.channel_manager, "handle_message", new=AsyncMock()) as handle_message:
                response = self.client.post(
                    "/api/channels/telegram/webhook",
                    json={"update_id": 2003, "callback_query": {"id": "cbq-1"}},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ignored"])
        self.assertEqual(payload["reason"], "unsupported_update")
        handle_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
