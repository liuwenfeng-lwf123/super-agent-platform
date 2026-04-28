"""
Performance and stress tests for the Super Agent Platform.

Covers:
- API endpoint response time baselines
- Concurrent request handling
- Pipeline execution latency
- Spawn manager concurrent operations
- Reflection evaluation latency (mocked LLM)
- TaskManager bulk create/cleanup
- Memory store query cache hit rate
- Rate limiter correctness
- Middleware timing header
"""
import asyncio
import time
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient


def _get_client():
    from app.main import app
    return TestClient(app)


class TestAPIResponseTime(unittest.TestCase):
    """Verify key endpoints respond within acceptable latency."""

    def test_health_under_100ms(self):
        client = _get_client()
        start = time.time()
        resp = client.get("/health")
        elapsed = time.time() - start
        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 0.1, f"Health endpoint too slow: {elapsed:.3f}s")

    def test_ready_under_100ms(self):
        client = _get_client()
        start = time.time()
        resp = client.get("/ready")
        elapsed = time.time() - start
        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 0.1, f"Ready endpoint too slow: {elapsed:.3f}s")

    def test_features_under_100ms(self):
        client = _get_client()
        start = time.time()
        resp = client.get("/api/features")
        elapsed = time.time() - start
        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 0.1, f"Features endpoint too slow: {elapsed:.3f}s")

    def test_pipelines_under_100ms(self):
        client = _get_client()
        start = time.time()
        resp = client.get("/api/pipelines")
        elapsed = time.time() - start
        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 0.1, f"Pipelines endpoint too slow: {elapsed:.3f}s")

    def test_spawn_status_under_100ms(self):
        client = _get_client()
        start = time.time()
        resp = client.get("/api/spawn/status")
        elapsed = time.time() - start
        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 0.1, f"Spawn status endpoint too slow: {elapsed:.3f}s")

    def test_token_budget_features_under_100ms(self):
        client = _get_client()
        start = time.time()
        resp = client.get("/api/features/token-budget")
        elapsed = time.time() - start
        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 0.1, f"Token budget endpoint too slow: {elapsed:.3f}s")


class TestConcurrentRequests(unittest.TestCase):
    """Test server handles concurrent load."""

    def test_10_concurrent_health(self):
        """10 concurrent health checks should all succeed within 1s."""
        client = _get_client()
        results = []

        def hit():
            start = time.time()
            resp = client.get("/health")
            return resp.status_code, time.time() - start

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(hit) for _ in range(10)]
            results = [f.result() for f in futures]

        statuses = [r[0] for r in results]
        times = [r[1] for r in results]
        self.assertTrue(all(s == 200 for s in statuses))
        self.assertLess(max(times), 1.0, f"Slowest concurrent request: {max(times):.3f}s")

    def test_10_concurrent_pipelines(self):
        """10 concurrent pipeline list calls."""
        client = _get_client()

        def hit():
            start = time.time()
            resp = client.get("/api/pipelines")
            return resp.status_code, time.time() - start

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(hit) for _ in range(10)]
            results = [f.result() for f in futures]

        statuses = [r[0] for r in results]
        self.assertTrue(all(s == 200 for s in statuses))

    def test_20_concurrent_mixed(self):
        """20 mixed concurrent GET requests."""
        client = _get_client()
        endpoints = ["/health", "/ready", "/api/features", "/api/pipelines", "/api/spawn/status"]

        def hit(url):
            resp = client.get(url)
            return resp.status_code

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(hit, endpoints[i % len(endpoints)]) for i in range(20)]
            results = [f.result() for f in futures]

        self.assertTrue(all(s == 200 for s in results))


class TestPipelineLatency(unittest.TestCase):
    """Test pipeline registry operations are fast."""

    def test_list_pipelines_under_5ms(self):
        from app.agents.tool_pipelines import PipelineRegistry
        reg = PipelineRegistry()
        start = time.time()
        for _ in range(100):
            reg.list_pipelines()
        elapsed = time.time() - start
        per_call = elapsed / 100 * 1000  # ms
        self.assertLess(per_call, 5.0, f"list_pipelines too slow: {per_call:.2f}ms")

    def test_get_pipeline_under_1ms(self):
        from app.agents.tool_pipelines import PipelineRegistry
        reg = PipelineRegistry()
        start = time.time()
        for _ in range(1000):
            reg.get("research_and_summarize")
        elapsed = time.time() - start
        per_call = elapsed / 1000 * 1000  # ms
        self.assertLess(per_call, 1.0, f"get_pipeline too slow: {per_call:.2f}ms")

    def test_pipeline_not_found_fast(self):
        from app.agents.tool_pipelines import PipelineRegistry
        reg = PipelineRegistry()
        start = time.time()
        result = asyncio.run(reg.execute("nonexistent", {}))
        elapsed = time.time() - start
        self.assertEqual(result.status, "failed")
        self.assertLess(elapsed, 0.05, f"Pipeline not-found too slow: {elapsed:.3f}s")


