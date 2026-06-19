from __future__ import annotations

from collections import defaultdict

from workforce_runtime.core import AgentProfile, Artifact, ReportContract, TaskContract


def total_budget_usage(tasks: list[TaskContract], reports: list[ReportContract]) -> dict[str, int]:
    max_tokens = sum(task.budget.max_tokens for task in tasks)
    max_runtime_seconds = sum(task.budget.max_runtime_seconds for task in tasks)
    max_tool_calls = sum(task.budget.max_tool_calls for task in tasks)
    task_tokens = sum(task.budget.tokens_used for task in tasks)
    task_runtime = sum(task.budget.runtime_seconds_used for task in tasks)
    task_tool_calls = sum(task.budget.tool_calls_used for task in tasks)
    report_tokens = sum(report.cost.tokens_used for report in reports)
    report_runtime = sum(report.cost.runtime_seconds for report in reports)
    report_tool_calls = sum(report.cost.tool_calls for report in reports)
    return {
        "max_tokens": max_tokens,
        "max_runtime_seconds": max_runtime_seconds,
        "max_tool_calls": max_tool_calls,
        "tokens_used": task_tokens + report_tokens,
        "runtime_seconds_used": task_runtime + report_runtime,
        "tool_calls_used": task_tool_calls + report_tool_calls,
    }


def worker_performance(
    agents: list[AgentProfile],
    tasks: list[TaskContract],
    reports: list[ReportContract],
    artifacts: list[Artifact],
) -> list[str]:
    tasks_by_agent: dict[str, int] = defaultdict(int)
    completed_by_agent: dict[str, int] = defaultdict(int)
    reports_by_agent: dict[str, int] = defaultdict(int)
    artifacts_by_agent: dict[str, int] = defaultdict(int)

    for task in tasks:
        if not task.assigned_to:
            continue
        tasks_by_agent[task.assigned_to] += 1
        if task.status == "completed":
            completed_by_agent[task.assigned_to] += 1

    for report in reports:
        reports_by_agent[report.from_agent_id] += 1

    for artifact in artifacts:
        artifacts_by_agent[artifact.agent_id] += 1

    lines: list[str] = []
    for agent in agents:
        total = tasks_by_agent[agent.id]
        if total == 0 and reports_by_agent[agent.id] == 0 and artifacts_by_agent[agent.id] == 0:
            continue
        lines.append(
            f"{agent.name}: tasks={total}, completed={completed_by_agent[agent.id]}, "
            f"reports={reports_by_agent[agent.id]}, artifacts={artifacts_by_agent[agent.id]}"
        )
    return lines
