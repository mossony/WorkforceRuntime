from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from workforce_runtime.core import TaskTraceExport

if TYPE_CHECKING:
    from workforce_runtime.server.runtime import WorkforceRuntime


def write_trace_file(
    runtime: WorkforceRuntime,
    *,
    workspace: str | Path,
    run_id: str,
    label: str,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    trace_dir = Path(workspace) / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"{_safe_name(run_id)}.jsonl"
    with trace_path.open("w") as handle:
        for event in runtime.store.list_events():
            handle.write(json.dumps(event.model_dump(mode="json")) + "\n")
    payload = {"run_id": run_id, "label": label, "trace_path": str(trace_path)}
    if metadata:
        payload.update(metadata)
    runtime.record_event(
        event_type="trace_file_written",
        actor_id="system",
        task_id=task_id,
        payload=payload,
    )
    return trace_path


def export_task_trace(
    runtime: WorkforceRuntime,
    *,
    task_id: str,
    workspace: str | Path,
    trace_id: str | None = None,
    include_descendants: bool = True,
    include_file_contents: bool = True,
    max_file_bytes: int = 500_000,
) -> TaskTraceExport:
    trace_id = trace_id or f"tasktrace_{_safe_name(task_id)}"
    trace_dir = Path(workspace)
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"{_safe_name(task_id)}.trace.json"
    payload = build_task_trace_payload(
        runtime,
        task_id=task_id,
        trace_id=trace_id,
        trace_path=trace_path,
        include_descendants=include_descendants,
        include_file_contents=include_file_contents,
        max_file_bytes=max_file_bytes,
    )
    trace = TaskTraceExport(
        trace_id=trace_id,
        task_id=task_id,
        path=str(trace_path),
        payload=payload,
    )
    trace_path.write_text(json.dumps(trace.model_dump(mode="json"), indent=2))
    runtime.store.save_task_trace_export(trace)
    return trace


def build_task_trace_payload(
    runtime: WorkforceRuntime,
    *,
    task_id: str,
    trace_id: str,
    trace_path: str | Path,
    include_descendants: bool = True,
    include_file_contents: bool = True,
    max_file_bytes: int = 500_000,
) -> dict[str, Any]:
    task = runtime.require_task(task_id)
    task_ids = _task_scope(runtime, task_id, include_descendants=include_descendants)
    tasks = [runtime.store.get_task(item) for item in task_ids]
    tasks = [item for item in tasks if item is not None]
    reports = [report for report in runtime.store.list_reports() if report.task_id in task_ids]
    artifacts = [artifact for artifact in runtime.store.list_artifacts() if artifact.task_id in task_ids]
    documents = [document for document in runtime.store.list_task_documents() if document.task_id in task_ids]
    sequenced_events = [
        item
        for item in runtime.store.list_events_after(0, limit=1_000_000)
        if _event_references_task(item.event, task_ids)
    ]
    involved_agent_ids = _involved_agent_ids(tasks, reports, artifacts, sequenced_events)
    agents = [
        agent
        for agent in runtime.store.list_agents()
        if agent.id in involved_agent_ids or not involved_agent_ids
    ]
    file_paths = _known_task_file_paths(artifacts, reports, sequenced_events)
    file_entries = [
        _file_entry(path, include_contents=include_file_contents, max_file_bytes=max_file_bytes)
        for path in sorted(file_paths)
    ]
    root_task_id = task.root_goal_id or task.task_id
    return {
        "schema_version": 1,
        "trace_id": trace_id,
        "task_id": task_id,
        "root_task_id": root_task_id,
        "trace_path": str(trace_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "include_descendants": include_descendants,
            "task_ids": task_ids,
        },
        "summary": {
            "task_count": len(tasks),
            "event_count": len(sequenced_events),
            "report_count": len(reports),
            "artifact_count": len(artifacts),
            "document_count": len(documents),
            "agent_count": len(agents),
            "file_count": len(file_entries),
        },
        "company": runtime.store.get_company().model_dump(mode="json") if runtime.store.get_company() else None,
        "tasks": [item.model_dump(mode="json") for item in tasks],
        "agents": [agent.model_dump(mode="json") for agent in agents],
        "documents": [document.model_dump(mode="json") for document in documents],
        "reports": [report.model_dump(mode="json") for report in reports],
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "events": [
            {
                "sequence": item.sequence,
                **item.event.model_dump(mode="json"),
            }
            for item in sequenced_events
        ],
        "agent_runs": _agent_runs(sequenced_events),
        "files": file_entries,
    }


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in value)
    return safe.strip("._") or "trace"


def _task_scope(runtime: WorkforceRuntime, task_id: str, *, include_descendants: bool) -> list[str]:
    runtime.require_task(task_id)
    task_ids = [task_id]
    if not include_descendants:
        return task_ids
    seen = {task_id}
    changed = True
    while changed:
        changed = False
        for task in runtime.store.list_tasks():
            if task.task_id in seen:
                continue
            if task.parent_task_id in seen or task.root_goal_id == task_id:
                seen.add(task.task_id)
                task_ids.append(task.task_id)
                changed = True
    return task_ids


