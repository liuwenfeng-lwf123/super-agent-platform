import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.store import ThreadStore
from app.api.chat import router
from app.memory.layered_store import LayeredMemoryStore
from app.memory.store import MemoryStore
from app.rag.store import KnowledgeBase


class TestDemoSeedApi(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.thread_store = ThreadStore(storage_path=os.path.join(self._tempdir.name, "threads"))
        self.memory_store = MemoryStore(storage_path=os.path.join(self._tempdir.name, "memory"))
        self.layered_memory = LayeredMemoryStore(base_dir=os.path.join(self._tempdir.name, "layered"))
        self.original_kb_dir = KnowledgeBase.DATA_DIR
        KnowledgeBase.DATA_DIR = os.path.join(self._tempdir.name, "knowledge")
        self.knowledge_base = KnowledgeBase()
        self.patches = [
            patch("app.api.chat.thread_store", self.thread_store),
            patch("app.demo_seed.thread_store", self.thread_store),
            patch("app.demo_seed.memory_store", self.memory_store),
            patch("app.demo_seed.knowledge_base", self.knowledge_base),
            patch("app.memory.store.memory_store", self.memory_store),
            patch("app.memory.layered_store.layered_memory", self.layered_memory),
            patch("app.rag.store.knowledge_base", self.knowledge_base),
        ]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        try:
            self.client.close()
        except Exception:
            pass
        for item in reversed(self.patches):
            item.stop()
        KnowledgeBase.DATA_DIR = self.original_kb_dir
        self._tempdir.cleanup()

    def test_dry_run_does_not_persist_demo_data(self):
        response = self.client.post("/api/demo/seed?dry_run=true")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["summary"]["total"], 8)
        self.assertEqual(self.client.get("/api/memory").json(), [])
        self.assertEqual(self.client.get("/api/knowledge").json(), [])
        self.assertEqual(self.client.get("/api/threads").json(), [])

    def test_seed_creates_demo_memory_knowledge_and_thread(self):
        response = self.client.post("/api/demo/seed")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["failed"], 0)

        memories = self.client.get("/api/memory").json()
        docs = self.client.get("/api/knowledge").json()
        threads = self.client.get("/api/threads").json()
        self.assertEqual(len([m for m in memories if m["category"] == "demo"]), 2)
        self.assertEqual(len([d for d in docs if d["name"].startswith("[Demo]")]), 2)
        self.assertEqual(len([t for t in threads if t["title"].startswith("[Demo]")]), 1)

    def test_seed_clean_replaces_existing_demo_data(self):
        first = self.client.post("/api/demo/seed").json()
        second = self.client.post("/api/demo/seed").json()
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        memories = self.client.get("/api/memory").json()
        docs = self.client.get("/api/knowledge").json()
        threads = self.client.get("/api/threads").json()
        self.assertEqual(len([m for m in memories if m["category"] == "demo"]), 2)
        self.assertEqual(len([d for d in docs if d["name"].startswith("[Demo]")]), 2)
        self.assertEqual(len([t for t in threads if t["title"].startswith("[Demo]")]), 1)
        actions = [item["action"] for item in second["results"]]
        self.assertIn("delete_memory", actions)
        self.assertIn("delete_knowledge", actions)
        self.assertIn("delete_thread", actions)

    def test_no_clean_keeps_existing_demo_thread(self):
        self.client.post("/api/demo/seed")
        response = self.client.post("/api/demo/seed?clean=false")
        self.assertEqual(response.status_code, 200)
        threads = self.client.get("/api/threads").json()
        self.assertEqual(len([t for t in threads if t["title"].startswith("[Demo]")]), 2)


if __name__ == "__main__":
    unittest.main()
