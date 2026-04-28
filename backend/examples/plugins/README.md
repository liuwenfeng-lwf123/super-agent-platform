# Example Provider Plugins

These are **shipped demos** that make the `/hermes` → `记忆/上下文` tab
non-empty out of the box. They auto-discover on startup alongside your
`~/.hermes/plugins/` and `./.hermes/plugins/` entries.

## Included

| Plugin | Kind | Class | What it does |
|---|---|---|---|
| `keyword_memory/` | MemoryProvider | `KeywordMemoryProvider` | In-memory keyword-indexed recall (lost on restart; template only) |
| `lastn_context/` | ContextEngine | `LastNContextEngine` | Keeps only the last N message pairs + system prompt (latency demo) |

## How to write your own

1. Create a directory `~/.hermes/plugins/<your_name>/` (or `./.hermes/plugins/` for project-scoped).
2. Drop one or both of:
   - `memory_provider.json` — registers a `MemoryProvider`
   - `context_engine.json` — registers a `ContextEngine`
3. Provide a Python module whose class implements the Protocol in
   `backend/app/agents/provider_plugins.py`.

### `memory_provider.json` schema

```json
{
  "name": "my_provider",
  "description": "Short human-readable blurb",
  "module": "importable.dotted.path",
  "class": "MyProviderClass",
  "init_kwargs": { "optional": "kwargs" }
}
```

### `context_engine.json` schema

```json
{
  "name": "my_engine",
  "description": "...",
  "module": "importable.dotted.path",
  "class": "MyEngineClass",
  "init_kwargs": { }
}
```

### Required methods

`MemoryProvider`:
- `async add(key, value, metadata=None) -> str` (entry id)
- `async search(query, limit=10) -> list[dict]`
- `async get_context_for_query(query, max_entries=5) -> str`
- `async delete(entry_id) -> bool`

`ContextEngine`:
- `async build_context(system_prompt, history, new_message) -> list[dict]`
  (return final messages, each `{"role": ..., "content": ...}`)

## Activating

In the `/hermes` → `记忆/上下文` tab click **激活** next to the plugin
name. Activation is persisted in `backend/data/active_memory_provider.json`
(resp. `active_context_engine.json`) and picked up by `super_agent`'s
main loop the next time a request is handled.
