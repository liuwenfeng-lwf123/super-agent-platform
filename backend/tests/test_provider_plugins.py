import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


class ProviderPluginsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="provider_plugins_")
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmp)
        sys.path.insert(0, self._tmp)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        if self._tmp in sys.path:
            sys.path.remove(self._tmp)
        sys.modules.pop("fake_mem_module", None)
        sys.modules.pop("fake_ctx_module", None)
        sys.modules.pop("broken_mem_module", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _reload_module(self):
        module = importlib.import_module("app.agents.provider_plugins")
        return importlib.reload(module)

    def _write_memory_plugin(self, name="fake_mem", prefix="FAKE"):
        plugin_dir = Path(self._tmp) / ".hermes" / "plugins" / name
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "memory_provider.json").write_text(json.dumps({
            "name": name,
            "description": "test memory provider",
            "module": "fake_mem_module",
            "class": "FakeMemoryProvider",
            "init_kwargs": {"prefix": prefix},
        }))
        Path(self._tmp, "fake_mem_module.py").write_text(
            "class FakeMemoryProvider:\n"
            "    def __init__(self, prefix='FAKE'):\n"
            "        self.prefix = prefix\n"
            "        self.entries = []\n"
            "    async def add(self, key, value, metadata=None):\n"
            "        self.entries.append({'key': key, 'value': value, 'metadata': metadata or {}})\n"
            "        return f'{self.prefix}-{len(self.entries)}'\n"
            "    async def search(self, query, limit=10):\n"
            "        return [e for e in self.entries if query in e['value']][:limit]\n"
            "    async def get_context_for_query(self, query, max_entries=5):\n"
            "        hits = await self.search(query, max_entries)\n"
            "        return '\\n'.join(f\"{self.prefix}:{h['key']}={h['value']}\" for h in hits)\n"
            "    async def delete(self, entry_id):\n"
            "        return False\n"
        )

    def _write_context_plugin(self, name="fake_ctx", suffix="+FAKE"):
        plugin_dir = Path(self._tmp) / ".hermes" / "plugins" / name
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "context_engine.json").write_text(json.dumps({
            "name": name,
            "description": "test context engine",
            "module": "fake_ctx_module",
            "class": "FakeContextEngine",
            "init_kwargs": {"suffix": suffix},
        }))
        Path(self._tmp, "fake_ctx_module.py").write_text(
            "class FakeContextEngine:\n"
            "    def __init__(self, suffix='+FAKE'):\n"
            "        self.suffix = suffix\n"
            "    async def build_context(self, system_prompt, history, new_message):\n"
            "        return [\n"
            "            {'role': 'system', 'content': system_prompt + self.suffix},\n"
            "            *history,\n"
            "            {'role': 'user', 'content': new_message},\n"
            "        ]\n"
        )


class TestMemoryProviderRegistry(ProviderPluginsTestBase):
    def test_activation_persists_and_public_helper_reloads(self):
        self._write_memory_plugin(prefix="MEM")
        module = self._reload_module()

        ok, msg = module.memory_provider_registry.activate("fake_mem")
        self.assertTrue(ok, msg)
        self.assertEqual(module.memory_provider_registry.active_name, "fake_mem")
        self.assertEqual(module.get_active_memory_provider().prefix, "MEM")

        async def exercise(provider):
            entry_id = await provider.add("name", "Alice")
            self.assertEqual(entry_id, "MEM-1")
            results = await provider.search("Alice")
            self.assertEqual(len(results), 1)
            context = await provider.get_context_for_query("Alice")
            self.assertIn("MEM:name=Alice", context)

        asyncio.run(exercise(module.get_active_memory_provider()))

        saved = json.loads(Path(self._tmp, "data", "active_memory_provider.json").read_text())
        self.assertEqual(saved["name"], "fake_mem")
        listed = module.memory_provider_registry.list()
        self.assertTrue(any(item["name"] == "fake_mem" and item["active"] for item in listed))

        reloaded = self._reload_module()
        self.assertEqual(reloaded.memory_provider_registry.active_name, "fake_mem")
        self.assertEqual(reloaded.get_active_memory_provider().prefix, "MEM")

        ok, msg = reloaded.memory_provider_registry.deactivate()
        self.assertTrue(ok, msg)
        cleared = json.loads(Path(self._tmp, "data", "active_memory_provider.json").read_text())
        self.assertIsNone(cleared["name"])
        self.assertIsNone(reloaded.get_active_memory_provider())

    def test_activate_reports_instantiation_failure(self):
        plugin_dir = Path(self._tmp) / ".hermes" / "plugins" / "broken_mem"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "memory_provider.json").write_text(json.dumps({
            "name": "broken_mem",
            "description": "broken provider",
            "module": "broken_mem_module",
            "class": "MissingProvider",
        }))
        Path(self._tmp, "broken_mem_module.py").write_text("class PresentProvider:\n    pass\n")
        module = self._reload_module()

        ok, msg = module.memory_provider_registry.activate("broken_mem")
        self.assertFalse(ok)
        self.assertIn("Failed to instantiate", msg)


class TestContextEngineRegistry(ProviderPluginsTestBase):
    def test_activation_persists_and_public_helper_reloads(self):
        self._write_context_plugin(suffix="+CTX")
        module = self._reload_module()

        ok, msg = module.context_engine_registry.activate("fake_ctx")
        self.assertTrue(ok, msg)
        self.assertEqual(module.context_engine_registry.active_name, "fake_ctx")
        self.assertEqual(module.get_active_context_engine().suffix, "+CTX")

        async def exercise(engine):
            messages = await engine.build_context(
                "You are helpful",
                [{"role": "assistant", "content": "prior"}],
                "what is 2+2?",
            )
            self.assertEqual(len(messages), 3)
            self.assertEqual(messages[0]["content"], "You are helpful+CTX")
            self.assertEqual(messages[-1]["content"], "what is 2+2?")

        asyncio.run(exercise(module.get_active_context_engine()))

        saved = json.loads(Path(self._tmp, "data", "active_context_engine.json").read_text())
        self.assertEqual(saved["name"], "fake_ctx")
        listed = module.context_engine_registry.list()
        self.assertTrue(any(item["name"] == "fake_ctx" and item["active"] for item in listed))

        reloaded = self._reload_module()
        self.assertEqual(reloaded.context_engine_registry.active_name, "fake_ctx")
        self.assertEqual(reloaded.get_active_context_engine().suffix, "+CTX")

        ok, msg = reloaded.context_engine_registry.deactivate()
        self.assertTrue(ok, msg)
        cleared = json.loads(Path(self._tmp, "data", "active_context_engine.json").read_text())
        self.assertIsNone(cleared["name"])
        self.assertIsNone(reloaded.get_active_context_engine())


if __name__ == "__main__":
    unittest.main()
