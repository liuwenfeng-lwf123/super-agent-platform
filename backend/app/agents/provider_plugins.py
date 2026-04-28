"""Memory Providers & Context Engines — pluggable single-selection (Hermes pattern).

Two specialized plugin kinds, each with at most ONE active implementation:

  * Memory Provider  — backs `get_context_for_query()` / `add()` / `search()` in
    place of the default cosine-similarity store. Alternate: Honcho, mem0, Zep, ...
  * Context Engine   — builds the final message list sent to the LLM (compaction,
    selection, ordering). Alternate: priority-window, graph-RAG, ...

A provider is discovered from three sources (Hermes pattern):
  1. `~/.hermes/plugins/<name>/`  — per-user overrides
  2. `.hermes/plugins/<name>/`    — project-local
  3. pip entry_points (`hermes.memory_providers` / `hermes.context_engines`)

Declare via `memory_provider.json` (or `context_engine.json`) with at minimum:

    {
      "name": "honcho_memory",
      "description": "Honcho dialectic user modeling",
      "module": "my_package.memory_impl",
      "class": "HonchoMemoryProvider"
    }

The named class must implement the respective Protocol below.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

ACTIVE_PROVIDER_FILE = Path("data") / "active_memory_provider.json"
ACTIVE_ENGINE_FILE = Path("data") / "active_context_engine.json"


def _get_plugin_search_paths() -> list[Path]:
    """Resolved at call time so cwd changes (tests, chdir) take effect.

    Order (first-win on name collisions):
      1. ~/.hermes/plugins           — per-user overrides
      2. <cwd>/.hermes/plugins       — project-local overrides
      3. backend/examples/plugins    — shipped demos (always available)
    """
    paths = [
        Path.home() / ".hermes" / "plugins",
        Path.cwd() / ".hermes" / "plugins",
    ]
    try:
        # backend/examples/plugins is next to this file's grandparent ("app/")
        bundled = Path(__file__).resolve().parent.parent.parent / "examples" / "plugins"
        if bundled.is_dir():
            paths.append(bundled)
    except Exception as e:
        logger.debug("Suppressed error in provider_plugins: %s", e)
    return paths


# ---------------------------------------------------------------------------
# Interfaces (duck-typed Protocols — plugins do NOT need to inherit from these)
# ---------------------------------------------------------------------------
@runtime_checkable
class MemoryProvider(Protocol):
    """Minimal interface a memory provider must implement."""

    async def add(self, key: str, value: str, metadata: dict | None = None) -> str: ...
    async def search(self, query: str, limit: int = 10) -> list[dict]: ...
    async def get_context_for_query(self, query: str, max_entries: int = 5) -> str: ...
    async def delete(self, entry_id: str) -> bool: ...


@runtime_checkable
class ContextEngine(Protocol):
    """Minimal interface a context engine must implement."""

    async def build_context(
        self,
        system_prompt: str,
        history: list[dict],
        new_message: str,
    ) -> list[dict]:
        """Return the final list of messages to send to the LLM."""
        ...


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _discover(filename: str) -> list[dict]:
    """Find plugin metadata files named `filename` in all search paths."""
    found: list[dict] = []
    seen_names: set[str] = set()
    for root in _get_plugin_search_paths():
        if not root.is_dir():
            continue
        for plugin_dir in sorted(root.iterdir()):
            if not plugin_dir.is_dir():
                continue
            meta_path = plugin_dir / filename
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Invalid %s in %s: %s", filename, plugin_dir, e)
                continue
            name = meta.get("name") or plugin_dir.name
            if name in seen_names:
                continue
            seen_names.add(name)
            meta["name"] = name
            meta["_plugin_dir"] = str(plugin_dir)
            found.append(meta)
    return found


def _discover_entry_points(group: str) -> list[dict]:
    """Discover plugins registered via pip entry_points (e.g. `pip install hermes-memory-xyz`).

    Entry point group names:
      - hermes.memory_providers
      - hermes.context_engines

    Each entry point should resolve to a class (the provider/engine implementation).
    """
    found: list[dict] = []
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        # Python 3.12+ returns a SelectableGroups; 3.9-3.11 returns dict
        group_eps = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])
        for ep in group_eps:
            found.append({
                "name": ep.name,
                "description": f"pip entry_point: {ep.value}",
                "module": ep.value.rsplit(":", 1)[0] if ":" in ep.value else ep.value,
                "class": ep.value.rsplit(":", 1)[1] if ":" in ep.value else "",
                "_source": "entry_point",
            })
    except Exception as e:
        logger.debug("entry_points discovery for %s failed: %s", group, e)
    return found


def discover_memory_providers() -> list[dict]:
    return _discover("memory_provider.json") + _discover_entry_points("hermes.memory_providers")


def discover_context_engines() -> list[dict]:
    return _discover("context_engine.json") + _discover_entry_points("hermes.context_engines")


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------
def _instantiate(meta: dict) -> Any:
    """Import and instantiate the class described by the metadata."""
    module_name = meta.get("module")
    class_name = meta.get("class")
    if not module_name or not class_name:
        raise RuntimeError(f"Plugin {meta.get('name')} missing module/class")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    kwargs = meta.get("init_kwargs", {}) or {}
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# Registry with single-select semantics
# ---------------------------------------------------------------------------
class _SingleSelectRegistry:
    """Manages one active plugin of a given kind, persisted by name."""

    def __init__(
        self,
        kind: str,
        discover_fn,
        active_file: Path,
        interface: type,
    ):
        self.kind = kind
        self.discover_fn = discover_fn
        self.active_file = active_file
        self.interface = interface
        self._active_name: str | None = None
        self._active_instance: Any = None
        self._load_active()

    def _load_active(self):
        if not self.active_file.is_file():
            return
        try:
            data = json.loads(self.active_file.read_text(encoding="utf-8"))
            name = data.get("name")
            if name:
                self.activate(name, persist=False)
        except Exception as e:
            logger.warning("Failed to load active %s: %s", self.kind, e)

    def _save_active(self):
        self.active_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"name": self._active_name} if self._active_name else {"name": None}
        self.active_file.write_text(
            json.dumps(payload, indent=2), encoding="utf-8",
        )

    def list(self) -> list[dict]:
        items = []
        for meta in self.discover_fn():
            items.append({
                "name": meta["name"],
                "description": meta.get("description", ""),
                "module": meta.get("module"),
                "class": meta.get("class"),
                "active": meta["name"] == self._active_name,
                "plugin_dir": meta.get("_plugin_dir"),
            })
        return items

    def activate(self, name: str, persist: bool = True) -> tuple[bool, str]:
        for meta in self.discover_fn():
            if meta["name"] != name:
                continue
            try:
                instance = _instantiate(meta)
            except Exception as e:
                return False, f"Failed to instantiate '{name}': {e}"
            if not isinstance(instance, self.interface):
                logger.warning(
                    "Plugin '%s' does not fully implement %s — activating anyway",
                    name, self.interface.__name__,
                )
            self._active_instance = instance
            self._active_name = name
            if persist:
                self._save_active()
            return True, f"Activated {self.kind}: {name}"
        return False, f"{self.kind} '{name}' not found"

    def deactivate(self) -> tuple[bool, str]:
        self._active_instance = None
        self._active_name = None
        self._save_active()
        return True, f"Deactivated {self.kind}"

    @property
    def active_name(self) -> str | None:
        return self._active_name

    @property
    def active(self) -> Any:
        return self._active_instance


memory_provider_registry = _SingleSelectRegistry(
    kind="memory_provider",
    discover_fn=discover_memory_providers,
    active_file=ACTIVE_PROVIDER_FILE,
    interface=MemoryProvider,
)

context_engine_registry = _SingleSelectRegistry(
    kind="context_engine",
    discover_fn=discover_context_engines,
    active_file=ACTIVE_ENGINE_FILE,
    interface=ContextEngine,
)


# ---------------------------------------------------------------------------
# Public helpers used by super_agent / memory_store callsites
# ---------------------------------------------------------------------------
def get_active_memory_provider() -> Any:
    """Return the active memory provider, or None if the default store should be used."""
    return memory_provider_registry.active


def get_active_context_engine() -> Any:
    return context_engine_registry.active
