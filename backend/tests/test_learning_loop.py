"""Tests for Hermes-inspired learning loop."""
import os
import json
import tempfile
import unittest

from app.agents.learning_loop import (
    NudgeManager, SessionSearchDB, FrozenMemorySnapshot,
    should_suggest_skill_creation, get_skill_creation_hint,
    get_skill_improvement_hint, SKILL_WRITING_PRINCIPLES,
    NUDGE_INTERVAL, COMPLEX_TASK_THRESHOLD, DESCRIPTION_BUDGET,
)


class LearningLoopTestBase(unittest.TestCase):
    def setUp(self):
        import app.agents.learning_loop as mod
        self._mod = mod
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_paths = (mod.SESSION_DB_PATH, mod.NUDGE_STATE_PATH)
        mod.SESSION_DB_PATH = os.path.join(self._tempdir.name, "sessions.db")
        mod.NUDGE_STATE_PATH = os.path.join(self._tempdir.name, "nudge.json")

    def tearDown(self):
        self._mod.SESSION_DB_PATH, self._mod.NUDGE_STATE_PATH = self._old_paths
        self._tempdir.cleanup()


class TestNudgeManager(LearningLoopTestBase):
    def test_no_nudge_early(self):
        nm = NudgeManager()
        for _ in range(NUDGE_INTERVAL - 1):
            result = nm.tick()
        self.assertIsNone(result)

    def test_nudge_at_interval(self):
        nm = NudgeManager()
        result = None
        for _ in range(NUDGE_INTERVAL):
            result = nm.tick()
        self.assertIsNotNone(result)
        self.assertIn("Self-Reflection", result)

    def test_nudge_resets_counter(self):
        nm = NudgeManager()
        for _ in range(NUDGE_INTERVAL):
            nm.tick()
        state = nm.get_state()
        self.assertEqual(state["task_count"], 0)
        self.assertEqual(state["total_nudges"], 1)

    def test_nudge_persists(self):
        nm = NudgeManager()
        for _ in range(5):
            nm.tick()
        # New instance should load state
        nm2 = NudgeManager()
        self.assertEqual(nm2.get_state()["task_count"], 5)

    def test_double_nudge(self):
        nm = NudgeManager()
        for _ in range(NUDGE_INTERVAL * 2):
            nm.tick()
        self.assertEqual(nm.get_state()["total_nudges"], 2)


class TestSessionSearchDB(LearningLoopTestBase):
    def test_store_and_search(self):
        db = SessionSearchDB(os.path.join(self._tempdir.name, "test.db"))
        db.store("t1", "user", "How do I deploy with Docker?")
        db.store("t1", "assistant", "You can use docker-compose to deploy.")
        results = db.search("Docker deploy")
        self.assertGreater(len(results), 0)

    def test_search_empty(self):
        db = SessionSearchDB(os.path.join(self._tempdir.name, "test2.db"))
        results = db.search("nonexistent topic")
        self.assertEqual(len(results), 0)

    def test_get_thread_history(self):
        db = SessionSearchDB(os.path.join(self._tempdir.name, "test3.db"))
        db.store("t1", "user", "Hello")
        db.store("t1", "assistant", "Hi there!")
        db.store("t2", "user", "Other thread")
        history = db.get_thread_history("t1")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")

    def test_get_stats(self):
        db = SessionSearchDB(os.path.join(self._tempdir.name, "test4.db"))
        db.store("t1", "user", "msg1")
        db.store("t1", "assistant", "msg2")
        db.store("t2", "user", "msg3")
        stats = db.get_stats()
        self.assertEqual(stats["total_messages"], 3)
        self.assertEqual(stats["total_threads"], 2)

    def test_store_with_metadata(self):
        db = SessionSearchDB(os.path.join(self._tempdir.name, "test5.db"))
        db.store("t1", "assistant", "Used web_search", skill_used="research", tool_calls=["web_search", "web_fetch"])
        history = db.get_thread_history("t1")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["skill_used"], "research")
        self.assertIn("web_search", history[0]["tool_calls"])

    def test_content_truncation(self):
        db = SessionSearchDB(os.path.join(self._tempdir.name, "test6.db"))
        long_content = "x" * 20000
        db.store("t1", "user", long_content)
        history = db.get_thread_history("t1")
        self.assertLessEqual(len(history[0]["content"]), 500)


class TestFrozenMemorySnapshot(LearningLoopTestBase):
    def test_capture_once(self):
        fm = FrozenMemorySnapshot()
        first = fm.capture("t1", "Memory content v1")
        second = fm.capture("t1", "Memory content v2")
        # Should return the frozen v1, not v2
        self.assertEqual(first, "Memory content v1")
        self.assertEqual(second, "Memory content v1")

    def test_different_threads(self):
        fm = FrozenMemorySnapshot()
        fm.capture("t1", "Thread 1 memory")
        fm.capture("t2", "Thread 2 memory")
        self.assertEqual(fm.capture("t1", "ignored"), "Thread 1 memory")
        self.assertEqual(fm.capture("t2", "ignored"), "Thread 2 memory")

    def test_invalidate(self):
        fm = FrozenMemorySnapshot()
        fm.capture("t1", "Old memory")
        fm.invalidate("t1")
        new = fm.capture("t1", "New memory")
        self.assertEqual(new, "New memory")

    def test_is_frozen(self):
        fm = FrozenMemorySnapshot()
        self.assertFalse(fm.is_frozen("t1"))
        fm.capture("t1", "content")
        self.assertTrue(fm.is_frozen("t1"))


class TestSkillSuggestions(LearningLoopTestBase):
    def test_should_suggest_below_threshold(self):
        self.assertFalse(should_suggest_skill_creation(["tool1", "tool2"]))

    def test_should_suggest_at_threshold(self):
        tools = [f"tool{i}" for i in range(COMPLEX_TASK_THRESHOLD)]
        self.assertTrue(should_suggest_skill_creation(tools))

    def test_skill_creation_hint(self):
        tools = ["web_search", "web_fetch", "write_file", "execute_python", "read_file"]
        hint = get_skill_creation_hint(tools)
        self.assertIn("5", hint)
        self.assertIn("create_skill", hint)

    def test_skill_improvement_hint(self):
        hint = get_skill_improvement_hint()
        self.assertIn("patch", hint)

    def test_skill_writing_principles(self):
        self.assertIn("trigger conditions", SKILL_WRITING_PRINCIPLES)
        self.assertIn(str(DESCRIPTION_BUDGET), SKILL_WRITING_PRINCIPLES)


if __name__ == "__main__":
    unittest.main()
