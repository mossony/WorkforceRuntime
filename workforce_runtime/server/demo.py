from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from workforce_runtime.core import ReportContract, UsageCost
from workforce_runtime.dashboard import render_agent_trajectories, render_event_replay, render_text_dashboard
from workforce_runtime.mcp.server import MCPServer
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import GenericCLIWorker, RuntimeContext


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")
SIMPLE_STATUS_ORG = Path("examples/simple_status_org/org.yaml")
FIX_PARSER_WORKER = Path("examples/mock_worker/fix_parser_worker.py").resolve()
SIMPLE_NOTE_WORKER = Path("examples/mock_worker/simple_note_worker.py").resolve()
WEB_RESEARCH_WORKER = Path("examples/mock_worker/web_research_worker.py").resolve()


def _mcp_tool_call(
    server: MCPServer,
    request_id: int,
    name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    if response is None:
        raise RuntimeError("MCP server returned no response")
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]["structuredContent"]  # type: ignore[return-value]


def _management_run_start(runtime: WorkforceRuntime, agent_id: str, task_id: str, message: str) -> str:
    agent = runtime.get_agent(agent_id)
    run_id = f"run_{agent_id}_{uuid4().hex[:8]}"
    runtime.record_agent_run_started(
        run_id=run_id,
        task_id=task_id,
        actor_id=agent_id,
        adapter="management-demo",
        model=agent.model if agent else "",
    )
    runtime.record_agent_output(
        run_id=run_id,
        task_id=task_id,
        actor_id=agent_id,
        stream="assistant",
        text=message,
    )
    return run_id


def _management_run_finish(runtime: WorkforceRuntime, agent_id: str, task_id: str, run_id: str, message: str) -> None:
    runtime.record_agent_output(
        run_id=run_id,
        task_id=task_id,
        actor_id=agent_id,
        stream="assistant",
        text=message,
    )
    runtime.record_agent_run_finished(
        run_id=run_id,
        task_id=task_id,
        actor_id=agent_id,
        status="completed",
        usage={"tool_calls": 1},
    )


def run_sample_repo_fix_demo(db_path: Path, workspace: Path) -> str:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    _create_sample_repo(workspace)

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)

        company_goal_task = runtime.create_task(
            title="Fix parser test company goal",
            objective="Fix the failing parser test and produce a manager-reviewed report.",
            assign_to="ceo",
            acceptance_criteria=["Engineering delivers reviewed fix with test evidence"],
        )
        vp_task = runtime.create_task(
            title="Delegate parser fix to engineering",
            objective="Turn the company goal into an engineering repair plan.",
            assign_to="vp_engineering",
            assigned_by="ceo",
            parent_task_id=company_goal_task.task_id,
            root_goal_id=company_goal_task.task_id,
            acceptance_criteria=["Engineering manager receives a scoped no-tools planning task"],
        )
        runtime.update_task_status(company_goal_task.task_id, status="completed", actor_id="ceo")

        no_tools_task = runtime.create_task(
            title="Summarize repair plan",
            objective="Produce a short no-tools plan for fixing the parser test.",
            assign_to="engineering_manager",
            assigned_by="vp_engineering",
            parent_task_id=vp_task.task_id,
            root_goal_id=company_goal_task.task_id,
            acceptance_criteria=["Plan identifies the parser boolean bug"],
        )
        runtime.update_task_status(vp_task.task_id, status="completed", actor_id="vp_engineering")
        runtime.update_task_status(no_tools_task.task_id, status="completed", actor_id="engineering_manager")
        runtime.register_report(
            ReportContract(
                report_id="report_no_tools",
                from_agent_id="engineering_manager",
                to_agent_id="vp_engineering",
                task_id=no_tools_task.task_id,
                summary="The parser should normalize boolean strings and reject unknown values.",
                status="completed",
                work_done=["Read task objective", "Produced repair plan without tools"],
                evidence=[],
                risks=[],
                blockers=[],
                confidence=0.82,
                cost=UsageCost(tokens_used=0, runtime_seconds=0, tool_calls=0),
                next_action="Assign implementation to worker.",
                requires_decision=False,
                alignment_check="Plan addresses the failing boolean parser test.",
            )
        )

        tool_task = runtime.create_task(
            title="Fix failing parser test",
            objective="Fix parse_bool so pytest passes and submit diff/test artifacts.",
            assign_to="codex_worker",
            assigned_by="engineering_manager",
            parent_task_id=no_tools_task.task_id,
            root_goal_id=company_goal_task.task_id,
            acceptance_criteria=["pytest passes"],
            required_artifacts=["git_diff", "test_log"],
        )
        worker = GenericCLIWorker([sys.executable, str(FIX_PARSER_WORKER)], timeout_seconds=30)
        run = worker.start_task(
            tool_task,
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=workspace,
                agent_id="codex_worker",
                manager_id="engineering_manager",
            ),
        )

        reports = runtime.store.list_reports()
        artifacts = runtime.store.list_artifacts()
        events = runtime.store.list_events()
        dashboard = render_text_dashboard(runtime.store)
        tool_task = runtime.require_task(tool_task.task_id)

    diff_paths = [artifact.path for artifact in artifacts if artifact.type == "git_diff"]
    test_log_paths = [artifact.path for artifact in artifacts if artifact.type == "test_log"]
    tool_reports = [report for report in reports if report.task_id == tool_task.task_id]
    review_events = [event for event in events if event.event_type == "manager_review_decided"]
    total_tokens = sum(report.cost.tokens_used for report in reports)

    return "\n".join(
        [
            "Workforce Runtime Demo: sample-repo-fix",
            "==========================================",
            f"Workspace: {workspace}",
            f"Database: {db_path}",
            "",
            "Task Chain:",
            "  Human -> CEO -> VP Engineering -> Engineering Manager -> Codex Worker",
            f"  Company goal task: {company_goal_task.task_id}",
            f"  VP delegation task: {vp_task.task_id}",
            f"  No-tools task: {no_tools_task.task_id}",
            f"  Tool task: {tool_task.task_id}",
            "",
            "Worker Report:",
            f"  {tool_reports[-1].summary if tool_reports else 'No report'}",
            "",
            "Manager Review:",
            f"  {review_events[-1].payload if review_events else 'No manager review'}",
            "",
            "Artifacts:",
            f"  Diff: {diff_paths[-1] if diff_paths else 'missing'}",
            f"  Test log: {test_log_paths[-1] if test_log_paths else 'missing'}",
            "",
            f"Total cost: {total_tokens} tokens",
            f"Final status: {tool_task.status}",
            f"Worker return code: {run.returncode}",
            "",
            dashboard,
        ]
    )


