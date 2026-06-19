from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def assign(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    caller_id = str(arguments.get("from_agent_id") or arguments.get("agent_id") or arguments["caller_id"])
    assignee_id = str(arguments.get("to_agent_id") or arguments.get("assignee_id") or arguments["worker_id"])
    task_id = arguments.get("task_id") or arguments.get("id")

    if task_id:
        task = runtime.assign_task(str(task_id), assign_to=assignee_id, assigned_by=caller_id)
    else:
        message = str(arguments.get("message") or arguments.get("objective") or "")
        if not message:
            raise ValueError("assign requires message or objective when creating a task")
        title = str(arguments.get("title") or _title_from_message(message))
        task = runtime.create_task(
            title=title,
            objective=message,
            assign_to=assignee_id,
            assigned_by=caller_id,
            parent_task_id=str(arguments["parent_task_id"]) if arguments.get("parent_task_id") else None,
            root_goal_id=str(arguments["root_goal_id"]) if arguments.get("root_goal_id") else None,
            context_refs=[str(item) for item in arguments.get("context_refs") or []],
            constraints=[str(item) for item in arguments.get("constraints") or []],
            acceptance_criteria=[str(item) for item in arguments.get("acceptance_criteria") or []],
            required_artifacts=[str(item) for item in arguments.get("required_artifacts") or []],
        )

    return {
        "ok": True,
        "task_id": task.task_id,
        "assigned_to": task.assigned_to,
        "assigned_by": task.assigned_by,
        "status": task.status,
    }


def _title_from_message(message: str) -> str:
    first_line = next((line.strip() for line in message.splitlines() if line.strip()), "Assigned task")
    return first_line[:80]