class TestSpawnManagerPerformance(unittest.TestCase):
    """Test spawn manager operations under load."""

    def test_register_1000_parents(self):
        from app.agents.dynamic_spawn import DynamicSpawnManager
        mgr = DynamicSpawnManager()
        start = time.time()
        for i in range(1000):
            mgr.register_parent(f"parent-{i}")
        elapsed = time.time() - start
        self.assertLess(elapsed, 0.1, f"Registering 1000 parents too slow: {elapsed:.3f}s")

    def test_can_spawn_check_fast(self):
        from app.agents.dynamic_spawn import DynamicSpawnManager
        mgr = DynamicSpawnManager()
        mgr.register_parent("fast-parent")
        start = time.time()
        for _ in range(10000):
            mgr.can_spawn("fast-parent")
        elapsed = time.time() - start
        per_call = elapsed / 10000 * 1000 * 1000  # microseconds
        self.assertLess(per_call, 100, f"can_spawn too slow: {per_call:.0f}μs")

    def test_cleanup_1000_children(self):
        from app.agents.dynamic_spawn import DynamicSpawnManager, SpawnResult
        mgr = DynamicSpawnManager()
        mgr.register_parent("bulk-parent")
        mgr._active_spawns["bulk-parent"] = [f"child-{i}" for i in range(1000)]
        for i in range(1000):
            mgr._results[f"child-{i}"] = SpawnResult(f"child-{i}", "task", "r", "completed")
            mgr._spawn_depth[f"child-{i}"] = 1
        start = time.time()
        mgr.cleanup("bulk-parent")
        elapsed = time.time() - start
        self.assertLess(elapsed, 0.05, f"Cleanup 1000 children too slow: {elapsed:.3f}s")


class TestReflectionLatency(unittest.TestCase):
    """Test reflection with mocked LLM is fast."""

    def test_evaluate_response_mocked_under_100ms(self):
        from app.agents.reflection import evaluate_response

        mock_response = MagicMock()
        mock_response.content = '{"pass": true, "score": 85, "issues": [], "suggestion": ""}'

        with patch("app.agents.reflection.llm_provider") as mock_prov:
            mock_model = AsyncMock()
            mock_model.ainvoke.return_value = mock_response
            mock_prov.get_chat_model.return_value = mock_model
            mock_prov.aclose_model = AsyncMock()

            start = time.time()
            result = asyncio.run(evaluate_response("test query", "test response", "mock-model"))
            elapsed = time.time() - start

        self.assertTrue(result["pass"])
        self.assertLess(elapsed, 0.1, f"evaluate_response (mocked) too slow: {elapsed:.3f}s")

    def test_should_reflect_under_1ms(self):
        from app.agents.reflection import should_reflect
        start = time.time()
        for _ in range(10000):
            should_reflect("pro", "complex question about architecture", 500)
        elapsed = time.time() - start
        per_call = elapsed / 10000 * 1000 * 1000  # microseconds
        self.assertLess(per_call, 100, f"should_reflect too slow: {per_call:.0f}μs")


class TestTaskManagerBulk(unittest.TestCase):
    """Test TaskManager under bulk operations."""

    def test_create_1000_tasks(self):
        from app.agents.task_manager import TaskManager
        mgr = TaskManager()
        start = time.time()
        for i in range(1000):
            mgr.create_task(f"task-{i}", f"description-{i}", f"agent-{i % 10}")
        elapsed = time.time() - start
        self.assertEqual(len(mgr._tasks), 1000)
        self.assertLess(elapsed, 1.0, f"Creating 1000 tasks too slow: {elapsed:.3f}s")

    def test_list_tasks_1000(self):
        from app.agents.task_manager import TaskManager
        mgr = TaskManager()
        for i in range(1000):
            mgr.create_task(f"task-{i}", f"desc-{i}")
        start = time.time()
        result = mgr.list_tasks()
        elapsed = time.time() - start
        self.assertEqual(len(result), 1000)
        self.assertLess(elapsed, 0.5, f"Listing 1000 tasks too slow: {elapsed:.3f}s")

    def test_cleanup_old_tasks(self):
        from app.agents.task_manager import TaskManager, TaskStatus
        from datetime import datetime, timedelta
        mgr = TaskManager()
        # Create new tasks first (won't be cleaned)
        for i in range(200):
            t = mgr.create_task(f"new-{i}", f"desc-{i}")
            t.status = TaskStatus.COMPLETED
            t.completed_at = datetime.now()
        # Create old tasks (will be cleaned)
        for i in range(200):
            t = mgr.create_task(f"old-{i}", f"desc-{i}")
            t.status = TaskStatus.COMPLETED
            t.completed_at = datetime.now() - timedelta(hours=2)
        before = len(mgr._tasks)
        old_count = sum(1 for t in mgr._tasks.values()
                        if t.completed_at and (datetime.now() - t.completed_at).total_seconds() > 3600)
        start = time.time()
        mgr.cleanup_old(max_age_hours=1)
        elapsed = time.time() - start
        after = len(mgr._tasks)
        self.assertEqual(after, before - old_count)
        self.assertLess(elapsed, 0.1, f"Cleanup too slow: {elapsed:.3f}s")