def _event_references_task(event: Any, task_ids: list[str]) -> bool:
    task_id_set = set(task_ids)
    if event.task_id in task_id_set:
        return True
    for key in ("task_id", "parent_task_id", "root_goal_id", "root_task_id", "final_task_id"):
        value = event.payload.get(key)
        if isinstance(value, str) and value in task_id_set:
            return True
    for key in ("task_ids", "current_task_ids"):
        value = event.payload.get(key)
        if isinstance(value, list) and task_id_set.intersection(str(item) for item in value):
            return True
    return False


def _involved_agent_ids(tasks: list[Any], reports: list[Any], artifacts: list[Any], events: list[Any]) -> set[str]:
    ids: set[str] = set()
    for task in tasks:
        for value in (task.assigned_to, task.assigned_by):
            if value:
                ids.add(str(value))
    for report in reports:
        ids.add(report.from_agent_id)
        ids.add(report.to_agent_id)
    for artifact in artifacts:
        ids.add(artifact.agent_id)
    for item in events:
        event = item.event
        ids.add(event.actor_id)
        for key in ("agent_id", "from_agent_id", "to_agent_id", "target_agent_id", "assigned_to", "worker_id"):
            value = event.payload.get(key)
            if value:
                ids.add(str(value))
    return {item for item in ids if item not in {"human", "system", "runtime"}}


def _known_task_file_paths(artifacts: list[Any], reports: list[Any], events: list[Any]) -> set[str]:
    paths: set[str] = {artifact.path for artifact in artifacts}
    for report in reports:
        for evidence in report.evidence:
            if isinstance(evidence, dict) and evidence.get("path"):
                paths.add(str(evidence["path"]))
    for item in events:
        event = item.event
        for key in (
            "trace_path",
            "stdout_path",
            "stderr_path",
            "prompt_path",
            "response_path",
            "raw_response_path",
            "error_path",
            "last_attempt_error_path",
            "path",
        ):
            value = event.payload.get(key)
            if value:
                paths.add(str(value))
        if event.event_type == "agent_run_path_registered" and event.payload.get("run_dir"):
            run_dir = Path(str(event.payload["run_dir"]))
            if run_dir.exists():
                for path in run_dir.iterdir():
                    if path.is_file():
                        paths.add(str(path))
    return paths


def _file_entry(path_text: str, *, include_contents: bool, max_file_bytes: int) -> dict[str, Any]:
    path = Path(path_text)
    entry: dict[str, Any] = {
        "path": path_text,
        "exists": path.exists(),
        "is_file": path.is_file(),
    }
    if not path.exists() or not path.is_file():
        return entry
    size = path.stat().st_size
    entry["size_bytes"] = size
    if not include_contents:
        return entry
    data = path.read_bytes()[: max(max_file_bytes, 0)]
    entry["content_truncated"] = size > len(data)
    try:
        entry["content"] = data.decode("utf-8")
        entry["encoding"] = "utf-8"
    except UnicodeDecodeError:
        entry["content_base64"] = __import__("base64").b64encode(data).decode("ascii")
        entry["encoding"] = "base64"
    return entry


def _agent_runs(events: list[Any]) -> list[dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for item in events:
        event = item.event
        run_id = event.payload.get("run_id")
        if not run_id:
            continue
        run = runs.setdefault(
            str(run_id),
            {
                "run_id": str(run_id),
                "task_id": event.task_id,
                "actor_id": event.actor_id,
                "outputs": [],
                "events": [],
            },
        )
        run["events"].append({"sequence": item.sequence, **event.model_dump(mode="json")})
        if event.event_type in {"agent_run_started", "worker_run_started"}:
            run["status"] = "running"
            run["adapter"] = event.payload.get("adapter") or event.payload.get("executable") or ""
            run["model"] = event.payload.get("model") or ""
        elif event.event_type in {"agent_output", "worker_output"}:
            run["outputs"].append(
                {
                    "sequence": item.sequence,
                    "stream": event.payload.get("stream"),
                    "text": event.payload.get("text"),
                }
            )
        elif event.event_type in {"agent_run_finished", "worker_run_finished"}:
            run["status"] = event.payload.get("status") or "finished"
            run["usage"] = event.payload.get("usage") or {}
            run["returncode"] = event.payload.get("returncode")
            run["timed_out"] = event.payload.get("timed_out")
            run["error"] = event.payload.get("error") or ""
        elif event.event_type == "agent_run_path_registered":
            for key in (
                "run_dir",
                "stdout_path",
                "stderr_path",
                "prompt_path",
                "response_path",
                "raw_response_path",
                "error_path",
                "last_attempt_error_path",
            ):
                if event.payload.get(key):
                    run[key] = event.payload.get(key)
    return list(runs.values())
