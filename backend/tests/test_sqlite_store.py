"""Unit tests for SQLiteThreadStore — verifies CRUD, fork, lineage, export/import."""
import asyncio
import os
import tempfile
import unittest

from app.agents.sqlite_store import SQLiteThreadStore
from app.models.schemas import Message


def _run(coro):
    return asyncio.run(coro)


class TestSQLiteThreadStoreCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_threads.db")
        self.store = SQLiteThreadStore(db_path=self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_create_and_get(self):
        thread = _run(self.store.create(title="Hello"))
        self.assertEqual(thread.title, "Hello")
        fetched = _run(self.store.get(thread.id))
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, thread.id)
        self.assertEqual(fetched.title, "Hello")

    def test_get_nonexistent(self):
        result = _run(self.store.get("nonexistent-id"))
        self.assertIsNone(result)

    def test_list_threads_order(self):
        t1 = _run(self.store.create(title="First"))
        t2 = _run(self.store.create(title="Second"))
        threads = _run(self.store.list_threads())
        self.assertEqual(len(threads), 2)
        # Most recently created should be first (by updated_at)
        self.assertEqual(threads[0].id, t2.id)

    def test_add_message(self):
        thread = _run(self.store.create(title="Chat"))
        msg = Message(role="user", content="Hello world")
        updated = _run(self.store.add_message(thread.id, msg))
        self.assertIsNotNone(updated)
        self.assertEqual(len(updated.messages), 1)
        self.assertEqual(updated.messages[0].content, "Hello world")
        # Title should update from first user message
        self.assertIn("Hello world", updated.title)

    def test_add_message_nonexistent_thread(self):
        msg = Message(role="user", content="Hi")
        result = _run(self.store.add_message("fake-id", msg))
        self.assertIsNone(result)

    def test_delete(self):
        thread = _run(self.store.create(title="To Delete"))
        self.assertTrue(_run(self.store.delete(thread.id)))
        self.assertIsNone(_run(self.store.get(thread.id)))

    def test_delete_nonexistent(self):
        self.assertFalse(_run(self.store.delete("fake-id")))

    def test_update_thread(self):
        thread = _run(self.store.create(title="Original"))
        thread.title = "Updated"
        _run(self.store.update_thread(thread))
        fetched = _run(self.store.get(thread.id))
        self.assertEqual(fetched.title, "Updated")


class TestSQLiteThreadStoreRelationships(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_threads.db")
        self.store = SQLiteThreadStore(db_path=self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_get_children(self):
        parent = _run(self.store.create(title="Parent"))
        child1 = _run(self.store.create(title="Child 1", parent_id=parent.id))
        child2 = _run(self.store.create(title="Child 2", parent_id=parent.id))
        children = _run(self.store.get_children(parent.id))
        self.assertEqual(len(children), 2)
        child_ids = {c.id for c in children}
        self.assertIn(child1.id, child_ids)
        self.assertIn(child2.id, child_ids)

    def test_get_lineage(self):
        grandparent = _run(self.store.create(title="GP"))
        parent = _run(self.store.create(title="P", parent_id=grandparent.id))
        child = _run(self.store.create(title="C", parent_id=parent.id))
        lineage = _run(self.store.get_lineage(child.id))
        self.assertEqual(len(lineage), 3)
        self.assertEqual(lineage[0]["id"], grandparent.id)
        self.assertEqual(lineage[1]["id"], parent.id)
        self.assertEqual(lineage[2]["id"], child.id)

    def test_fork(self):
        parent = _run(self.store.create(title="Parent"))
        msg = Message(role="user", content="Some context")
        _run(self.store.add_message(parent.id, msg))
        child = _run(self.store.fork(parent.id, "Summary of convo"))
        self.assertIsNotNone(child)
        self.assertEqual(child.parent_id, parent.id)
        self.assertEqual(child.compact_summary, "Summary of convo")
        # Parent should also have the summary
        updated_parent = _run(self.store.get(parent.id))
        self.assertEqual(updated_parent.compact_summary, "Summary of convo")

    def test_fork_nonexistent_parent(self):
        result = _run(self.store.fork("fake", "summary"))
        self.assertIsNone(result)


class TestSQLiteThreadStoreExportImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_threads.db")
        self.store = SQLiteThreadStore(db_path=self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_export_and_import_roundtrip(self):
        thread = _run(self.store.create(title="Export Me"))
        msg = Message(role="user", content="Important message")
        _run(self.store.add_message(thread.id, msg))

        exported = _run(self.store.export_thread(thread.id))
        self.assertIsNotNone(exported)
        self.assertEqual(exported["format"], "sap.trajectory.v1")
        self.assertEqual(exported["message_count"], 1)

        imported = _run(self.store.import_thread(exported, title="Imported"))
        self.assertIsNotNone(imported)
        self.assertNotEqual(imported.id, thread.id)
        self.assertEqual(imported.title, "Imported")
        self.assertEqual(len(imported.messages), 1)
        self.assertEqual(imported.messages[0].content, "Important message")

    def test_export_nonexistent(self):
        result = _run(self.store.export_thread("fake"))
        self.assertIsNone(result)

    def test_import_invalid_payload(self):
        result = _run(self.store.import_thread({"no_thread": True}))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
