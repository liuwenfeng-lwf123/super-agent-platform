"""
Token Budget Control — unit tests for toggles, presets, daily budget, and feature guards.
"""
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.token_budget import router, _TOKEN_FEATURES, _PRESETS
from app.config import Settings


class TokenBudgetTestBase(unittest.TestCase):
    """Base with TestClient and isolated .env."""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._env_path = os.path.join(self._tempdir.name, ".env")
        with open(self._env_path, "w") as f:
            f.write("")

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

        # Patch _ENV_PATH to use temp file
        self._env_patch = patch("app.api.token_budget._ENV_PATH", self._env_path)
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._tempdir.cleanup()


class TestTokenBudgetEndpoint(TokenBudgetTestBase):
    """Tests for GET /api/features/token-budget"""

    def test_returns_all_features(self):
        resp = self.client.get("/api/features/token-budget")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("features", data)
        self.assertEqual(len(data["features"]), len(_TOKEN_FEATURES))

    def test_feature_fields(self):
        resp = self.client.get("/api/features/token-budget")
        data = resp.json()
        for f in data["features"]:
            self.assertIn("id", f)
            self.assertIn("name", f)
            self.assertIn("description", f)
            self.assertIn("enabled", f)
            self.assertIn("est_tokens_per_use", f)
            self.assertIsInstance(f["est_tokens_per_use"], int)
            self.assertGreater(f["est_tokens_per_use"], 0)

    def test_totals_add_up(self):
        resp = self.client.get("/api/features/token-budget")
        data = resp.json()
        total = sum(f["est_tokens_per_use"] for f in data["features"])
        self.assertEqual(data["total_est_tokens"], total)
        active = sum(f["est_tokens_per_use"] for f in data["features"] if f["enabled"])
        self.assertEqual(data["active_est_tokens"], active)
        self.assertEqual(data["saved_tokens"], total - active)


class TestToggleTokenFeature(TokenBudgetTestBase):
    """Tests for POST /api/features/token-budget/toggle"""

    def test_toggle_off(self):
        resp = self.client.post("/api/features/token-budget/toggle",
                                json={"feature_id": "speculation", "enable": False})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertFalse(data["enabled"])
        # Verify .env was written
        with open(self._env_path) as f:
            content = f.read()
        self.assertIn("ENABLE_SPECULATION=false", content)

    def test_toggle_on(self):
        resp = self.client.post("/api/features/token-budget/toggle",
                                json={"feature_id": "speculation", "enable": True})
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["enabled"])

    def test_toggle_unknown_feature(self):
        resp = self.client.post("/api/features/token-budget/toggle",
                                json={"feature_id": "nonexistent", "enable": False})
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("error", data)

    def test_toggle_updates_settings(self):
        from app.config import settings
        original = settings.enable_speculation
        self.client.post("/api/features/token-budget/toggle",
                         json={"feature_id": "speculation", "enable": False})
        self.assertFalse(settings.enable_speculation)
        # Restore
        self.client.post("/api/features/token-budget/toggle",
                         json={"feature_id": "speculation", "enable": True})
        self.assertTrue(settings.enable_speculation)

    def test_saved_tokens_after_toggle(self):
        self.client.post("/api/features/token-budget/toggle",
                         json={"feature_id": "gepa_evolution", "enable": False})
        resp = self.client.get("/api/features/token-budget")
        data = resp.json()
        self.assertGreater(data["saved_tokens"], 0)
        # Restore
        self.client.post("/api/features/token-budget/toggle",
                         json={"feature_id": "gepa_evolution", "enable": True})