def run_web_research_demo(db_path: Path, workspace: Path) -> str:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    _create_web_research_workspace(workspace)

    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(EXAMPLE_ORG)
        server = MCPServer(runtime)

        company_goal_task = runtime.create_task(
            title="Research public example domains",
            objective=(
                "Coordinate a small web research task. The terminal worker should fetch a public page, "
                "produce an artifact, and report with evidence."
            ),
            assign_to="ceo",
            acceptance_criteria=[
                "Delegation chain reaches engineering",
                "Worker fetches a public internet page",
                "Artifact and manager-reviewed report exist",
            ],
        )

        ceo_run = _management_run_start(
            runtime,
            "ceo",
            company_goal_task.task_id,
            "I will delegate public-source research to engineering leadership and keep the scope narrow.",
        )
        vp_task = _mcp_tool_call(
            server,
            1,
            "assign",
            {
                "from_agent_id": "ceo",
                "to_agent_id": "vp_engineering",
                "title": "Plan public web research",
                "message": "Ask engineering to fetch a public IANA page and return a reviewed artifact.",
                "parent_task_id": company_goal_task.task_id,
                "root_goal_id": company_goal_task.task_id,
                "acceptance_criteria": ["Engineering manager receives a concrete web research task"],
            },
        )
        _mcp_tool_call(
            server,
            2,
            "update_status",
            {"agent_id": "ceo", "task_id": company_goal_task.task_id, "status": "completed"},
        )
        _management_run_finish(
            runtime,
            "ceo",
            company_goal_task.task_id,
            ceo_run,
            f"Delegated to VP Engineering as {vp_task['task_id']}.",
        )

        vp_run = _management_run_start(
            runtime,
            "vp_engineering",
            str(vp_task["task_id"]),
            "I will convert the web research goal into a worker-ready engineering task.",
        )
        manager_task = _mcp_tool_call(
            server,
            3,
            "assign",
            {
                "from_agent_id": "vp_engineering",
                "to_agent_id": "engineering_manager",
                "title": "Scope IANA page research",
                "message": "Assign a worker to fetch the IANA example domains page and submit a concise artifact.",
                "parent_task_id": str(vp_task["task_id"]),
                "root_goal_id": company_goal_task.task_id,
                "acceptance_criteria": ["Worker receives URL and required artifact details"],
            },
        )
        _mcp_tool_call(
            server,
            4,
            "update_status",
            {"agent_id": "vp_engineering", "task_id": str(vp_task["task_id"]), "status": "completed"},
        )
        _management_run_finish(
            runtime,
            "vp_engineering",
            str(vp_task["task_id"]),
            vp_run,
            f"Scoped execution for Engineering Manager as {manager_task['task_id']}.",
        )

        manager_run = _management_run_start(
            runtime,
            "engineering_manager",
            str(manager_task["task_id"]),
            "I will assign the web fetch to the terminal worker and require artifact evidence.",
        )
        worker_task = _mcp_tool_call(
            server,
            5,
            "assign",
            {
                "from_agent_id": "engineering_manager",
                "to_agent_id": "codex_worker",
                "title": "Fetch and summarize IANA example domains page",
                "message": (
                    "Fetch https://www.iana.org/help/example-domains, write a short artifact with source URL, "
                    "fetch metadata, and extracted findings, then report completion."
                ),
                "parent_task_id": str(manager_task["task_id"]),
                "root_goal_id": company_goal_task.task_id,
                "acceptance_criteria": ["Public page is fetched", "web_research_summary artifact is submitted"],
                "required_artifacts": ["web_research_summary"],
            },
        )
        _mcp_tool_call(
            server,
            6,
            "check_progress",
            {
                "from_agent_id": "engineering_manager",
                "target_agent_id": "codex_worker",
                "task_id": str(worker_task["task_id"]),
                "message": "Confirm worker is assigned before execution starts.",
            },
        )
        _mcp_tool_call(
            server,
            7,
            "update_status",
            {"agent_id": "engineering_manager", "task_id": str(manager_task["task_id"]), "status": "completed"},
        )
        _management_run_finish(
            runtime,
            "engineering_manager",
            str(manager_task["task_id"]),
            manager_run,
            f"Assigned worker task {worker_task['task_id']} and checked progress.",
        )

        worker = GenericCLIWorker([sys.executable, str(WEB_RESEARCH_WORKER)], timeout_seconds=60)
        run = worker.start_task(
            runtime.require_task(str(worker_task["task_id"])),
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=workspace,
                agent_id="codex_worker",
                manager_id="engineering_manager",
            ),
        )

        reports = runtime.store.list_reports()
        artifacts = runtime.store.list_artifacts()
        events = runtime.store.list_events()
        dashboard = render_text_dashboard(runtime.store)
        replay = render_event_replay(runtime.store)
        trajectories = render_agent_trajectories(runtime.store)
        final_worker_task = runtime.require_task(str(worker_task["task_id"]))

    worker_reports = [report for report in reports if report.task_id == final_worker_task.task_id]
    worker_artifacts = [artifact for artifact in artifacts if artifact.task_id == final_worker_task.task_id]
    tool_events = [event for event in events if event.event_type.startswith("mcp_tool_call_")]
    output_events = [event for event in events if event.event_type in {"agent_output", "worker_output"}]
    review_events = [event for event in events if event.event_type == "manager_review_decided"]

    return "\n".join(
        [
            "Workforce Runtime Demo: web-research",
            "====================================",
            f"Workspace: {workspace}",
            f"Database: {db_path}",
            "",
            "Task Chain:",
            "  Human -> CEO -> VP Engineering -> Engineering Manager -> Codex Worker",
            f"  Company goal task: {company_goal_task.task_id}",
            f"  VP task: {vp_task['task_id']}",
            f"  Manager task: {manager_task['task_id']}",
            f"  Worker task: {final_worker_task.task_id}",
            "",
            "Network Work:",
            "  Worker fetched: https://www.iana.org/help/example-domains",
            "",
            "Final Worker Report:",
            f"  {worker_reports[-1].summary if worker_reports else 'No report'}",
            "",
            "Manager Review:",
            f"  {review_events[-1].payload if review_events else 'No manager review'}",
            "",
            "Artifacts:",
            *(f"  {artifact.type}: {artifact.path}" for artifact in worker_artifacts),
            "",
            f"MCP tool-call events: {len(tool_events)}",
            f"Output stream events: {len(output_events)}",
            f"Final status: {final_worker_task.status}",
            f"Worker return code: {run.returncode}",
            "",
            dashboard,
            "",
            replay,
            "",
            trajectories,
        ]
    )


