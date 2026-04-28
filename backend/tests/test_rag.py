"""Tests for RAG knowledge base."""
import os
import json
import tempfile
import unittest

from app.rag.store import KnowledgeBase, _tokenize, _tf


class TestTokenizer(unittest.TestCase):
    def test_english(self):
        tokens = _tokenize("Hello world")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)

    def test_cjk(self):
        tokens = _tokenize("你好世界")
        self.assertIn("你", tokens)
        self.assertIn("好", tokens)
        # bigrams
        self.assertIn("你好", tokens)

    def test_mixed(self):
        tokens = _tokenize("Docker 部署指南")
        self.assertIn("docker", tokens)
        self.assertIn("部", tokens)

    def test_tf(self):
        tokens = ["a", "b", "a"]
        tf = _tf(tokens)
        self.assertAlmostEqual(tf["a"], 2 / 3)
        self.assertAlmostEqual(tf["b"], 1 / 3)


class RAGTestBase(unittest.TestCase):
    def setUp(self):
        import app.rag.store as mod
        self._mod = mod
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_data_dir = mod.KnowledgeBase.DATA_DIR
        mod.KnowledgeBase.DATA_DIR = self._tempdir.name

    def tearDown(self):
        self._mod.KnowledgeBase.DATA_DIR = self._old_data_dir
        self._tempdir.cleanup()


class TestKnowledgeBase(RAGTestBase):
    def test_add_and_search(self):
        kb = KnowledgeBase()
        doc_id = kb.add_document("test.txt", "Docker is a containerization platform for deploying apps.")
        self.assertIsNotNone(doc_id)
        results = kb.search("Docker deploy")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["doc_name"], "test.txt")

    def test_add_and_remove(self):
        kb = KnowledgeBase()
        doc_id = kb.add_document("to_remove.txt", "Temporary content")
        self.assertTrue(kb.remove_document(doc_id))
        self.assertFalse(kb.remove_document("nonexistent"))
        self.assertEqual(len(kb.list_documents()), 0)

    def test_list_documents(self):
        kb = KnowledgeBase()
        kb.add_document("a.txt", "Content A")
        kb.add_document("b.txt", "Content B")
        docs = kb.list_documents()
        self.assertEqual(len(docs), 2)
        names = {d["name"] for d in docs}
        self.assertIn("a.txt", names)
        self.assertIn("b.txt", names)

    def test_get_document(self):
        kb = KnowledgeBase()
        doc_id = kb.add_document("doc.txt", "Full content here")
        doc = kb.get_document(doc_id)
        self.assertIsNotNone(doc)
        self.assertEqual(doc["name"], "doc.txt")
        self.assertEqual(doc["content"], "Full content here")
        self.assertIsNone(kb.get_document("nonexistent"))

    def test_get_context(self):
        kb = KnowledgeBase()
        kb.add_document("guide.txt", "To deploy with Docker, use docker-compose up. This will start all services.")
        context = kb.get_context("Docker deploy")
        self.assertIn("Docker", context)

    def test_search_no_results(self):
        kb = KnowledgeBase()
        results = kb.search("nonexistent topic")
        self.assertEqual(len(results), 0)

    def test_search_empty_kb(self):
        kb = KnowledgeBase()
        results = kb.search("anything")
        self.assertEqual(len(results), 0)

    def test_persistence(self):
        kb = KnowledgeBase()
        kb.add_document("persist.txt", "Persistent content")
        # Load new instance
        kb2 = KnowledgeBase()
        docs = kb2.list_documents()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["name"], "persist.txt")

    def test_chunking(self):
        kb = KnowledgeBase()
        long_content = "word " * 500  # >2500 chars, should create multiple chunks
        doc_id = kb.add_document("long.txt", long_content)
        docs = kb.list_documents()
        self.assertGreater(docs[0]["chunks"], 1)

    def test_add_file_txt(self):
        kb = KnowledgeBase()
        txt_path = os.path.join(self._tempdir.name, "test_file.txt")
        with open(txt_path, "w") as f:
            f.write("This is a test file for RAG ingestion.")
        doc_id = kb.add_file(txt_path)
        self.assertIsNotNone(doc_id)
        docs = kb.list_documents()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["name"], "test_file.txt")

    def test_add_file_markdown(self):
        kb = KnowledgeBase()
        md_path = os.path.join(self._tempdir.name, "test.md")
        with open(md_path, "w") as f:
            f.write("# Title\n\n**Bold** text with `code` and\n```python\nprint('hello')\n```\n")
        doc_id = kb.add_file(md_path)
        self.assertIsNotNone(doc_id)
        doc = kb.get_document(doc_id)
        # Markdown syntax should be stripped
        self.assertNotIn("#", doc["content"])
        self.assertNotIn("```", doc["content"])

    def test_add_file_empty(self):
        kb = KnowledgeBase()
        empty_path = os.path.join(self._tempdir.name, "empty.txt")
        with open(empty_path, "w") as f:
            f.write("")
        doc_id = kb.add_file(empty_path)
        self.assertEqual(doc_id, "")

    def test_metadata(self):
        kb = KnowledgeBase()
        doc_id = kb.add_document("meta.txt", "Content", {"source": "api", "category": "docs"})
        doc = kb.get_document(doc_id)
        self.assertEqual(doc["metadata"]["source"], "api")


if __name__ == "__main__":
    unittest.main()