class TestPresets(TokenBudgetTestBase):
    """Tests for preset modes."""

    def test_get_presets(self):
        resp = self.client.get("/api/features/token-budget/presets")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("presets", data)
        self.assertIn("minimal", data["presets"])
        self.assertIn("standard", data["presets"])
        self.assertIn("full", data["presets"])

    def test_apply_minimal_preset(self):
        resp = self.client.post("/api/features/token-budget/preset/minimal")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        # Verify all features are off
        budget = self.client.get("/api/features/token-budget").json()
        for f in budget["features"]:
            self.assertFalse(f["enabled"], f"Feature {f['id']} should be disabled in minimal mode")
        # Restore
        self.client.post("/api/features/token-budget/preset/full")

    def test_apply_full_preset(self):
        # First go minimal
        self.client.post("/api/features/token-budget/preset/minimal")
        # Then full
        resp = self.client.post("/api/features/token-budget/preset/full")
        data = resp.json()
        self.assertTrue(data["success"])
        budget = self.client.get("/api/features/token-budget").json()
        for f in budget["features"]:
            self.assertTrue(f["enabled"], f"Feature {f['id']} should be enabled in full mode")

    def test_active_preset_detected(self):
        self.client.post("/api/features/token-budget/preset/full")
        resp = self.client.get("/api/features/token-budget/presets")
        data = resp.json()
        self.assertEqual(data["active_preset"], "full")

    def test_unknown_preset(self):
        resp = self.client.post("/api/features/token-budget/preset/nonexistent")
        data = resp.json()
        self.assertFalse(data["success"])


class TestDailyBudget(TokenBudgetTestBase):
    """Tests for daily budget get/set."""

    def test_get_daily_usage(self):
        resp = self.client.get("/api/features/token-budget/daily")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("date", data)
        self.assertIn("total_tokens", data)
        self.assertIn("budget", data)
        self.assertIn("is_over_budget", data)

    def test_set_daily_budget(self):
        resp = self.client.post("/api/features/token-budget/daily",
                                json={"daily_token_budget": 500000})
        data = resp.json()
        self.assertTrue(data["success"])
        from app.config import settings
        self.assertEqual(settings.daily_token_budget, 500000)
        # Check .env
        with open(self._env_path) as f:
            content = f.read()
        self.assertIn("DAILY_TOKEN_BUDGET=500000", content)
        # Restore
        self.client.post("/api/features/token-budget/daily",
                         json={"daily_token_budget": 0})

    def test_set_unlimited(self):
        resp = self.client.post("/api/features/token-budget/daily",
                                json={"daily_token_budget": 0})
        data = resp.json()
        self.assertTrue(data["success"])


class TestFeatureGuards(unittest.TestCase):
    """Tests that feature guards actually block LLM calls."""

    def test_memory_extraction_guard(self):
        from app.config import settings
        import asyncio
        from app.memory.extract_memories import MemoryExtractor

        extractor = MemoryExtractor()
        original = settings.enable_memory_extraction
        settings.enable_memory_extraction = False
        result = asyncio.get_event_loop().run_until_complete(
            extractor.maybe_extract([{"role": "user", "content": "hi"}] * 20)
        )
        self.assertEqual(result, [])
        settings.enable_memory_extraction = original

    def test_intent_classify_guard(self):
        from app.config import settings
        import asyncio
        from app.agents.intent_classify import classify_intent

        original = settings.enable_intent_classify
        settings.enable_intent_classify = False
        result = asyncio.get_event_loop().run_until_complete(
            classify_intent("some ambiguous message")
        )
        # Should return fallback without calling LLM
        self.assertIn("intent", result)
        self.assertIn("confidence", result)
        settings.enable_intent_classify = original


class TestEnvLock(TokenBudgetTestBase):
    """Test that concurrent .env writes don't corrupt the file."""

    def test_concurrent_writes(self):
        import threading

        errors = []

        def toggle(fid, enable):
            try:
                self.client.post("/api/features/token-budget/toggle",
                                 json={"feature_id": fid, "enable": enable})
            except Exception as e:
                errors.append(e)

        threads = []
        feature_ids = list(_TOKEN_FEATURES.keys())
        for i in range(20):
            fid = feature_ids[i % len(feature_ids)]
            t = threading.Thread(target=toggle, args=(fid, i % 2 == 0))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent writes produced errors: {errors}")
        # .env should be valid — each key appears exactly once
        with open(self._env_path) as f:
            lines = f.readlines()
        keys = [l.split("=")[0] for l in lines if "=" in l and not l.startswith("#")]
        self.assertEqual(len(keys), len(set(keys)), "Duplicate keys found in .env after concurrent writes")
        # Restore all to True
        self.client.post("/api/features/token-budget/preset/full")


if __name__ == "__main__":
    unittest.main()
