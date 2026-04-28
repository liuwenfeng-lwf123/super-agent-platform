"""
User-defined local mode shortcuts.
Each shortcut is a named sequence of steps the AI should execute.
Stored in data/local_shortcuts.json.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SHORTCUTS_PATH = Path("data/local_shortcuts.json")

_shortcuts: dict[str, dict] = {}


def _load():
    global _shortcuts
    if SHORTCUTS_PATH.exists():
        try:
            _shortcuts = json.loads(SHORTCUTS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Suppressed error in shortcuts: %s", e)
            _shortcuts = {}


def _save():
    SHORTCUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHORTCUTS_PATH.write_text(json.dumps(_shortcuts, ensure_ascii=False, indent=2), encoding="utf-8")


_load()


def list_shortcuts() -> list[dict]:
    return [{"name": k, **v} for k, v in _shortcuts.items()]


def get_shortcut(name: str) -> dict | None:
    return _shortcuts.get(name)


def save_shortcut(name: str, description: str, steps: list[str]):
    _shortcuts[name] = {"description": description, "steps": steps}
    _save()


def delete_shortcut(name: str) -> bool:
    if name in _shortcuts:
        del _shortcuts[name]
        _save()
        return True
    return False


def match_shortcut(message: str) -> dict | None:
    """Check if message matches a shortcut trigger name."""
    text = message.strip().lower()
    for name, data in _shortcuts.items():
        if text == name.lower() or text == f"/{name.lower()}":
            return {"name": name, **data}
    return None