def _create_sample_repo(workspace: Path) -> None:
    (workspace / "parser.py").write_text(
        """def parse_bool(value: str) -> bool:
    return value == "true"
"""
    )
    (workspace / "test_parser.py").write_text(
        """import pytest

from parser import parse_bool


def test_parse_bool_accepts_common_true_values():
    assert parse_bool("true") is True
    assert parse_bool("YES") is True
    assert parse_bool(" 1 ") is True


def test_parse_bool_accepts_common_false_values():
    assert parse_bool("false") is False
    assert parse_bool("NO") is False
    assert parse_bool("0") is False


def test_parse_bool_rejects_unknown_values():
    with pytest.raises(ValueError):
        parse_bool("maybe")
"""
    )
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "parser.py", "test_parser.py"], cwd=workspace, check=True, capture_output=True, text=True)


def _create_web_research_workspace(workspace: Path) -> None:
    (workspace / "README.md").write_text("# Web Research Demo\n")
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True, capture_output=True, text=True)


def run_simple_status_demo(db_path: Path, workspace: Path) -> str:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    snapshots: list[str] = []
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_org(SIMPLE_STATUS_ORG)
        snapshots.append(_dashboard_snapshot("01 org initialized", runtime))

        company_goal = runtime.create_task(
            title="Create launch note",
            objective="Create one concise launch note and show the assignment trajectory.",
            assign_to="ceo",
            acceptance_criteria=["Launch note artifact exists", "Worker report is reviewed"],
        )
        snapshots.append(_dashboard_snapshot("02 human assigned CEO", runtime))

        manager_task = runtime.create_task(
            title="Scope launch note",
            objective="Scope the launch note task for the terminal worker.",
            assign_to="product_manager",
            assigned_by="ceo",
            parent_task_id=company_goal.task_id,
            root_goal_id=company_goal.task_id,
            acceptance_criteria=["Worker receives a small concrete writing task"],
        )
        runtime.update_task_status(company_goal.task_id, status="completed", actor_id="ceo")
        snapshots.append(_dashboard_snapshot("03 CEO delegated to product manager", runtime))

        worker_task = runtime.create_task(
            title="Write concise launch note",
            objective="Write a two-sentence launch note as an artifact and report completion.",
            assign_to="laguna_worker",
            assigned_by="product_manager",
            parent_task_id=manager_task.task_id,
            root_goal_id=company_goal.task_id,
            acceptance_criteria=["Artifact launch_note.md exists", "Report summarizes work"],
            required_artifacts=["launch_note"],
        )
        runtime.update_task_status(manager_task.task_id, status="completed", actor_id="product_manager")
        snapshots.append(_dashboard_snapshot("04 manager assigned terminal worker", runtime))

        progress = runtime.check_progress(
            manager_id="product_manager",
            target_agent_id="laguna_worker",
            task_id=worker_task.task_id,
            message="Periodic check before worker execution.",
        )
        snapshots.append(_dashboard_snapshot("05 manager checked worker progress", runtime))

        worker = GenericCLIWorker([sys.executable, str(SIMPLE_NOTE_WORKER)], timeout_seconds=30)
        run = worker.start_task(
            worker_task,
            RuntimeContext(
                runtime=runtime,
                db_path=db_path,
                workspace=workspace,
                agent_id="laguna_worker",
                manager_id="product_manager",
            ),
        )

        artifacts = runtime.store.list_artifacts()
        reports = runtime.store.list_reports()
        worker_task = runtime.require_task(worker_task.task_id)
        snapshots.append(_dashboard_snapshot("06 worker completed and manager reviewed", runtime))
        replay = render_event_replay(runtime.store)
        trajectories = render_agent_trajectories(runtime.store)

    artifact_paths = [artifact.path for artifact in artifacts if artifact.task_id == worker_task.task_id]
    worker_reports = [report for report in reports if report.task_id == worker_task.task_id]

    return "\n".join(
        [
            "Workforce Runtime Demo: simple-status",
            "======================================",
            f"Workspace: {workspace}",
            f"Database: {db_path}",
            "",
            "Model Routing:",
            "  Managers: openai/gpt-oss-120b:free",
            "  Terminal worker: poolside/laguna-xs.2:free",
            "",
            "Task Chain:",
            "  Human -> CEO -> Product Manager -> Laguna Worker",
            f"  Company goal task: {company_goal.task_id}",
            f"  Manager task: {manager_task.task_id}",
            f"  Worker task: {worker_task.task_id}",
            "",
            "Progress Check:",
            f"  event: {progress['event_id']}",
            f"  active tasks observed: {[task['task_id'] for task in progress['active_tasks']]}",
            "",
            "Final Worker Report:",
            f"  {worker_reports[-1].summary if worker_reports else 'No report'}",
            "",
            "Artifacts:",
            *(f"  {path}" for path in artifact_paths),
            "",
            f"Final status: {worker_task.status}",
            f"Worker return code: {run.returncode}",
            "",
            "Live Dashboard Snapshots:",
            "-------------------------",
            *snapshots,
            "",
            replay,
            "",
            trajectories,
        ]
    )


def _dashboard_snapshot(label: str, runtime: WorkforceRuntime) -> str:
    return "\n".join([f"### {label}", render_text_dashboard(runtime.store)])
