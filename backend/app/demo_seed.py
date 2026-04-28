from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
import uuid

from app.agents.store import thread_store
from app.memory.store import memory_store
from app.rag.store import knowledge_base


DEMO_PREFIX = "[Demo]"
DEMO_CATEGORY = "demo"
DEMO_TITLE = f"{DEMO_PREFIX} Agent Platform Walkthrough"

DEMO_MEMORY = [
    {
        "key": "demo_platform_positioning",
        "value": "This project is an Agent platform with chat, subagents, tools, runtime routing, memory, RAG, evaluation, and Hermes-style skills.",
        "category": DEMO_CATEGORY,
    },
    {
        "key": "demo_operator_goal",
        "value": "When presenting the platform, show chat streaming, tool validation, memory recall, knowledge search, and task evaluation before adding new features.",
        "category": DEMO_CATEGORY,
    },
]

DEMO_KNOWLEDGE = [
    {
        "name": f"{DEMO_PREFIX} Platform Tour",
        "content": """Super Agent Platform demo tour:\n1. Open the chat page and ask about the project architecture.\n2. Open Hermes to inspect skills, subagents, plugins, cron, and evolution.\n3. Open Memory to see seeded demo memories.\n4. Use the runtime and tool panels to inspect validation results.\n5. Run task evaluation to verify agent behavior with reproducible cases.""",
    },
    {
        "name": f"{DEMO_PREFIX} Production Checklist",
        "content": """Production readiness checklist:\n- Run scripts/quality_gate.py before merging changes.\n- Start services and run scripts/smoke_check.py before demos.\n- Enable REAL_LLM_E2E, RUN_DOCKER_LIVE, and RUN_SSH_LIVE only when real credentials and infrastructure are available.\n- Keep dangerous tools behind permission approval.\n- Use /health and /ready for deployment probes.""",
    },
]


@dataclass
class DemoSeedAction:
    action: str
    ok: bool
    target: str
    message: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _action(action: str, ok: bool, target: str, message: str) -> DemoSeedAction:
    return DemoSeedAction(action=action, ok=ok, target=target, message=message)


def _serialize(results: list[DemoSeedAction]) -> list[dict[str, Any]]:
    return [asdict(result) for result in results]


def build_demo_trajectory() -> dict[str, Any]:
    thread_id = "demo-source-" + uuid.uuid4().hex[:8]
    messages = [
        {
            "role": "user",
            "content": "请用人话介绍这个 Agent 平台现在能做什么。",
            "metadata": {"demo": True},
            "created_at": _now(),
        },
        {
            "role": "assistant",
            "content": "它不是单一聊天页，而是一套 Agent 平台：有主聊天、多 Agent、工具调用、权限安全、记忆、知识库、Runtime、评测、Hermes 技能系统和生产验收脚本。",
            "metadata": {"demo": True},
            "created_at": _now(),
        },
        {
            "role": "user",
            "content": "上线前我应该先看什么？",
            "metadata": {"demo": True},
            "created_at": _now(),
        },
        {
            "role": "assistant",
            "content": "先跑 scripts/quality_gate.py，再启动服务跑 scripts/smoke_check.py。真实生产前再打开 REAL_LLM_E2E、RUN_DOCKER_LIVE、RUN_SSH_LIVE 这些外部依赖测试。",
            "metadata": {"demo": True},
            "created_at": _now(),
        },
    ]
    return {
        "format": "sap.trajectory.v1",
        "exported_at": _now(),
        "message_count": len(messages),
        "thread": {
            "id": thread_id,
            "title": DEMO_TITLE,
            "messages": messages,
            "metadata": {"demo_seed": True},
            "created_at": _now(),
            "updated_at": _now(),
            "parent_id": None,
            "compact_summary": "Demo walkthrough for platform capabilities and production readiness.",
        },
        "lineage": [],
        "children": [],
    }


def planned_actions(clean: bool = True) -> list[DemoSeedAction]:
    results: list[DemoSeedAction] = []
    if clean:
        results.extend([
            _action("delete_memory", True, "demo memory", "planned"),
            _action("delete_knowledge", True, "demo knowledge", "planned"),
            _action("delete_thread", True, "demo thread", "planned"),
        ])
    results.extend(_action("add_memory", True, entry["key"], "planned") for entry in DEMO_MEMORY)
    results.extend(_action("add_knowledge", True, doc["name"], "planned") for doc in DEMO_KNOWLEDGE)
    results.append(_action("add_thread", True, DEMO_TITLE, "planned"))
    return results


async def clean_demo_data() -> list[DemoSeedAction]:
    results: list[DemoSeedAction] = []
    entries = await memory_store.get_all()
    for entry in entries:
        if entry.category == DEMO_CATEGORY or entry.key.startswith("demo_"):
            deleted = await memory_store.delete(entry.id)
            results.append(_action("delete_memory", deleted, entry.id, "deleted" if deleted else "not found"))

    for doc in knowledge_base.list_documents():
        if str(doc.get("name", "")).startswith(DEMO_PREFIX):
            doc_id = str(doc.get("doc_id", ""))
            deleted = knowledge_base.remove_document(doc_id)
            results.append(_action("delete_knowledge", deleted, doc_id, "deleted" if deleted else "not found"))

    threads = await thread_store.list_threads()
    for thread in threads:
        if thread.title.startswith(DEMO_PREFIX) or thread.metadata.get("demo_seed") is True:
            deleted = await thread_store.delete(thread.id)
            results.append(_action("delete_thread", deleted, thread.id, "deleted" if deleted else "not found"))
    return results


async def seed_demo_data(clean: bool = True) -> list[DemoSeedAction]:
    results: list[DemoSeedAction] = []
    if clean:
        results.extend(await clean_demo_data())

    for entry in DEMO_MEMORY:
        created = await memory_store.add(entry["key"], entry["value"], entry["category"])
        results.append(_action("add_memory", True, entry["key"], created.id))

    for doc in DEMO_KNOWLEDGE:
        doc_id = knowledge_base.add_document(doc["name"], doc["content"], {"demo_seed": True})
        results.append(_action("add_knowledge", True, doc["name"], doc_id))

    thread = await thread_store.import_thread(build_demo_trajectory(), title=DEMO_TITLE)
    if thread:
        results.append(_action("add_thread", True, thread.id, "created"))
    else:
        results.append(_action("add_thread", False, DEMO_TITLE, "import failed"))
    return results


async def run_demo_seed(clean: bool = True, dry_run: bool = False) -> dict[str, Any]:
    results = planned_actions(clean) if dry_run else await seed_demo_data(clean)
    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "dry_run": dry_run,
        "clean": clean,
        "summary": {
            "total": len(results),
            "passed": len(results) - len(failed),
            "failed": len(failed),
        },
        "results": _serialize(results),
    }