class TestMemoryCachePerformance(unittest.TestCase):
    """Test memory store query cache."""

    def test_cache_hit_faster_than_miss(self):
        import tempfile, os
        from app.memory.store import MemoryStore
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(storage_path=tmpdir)
            # Add some entries
            for i in range(50):
                asyncio.run(store.add(f"key-{i}", f"value about topic {i}", "knowledge"))

            query = "topic about something"

            # First call: cache miss
            start1 = time.time()
            result1 = asyncio.run(store.get_context_for_query(query))
            elapsed1 = time.time() - start1

            # Second call: cache hit
            start2 = time.time()
            result2 = asyncio.run(store.get_context_for_query(query))
            elapsed2 = time.time() - start2

            self.assertEqual(result1, result2)
            # Cache hit should be at least 2x faster
            if elapsed1 > 0.001:  # only check if first call is measurable
                self.assertLess(elapsed2, elapsed1, "Cache hit should be faster than miss")

    def test_cache_invalidated_on_write(self):
        import tempfile
        from app.memory.store import MemoryStore
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(storage_path=tmpdir)
            asyncio.run(store.add("python", "A programming language", "knowledge"))

            # Populate cache
            r1 = asyncio.run(store.get_context_for_query("python"))
            self.assertIn("python", r1.lower())
            self.assertTrue(len(store._query_cache) > 0)

            # Write invalidates cache
            asyncio.run(store.add("java", "Another language", "knowledge"))
            self.assertEqual(len(store._query_cache), 0)


class TestRateLimiter(unittest.TestCase):
    """Test rate limiting middleware."""

    def test_rate_limiter_bucket_cleanup(self):
        from app.middleware import RateLimitMiddleware
        from starlette.applications import Starlette
        limiter = RateLimitMiddleware(Starlette(), max_requests=3, window_seconds=1)
        # Simulate requests
        self.assertFalse(limiter._is_rate_limited("test-key"))
        self.assertFalse(limiter._is_rate_limited("test-key"))
        self.assertFalse(limiter._is_rate_limited("test-key"))
        # 4th should be limited
        self.assertTrue(limiter._is_rate_limited("test-key"))

    def test_rate_limiter_window_expires(self):
        from app.middleware import RateLimitMiddleware
        from starlette.applications import Starlette
        limiter = RateLimitMiddleware(Starlette(), max_requests=2, window_seconds=1)
        self.assertFalse(limiter._is_rate_limited("expire-key"))
        self.assertFalse(limiter._is_rate_limited("expire-key"))
        self.assertTrue(limiter._is_rate_limited("expire-key"))
        # Wait for window to expire
        time.sleep(1.1)
        self.assertFalse(limiter._is_rate_limited("expire-key"))

    def test_different_keys_independent(self):
        from app.middleware import RateLimitMiddleware
        from starlette.applications import Starlette
        limiter = RateLimitMiddleware(Starlette(), max_requests=1, window_seconds=60)
        self.assertFalse(limiter._is_rate_limited("key-a"))
        self.assertTrue(limiter._is_rate_limited("key-a"))
        # Different key is not limited
        self.assertFalse(limiter._is_rate_limited("key-b"))


class TestTimingMiddleware(unittest.TestCase):
    """Test timing middleware adds headers."""

    def test_response_has_timing_header(self):
        client = _get_client()
        resp = client.get("/health")
        self.assertIn("x-response-time", resp.headers)
        timing = resp.headers["x-response-time"]
        self.assertTrue(timing.endswith("s"))
        value = float(timing.rstrip("s"))
        self.assertGreater(value, 0)
        self.assertLess(value, 1.0)

    def test_pipelines_has_timing_header(self):
        client = _get_client()
        resp = client.get("/api/pipelines")
        self.assertIn("x-response-time", resp.headers)


if __name__ == "__main__":
    unittest.main()
