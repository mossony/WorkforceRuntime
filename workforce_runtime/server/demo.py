from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from workforce_runtime.core import (
    AgentProfile,
    Budget,
    Company,
    Organization,
    ReportContract,
    UsageCost,
    WorkQueuePolicy,
    generate_system_prompt,
)
from workforce_runtime.core.permissions import DELEGATE_TASK, READ_REPO, REPORT, REPORT_TO_HUMAN, SUBMIT_ARTIFACT
from workforce_runtime.dashboard import render_agent_trajectories, render_event_replay, render_text_dashboard
from workforce_runtime.mcp.server import MCPServer
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import GenericCLIWorker, RuntimeContext


EXAMPLE_ORG = Path("examples/simple_engineering_org/org.yaml")
SIMPLE_STATUS_ORG = Path("examples/simple_status_org/org.yaml")
FIX_PARSER_WORKER = Path("examples/mock_worker/fix_parser_worker.py").resolve()
SIMPLE_NOTE_WORKER = Path("examples/mock_worker/simple_note_worker.py").resolve()
WEB_RESEARCH_WORKER = Path("examples/mock_worker/web_research_worker.py").resolve()


def build_large_scale_organization(
    *,
    agent_count: int = 3000,
    management_model: str = "openai/gpt-oss-120b:free",
    worker_model: str = "poolside/laguna-m.1:free",
) -> Organization:
    """Build a deterministic large org for runtime/dashboard scale smoke tests."""
    if agent_count < 3:
        raise ValueError("large scale organization requires at least 3 agents")

    company = Company(
        name="Large Scale Smoke Workforce",
        mission=(
            "Validate that Workforce Runtime can initialize, store, query, and visualize "
            f"a {agent_count}-agent organization while only a bounded number of agents run."
        ),
        headcount_limit=agent_count,
        token_budget=agent_count * 20_000,
    )

    agents: list[AgentProfile] = []

    def add_agent(
        *,
        agent_id: str,
        name: str,
        role: str,
        department: str,
        manager_id: str | None,
        worker_type: str,
        model: str,
        responsibilities: list[str],
        permissions: list[str],
        budget: Budget,
    ) -> None:
        agent = AgentProfile(
            id=agent_id,
            name=name,
            role=role,
            department=department,
            manager_id=manager_id,
            worker_type=worker_type,
            model=model,
            responsibilities=responsibilities,
            permissions=permissions,
            budget=budget,
        )
        agent.system_prompt = generate_system_prompt(company, agent)
        agents.append(agent)

    add_agent(
        agent_id="ceo",
        name="Scale CEO",
        role="Chief Executive Officer",
        department="Executive",
        manager_id=None,
        worker_type="openrouter_manager",
        model=management_model,
        responsibilities=["set large-org strategy", "enforce execution slot limits", "report scale readiness"],
        permissions=[DELEGATE_TASK, REPORT, REPORT_TO_HUMAN],
        budget=Budget(max_tokens=250_000, max_runtime_seconds=7200, max_tool_calls=200),
    )

    slots_after_ceo = agent_count - 1
    vp_count = min(max(1, agent_count // 300), max(1, slots_after_ceo - 1))
    manager_capacity = slots_after_ceo - vp_count
    manager_count = min(max(1, agent_count // 30), max(0, manager_capacity - 1))
    worker_count = agent_count - 1 - vp_count - manager_count

    vp_ids: list[str] = []
    for index in range(vp_count):
        agent_id = f"vp_{index + 1:03d}"
        vp_ids.append(agent_id)
        add_agent(
            agent_id=agent_id,
            name=f"Scale VP {index + 1:03d}",
            role="VP of Delivery",
            department=f"Division {index + 1:03d}",
            manager_id="ceo",
            worker_type="openrouter_manager",
            model=management_model,
            responsibilities=["route work to managers", "watch local budget", "summarize division status"],
            permissions=[DELEGATE_TASK, REPORT],
            budget=Budget(max_tokens=120_000, max_runtime_seconds=5400, max_tool_calls=120),
        )

    manager_ids: list[str] = []
    for index in range(manager_count):
        manager_id = f"manager_{index + 1:04d}"
        manager_ids.append(manager_id)
        vp_id = vp_ids[index % len(vp_ids)]
        add_agent(
            agent_id=manager_id,
            name=f"Scale Manager {index + 1:04d}",
            role="Delivery Manager",
            department=f"Delivery Pod {(index % max(vp_count, 1)) + 1:03d}",
            manager_id=vp_id,
            worker_type="openrouter_manager",
            model=management_model,
            responsibilities=["assign bounded worker tasks", "check progress", "escalate blocked work"],
            permissions=[DELEGATE_TASK, REPORT],
            budget=Budget(max_tokens=80_000, max_runtime_seconds=3600, max_tool_calls=80),
        )

    worker_managers = manager_ids or vp_ids
    for index in range(worker_count):
        worker_id = f"worker_{index + 1:05d}"
        manager_id = worker_managers[index % len(worker_managers)]
        add_agent(
            agent_id=worker_id,
            name=f"Scale Worker {index + 1:05d}",
            role="Execution Worker",
            department=f"Execution Pool {(index % max(vp_count, 1)) + 1:03d}",
            manager_id=manager_id,
            worker_type="openrouter_worker",
            model=worker_model,
            responsibilities=["execute one assigned work packet", "emit status", "report evidence"],
            permissions=[READ_REPO, SUBMIT_ARTIFACT, REPORT],
            budget=Budget(max_tokens=40_000, max_runtime_seconds=1800, max_tool_calls=30),
        )

    return Organization(company=company, agents=agents)


def run_large_org_scale_demo(
    db_path: Path,
    workspace: Path,
    *,
    agent_count: int = 3000,
    active_agent_limit: int = 20,
    management_model: str = "openai/gpt-oss-120b:free",
    worker_model: str = "poolside/laguna-m.1:free",
) -> str:
    if agent_count < 3:
        raise ValueError("agent_count must be at least 3")
    active_agent_limit = max(0, min(active_agent_limit, agent_count - 1))
    if db_path.exists():
        db_path.unlink()
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    organization = build_large_scale_organization(
        agent_count=agent_count,
        management_model=management_model,
        worker_model=worker_model,
    )
    workers = [agent for agent in organization.agents if agent.worker_type == "openrouter_worker"]

    started = time.perf_counter()
    with WorkforceRuntime(db_path) as runtime:
        runtime.initialize_organization(organization, source="large_org_scale_demo")
        init_seconds = time.perf_counter() - started
        runtime.record_event(
            event_type="large_org_concurrency_limit_configured",
            actor_id="system",
                payload={
                    "agent_count": len(organization.agents),
                    "max_active_agents": active_agent_limit,
                    "active_worker_runs": 0,
                    "mode": "persistent_work_queue",
                },
            )
        root_task = runtime.create_task(
            title="Large org scale smoke",
            objective=(
                f"Initialize {agent_count} agents and keep active simulated worker slots "
                f"at or below {active_agent_limit}."
            ),
            assign_to="ceo",
            acceptance_criteria=[
                "All agent profiles are stored",
                "Dashboard state can be generated",
                "Active worker runs do not exceed the configured limit",
            ],
        )
        runtime.update_task_status(root_task.task_id, status="completed", actor_id="ceo")

        queue_started = time.perf_counter()
        for worker in workers:
            runtime.enqueue_work_item(
                actor_id="system",
                agent_id=worker.id,
                kind="worker_run",
                task_id=root_task.task_id,
                payload={
                    "mode": "scale_smoke",
                    "objective": "Validate queue scheduling without launching an external LLM process.",
                },
                priority=0,
                model=worker.model,
                idempotency_key=f"large-org-scale:{root_task.task_id}:{worker.id}",
            )
        queued_seconds = time.perf_counter() - queue_started
        claimed_items = runtime.claim_work_items(
            lease_owner="scale_dispatcher",
            limit=active_agent_limit,
            policy=WorkQueuePolicy(
                max_active_agents=active_agent_limit or 1,
                lease_seconds=3600,
                per_kind_limits={"worker_run": active_agent_limit or 1},
            ),
        )

        active_task_ids: list[str] = []
        for slot, item in enumerate(claimed_items, start=1):
            worker = runtime.store.get_agent(item.agent_id)
            if worker is None:
                continue
            task = runtime.create_task(
                title=f"Scale slot {slot:03d} heartbeat",
                objective=(
                    "Hold one simulated execution slot for the large-org scale smoke test. "
                    "This does not launch an external model process."
                ),
                assign_to=worker.id,
                assigned_by=worker.manager_id or "ceo",
                parent_task_id=root_task.task_id,
                root_goal_id=root_task.task_id,
                acceptance_criteria=["worker_run_started event exists for this slot"],
            )
            active_task_ids.append(task.task_id)
            run_id = f"run_scale_slot_{slot:03d}_{worker.id}"
            runtime.record_worker_run_started(
                run_id=run_id,
                task_id=task.task_id,
                actor_id=worker.id,
                executable="simulated-scale-slot",
            )
            runtime.record_event(
                event_type="work_item_execution_started",
                actor_id=worker.id,
                task_id=task.task_id,
                payload={"work_item_id": item.work_item_id, "run_id": run_id},
            )
            runtime.record_worker_output(
                run_id=run_id,
                task_id=task.task_id,
                actor_id=worker.id,
                stream="stdout",
                text=(
                    f"Active slot {slot}/{active_agent_limit}. "
                    f"{worker.id} is simulated as running for scale validation via queued {item.work_item_id}."
                ),
            )

        runtime.report_to_human(
            from_agent_id="ceo",
            task_id=root_task.task_id,
            title="Large org scale smoke initialized",
            message=(
                f"Initialized {len(organization.agents)} agents. "
                f"Queued worker_run items: {len(workers)}. "
                f"Configured active agent limit: {active_agent_limit}. "
                f"Claimed active queue items: {len(claimed_items)}. "
                "No external LLM worker processes were launched."
            ),
            status="running",
            confidence=0.9,
            next_action="Open the dashboard and inspect the collapsed org tree plus active worker slots.",
            requires_decision=False,
        )

        state_started = time.perf_counter()
        from workforce_runtime.dashboard.web_dashboard import build_web_dashboard_state

        state = build_web_dashboard_state(runtime.store)
        state_seconds = time.perf_counter() - state_started
        busy_agents = [agent for agent in runtime.store.list_agents() if agent.status == "busy"]
        latest_sequence = runtime.store.latest_event_sequence()
        queue_snapshot = runtime.work_queue_snapshot()

    state_payload_bytes = len(str(state).encode())
    return "\n".join(
        [
            "Workforce Runtime Demo: large-org-scale",
            "========================================",
            f"Workspace: {workspace}",
            f"Database: {db_path}",
            "",
            f"Agents initialized: {len(organization.agents)}",
            f"Managers/execs: {len(organization.agents) - len(workers)}",
            f"Workers: {len(workers)}",
            f"Queued worker_run items: {queue_snapshot['total']}",
            f"Configured active agent limit: {active_agent_limit}",
            f"Claimed active queue items: {queue_snapshot['active_items']}",
            f"Queue active agents: {queue_snapshot['active_agents']}",
            f"Simulated active worker runs: {len(claimed_items)}",
            f"Busy agents in runtime state: {len(busy_agents)}",
            f"Initialization seconds: {init_seconds:.3f}",
            f"Queue enqueue/claim seconds: {queued_seconds:.3f}",
            f"Dashboard state build seconds: {state_seconds:.3f}",
            f"Dashboard state payload bytes: {state_payload_bytes}",
            f"Latest event sequence: {latest_sequence}",
            f"Root task: {root_task.task_id}",
            f"Active task sample: {', '.join(active_task_ids[:5]) if active_task_ids else '-'}",
            "",
            "Concurrency note:",
            "  This smoke test enforces the limit through the persistent work queue.",
            "  It does not yet provide a general worker-pool scheduler for arbitrary runs.",
        ]
    )


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
