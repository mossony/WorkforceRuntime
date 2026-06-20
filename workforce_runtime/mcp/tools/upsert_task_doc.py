from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def upsert_task_doc(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    document = runtime.upsert_task_document(
        actor_id=str(arguments.get("actor_id") or arguments.get("agent_id") or arguments.get("from_agent_id") or "runtime"),
        task_id=str(arguments["task_id"]),
        doc_id=str(arguments["doc_id"]) if arguments.get("doc_id") else None,
        title=str(arguments["title"]),
        doc_type=str(arguments.get("doc_type") or "note"),
        content=str(arguments["content"]),
    )
    return {"ok": True, "document": document.model_dump(mode="json")}
