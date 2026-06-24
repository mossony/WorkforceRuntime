from __future__ import annotations

from workforce_runtime.core import AgentProfile, TaskContract
from workforce_runtime.core.organization import Company, Organization
from workforce_runtime.dashboard.summaries import total_budget_usage, worker_performance
from workforce_runtime.storage import RuntimeStore


def render_text_dashboard(store: RuntimeStore) -> str:
    company = store.get_company() or Company(name="Unknown Workforce")
    agents = store.list_agents()
    tasks = store.list_tasks()
    reports = store.list_reports()
    artifacts = store.list_artifacts()
    events = store.list_events()
    budget = total_budget_usage(tasks, reports)
    token_budget_limit = company.token_budget or budget["max_tokens"]
    headcount_limit = str(company.headcount_limit) if company.headcount_limit else "unlimited"
    organization = Organization(company=company, agents=agents) if agents else None
    tasks_by_id = {task.task_id: task for task in tasks}
    active_tasks = [task for task in tasks if task.status in {"assigned", "in_progress", "blocked"}]
    completed_tasks = [task for task in tasks if task.status == "completed"]
    failed_tasks = [task for task in tasks if task.status == "failed"]
    decision_reports = [report for report in reports if report.requires_decision]
    decision_events = [
        event
        for event in events
        if event.event_type in {"budget_requested", "permission_requested", "permission_violation"}
    ]

    lines = [
        "Workforce Runtime",
        "=================",
        "",
        "Company:",
        f"  {company.name}",
    ]
    if company.mission:
        lines.extend(["", "Company Goal:", f"  {company.mission}"])

    lines.extend(
        [
            "",
            "Budget:",
            f"  Tokens used: {budget['tokens_used']:,} / {token_budget_limit:,}",
            f"  Headcount: {len(agents)} / {headcount_limit}",
            f"  Runtime: {budget['runtime_seconds_used']}s / {budget['max_runtime_seconds']}s",
            f"  Tool calls: {budget['tool_calls_used']:,} / {budget['max_tool_calls']:,}",
            "",
            "Organization:",
        ]
    )
    if organization is None:
        lines.append("  No agents.")
    else:
        for root in [agent for agent in agents if agent.manager_id is None]:
            lines.extend(_format_agent_tree(organization, root, tasks_by_id=tasks_by_id, depth=1))

    lines.extend(["", "Current Work:"])
    if not agents:
        lines.append("  No agents.")
    else:
        for agent in agents:
            work = _current_work(agent, tasks_by_id)
            lines.append(f"  {agent.name} ({agent.role})  {agent.status}  {work}")

    lines.extend(["", "Active Agents:"])
    _append_agent_list(lines, [agent for agent in agents if agent.status == "busy"])

    lines.extend(["", "Idle Agents:"])
    _append_agent_list(lines, [agent for agent in agents if agent.status == "idle"])

    lines.extend(["", "Blocked Agents:"])
    _append_agent_list(lines, [agent for agent in agents if agent.status == "blocked"])

    lines.extend(["", "Active Tasks:"])
    _append_task_list(lines, active_tasks, agents)

    lines.extend(["", "Completed Tasks:"])
    _append_task_list(lines, completed_tasks, agents)

    lines.extend(["", "Failed Tasks:"])
    _append_task_list(lines, failed_tasks, agents)

    lines.extend(["", "Recent Reports:"])
    if not reports:
        lines.append("  No reports.")
    else:
        for report in reports[-5:]:
            lines.append(
                f"  {report.report_id}  "
                f"{_agent_label(report.from_agent_id, agents)} -> {_agent_label(report.to_agent_id, agents)}"
            )
            lines.append(f"    {report.status}: {report.summary}")

    lines.extend(["", "Recent Artifacts:"])
    if not artifacts:
        lines.append("  No artifacts.")
    else:
        for artifact in artifacts[-5:]:
            lines.append(f"  {artifact.artifact_id}  {artifact.type}  {artifact.path}")

    lines.extend(["", "Decision Inbox:"])
    decisions: list[str] = []
    decisions.extend(
        f"{report.from_agent_id} requires decision on {report.task_id}: {report.next_action}"
        for report in decision_reports
    )
    for event in decision_events:
        if event.event_type == "permission_requested":
            permission = event.payload.get("permission") or event.payload.get("capability") or "permission"
            decisions.append(f"{event.actor_id} requested {permission}")
        elif event.event_type == "budget_requested":
            decisions.append(f"{event.actor_id} requested budget")
        else:
            decisions.append(f"{event.actor_id} triggered {event.event_type}")
    if not decisions:
        lines.append("  No pending decisions.")
    else:
        for index, decision in enumerate(decisions, start=1):
            lines.append(f"  {index}. {decision}")

    lines.extend(["", "Budget Overruns:"])
    budget_violations = [event for event in events if event.event_type == "budget_violation"]
    if not budget_violations:
        lines.append("  None.")
    else:
        for event in budget_violations[-5:]:
            reason = event.payload.get("reason", "budget violation")
            lines.append(f"  {event.task_id or '-'}  {event.actor_id}  {reason}")

    lines.extend(["", "Agent Runs:"])
    run_lines = _worker_run_lines(events)
    if not run_lines:
        lines.append("  No agent runs.")
    else:
        lines.extend(f"  {line}" for line in run_lines[-5:])

    lines.extend(["", "Live Agent Output:"])
    output_lines = _worker_output_lines(events)
    if not output_lines:
        lines.append("  No agent output.")
    else:
        lines.extend(f"  {line}" for line in output_lines[-8:])

    lines.extend(["", "Worker Performance:"])
    performance = worker_performance(agents, tasks, reports, artifacts)
    if not performance:
        lines.append("  No worker activity.")
    else:
        for item in performance:
            lines.append(f"  {item}")

    lines.extend(["", "Recent Events:"])
    if not events:
        lines.append("  No events.")
    else:
        for event in events[-10:]:
            target = f" {event.task_id}" if event.task_id else ""
            lines.append(f"  {event.event_type}{target} by {event.actor_id}")

    return "\n".join(lines)


def render_event_replay(store: RuntimeStore) -> str:
    events = store.list_events()
    lines = [
        "Event Replay",
        "============",
    ]
    if not events:
        lines.append("No events.")
        return "\n".join(lines)

    for index, event in enumerate(events, start=1):
        target = f" task={event.task_id}" if event.task_id else ""
        detail = _event_detail(event.payload)
        suffix = f" {detail}" if detail else ""
        lines.append(f"{index:02d}. {event.event_type}{target} actor={event.actor_id}{suffix}")
    return "\n".join(lines)


def render_agent_trajectories(store: RuntimeStore) -> str:
    agents = store.list_agents()
    tasks = store.list_tasks()
    reports = store.list_reports()
    events = store.list_events()

    lines = [
        "Agent Trajectories",
        "==================",
    ]
    if not agents:
        lines.append("No agents.")
        return "\n".join(lines)

    for agent in agents:
        model = f" model={agent.model}" if agent.model else ""
        lines.append(f"{agent.name} ({agent.role}){model}")

        agent_tasks = [task for task in tasks if task.assigned_to == agent.id]
        if agent_tasks:
            for task in agent_tasks:
                lines.append(f"  task {task.task_id}: {task.status} - {task.title}")
        else:
            lines.append("  tasks: none")

        sent_reports = [report for report in reports if report.from_agent_id == agent.id]
        received_reports = [report for report in reports if report.to_agent_id == agent.id]
        for report in sent_reports:
            lines.append(f"  reported {report.report_id} on {report.task_id}: {report.status}")
        for report in received_reports:
            lines.append(f"  received report {report.report_id} from {report.from_agent_id}: {report.status}")

        relevant_events = [
            event
            for event in events
            if event.actor_id == agent.id
            or event.payload.get("assigned_to") == agent.id
            or event.payload.get("to_agent_id") == agent.id
            or event.payload.get("target_agent_id") == agent.id
        ]
        if not relevant_events:
            lines.append("  events: none")
        else:
            for event in relevant_events[-8:]:
                target = f" {event.task_id}" if event.task_id else ""
                lines.append(f"  event {event.event_type}{target} by {event.actor_id}")
    return "\n".join(lines)


def _format_agent_tree(
    organization: Organization,
    agent: AgentProfile,
    *,
    tasks_by_id: dict[str, TaskContract],
    depth: int,
) -> list[str]:
    indent = "  " * depth
    work = _current_work(agent, tasks_by_id)
    model = f" [{agent.model}]" if agent.model else ""
    lines = [f"{indent}{agent.name:<28} {agent.role:<22} {agent.status:<9} {work}{model}"]
    for report in organization.get_direct_reports(agent.id):
        lines.extend(_format_agent_tree(organization, report, tasks_by_id=tasks_by_id, depth=depth + 1))
    return lines


def _current_work(agent: AgentProfile, tasks_by_id: dict[str, TaskContract]) -> str:
    if not agent.current_task_ids:
        return "-"
    labels: list[str] = []
    for task_id in agent.current_task_ids:
        task = tasks_by_id.get(task_id)
        labels.append(f"{task_id}:{task.title}" if task else task_id)
    return ", ".join(labels)


def _append_agent_list(lines: list[str], agents: list[AgentProfile]) -> None:
    if not agents:
        lines.append("  None.")
        return
    for agent in agents:
        tasks = ",".join(agent.current_task_ids) if agent.current_task_ids else "-"
        lines.append(f"  {agent.name}  {agent.status}  {tasks}")


def _append_task_list(lines: list[str], tasks: list[TaskContract], agents: list[AgentProfile]) -> None:
    if not tasks:
        lines.append("  None.")
        return
    for task in tasks:
        assignee = _agent_label(task.assigned_to, agents)
        lines.append(f"  {task.task_id}  {task.title}  {task.status}  {assignee}")


def _agent_label(agent_id: str | None, agents: list[AgentProfile]) -> str:
    if agent_id is None:
        return "unassigned"
    for agent in agents:
        if agent.id == agent_id:
            return agent.name
    return agent_id


def _event_detail(payload: dict[str, object]) -> str:
    keys = [
        "assigned_to",
        "to_agent_id",
        "target_agent_id",
        "status",
        "stream",
        "returncode",
        "timed_out",
        "report_id",
        "decision",
        "message",
        "text",
    ]
    parts = []
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value)
        if len(text) > 80:
            text = text[:77] + "..."
        parts.append(f"{key}={text}")
    return " ".join(parts)


def _worker_run_lines(events: list[object]) -> list[str]:
    runs: dict[str, dict[str, object]] = {}
    for event in events:
        if event.event_type not in {
            "worker_run_started",
            "worker_run_finished",
            "agent_run_started",
            "agent_run_finished",
        }:
            continue
        run_id = str(event.payload.get("run_id") or "")
        if not run_id:
            continue
        kind = "worker" if event.event_type.startswith("worker_") else "agent"
        state = runs.setdefault(
            run_id,
            {
                "task_id": event.task_id or "-",
                "actor_id": event.actor_id,
                "kind": kind,
                "status": "running",
                "runtime": event.payload.get("executable") or event.payload.get("adapter") or event.payload.get("model") or "",
            },
        )
        state["task_id"] = event.task_id or state["task_id"]
        state["actor_id"] = event.actor_id
        state["kind"] = kind
        if event.event_type in {"worker_run_started", "agent_run_started"}:
            state["status"] = "running"
            state["runtime"] = (
                event.payload.get("executable")
                or event.payload.get("adapter")
                or event.payload.get("model")
                or state.get("runtime")
                or ""
            )
        elif event.event_type == "worker_run_finished":
            timed_out = bool(event.payload.get("timed_out"))
            returncode = event.payload.get("returncode")
            state["status"] = "timed_out" if timed_out else f"exited({returncode})"
        else:
            state["status"] = str(event.payload.get("status") or "finished")

    return [
        f"{run_id}  {state['task_id']}  {state['actor_id']}  {state['kind']}  {state['status']}  {state['runtime']}"
        for run_id, state in runs.items()
    ]


def _worker_output_lines(events: list[object]) -> list[str]:
    lines: list[str] = []
    for event in events:
        if event.event_type not in {"worker_output", "agent_output"}:
            continue
        stream = event.payload.get("stream", "output")
        text = str(event.payload.get("text", "")).strip()
        if not text:
            continue
        text = " ".join(text.split())
        if len(text) > 140:
            text = text[:137] + "..."
        lines.append(f"{event.task_id or '-'}  {event.actor_id}  {stream}: {text}")
    return lines
