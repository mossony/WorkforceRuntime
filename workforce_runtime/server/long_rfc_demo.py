from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from workforce_runtime.core import ReportContract, UsageCost
from workforce_runtime.mcp.server import MCPServer
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.workers import GenericCLIWorker, RuntimeContext


DEFAULT_RFC_URL = "https://www.rfc-editor.org/rfc/rfc9110.txt"
REQUIRED_AGENT_IDS = {"ceo", "coo", "hr_manager", "vp_research", "research_manager", "codex_worker", "claude_worker"}
WEB_RESEARCH_WORKER = Path("examples/mock_worker/web_research_worker.py").resolve()


def run_long_rfc_demo(
    db_path: str | Path,
    *,
    workspace: str | Path,
    url: str = DEFAULT_RFC_URL,
    delay_seconds: float = 0.8,
) -> dict[str, object]:
    """Run a predefined multi-level research workflow against the dashboard DB."""
    db = Path(db_path)
    workdir = Path(workspace)
    workdir.mkdir(parents=True, exist_ok=True)
    _ensure_workspace(workdir)

    with WorkforceRuntime(db) as runtime:
        _ensure_demo_org(runtime, workdir)
        _require_demo_agents(runtime)
        server = MCPServer(runtime)
        runtime.record_event(
            event_type="demo_run_started",
            actor_id="system",
            payload={"demo": "long-rfc", "workspace": str(workdir), "url": url},
        )

        company_goal = runtime.create_task(
            title="Long RFC research workflow",
            objective=(
                f"Coordinate a multi-agent research workflow for {url}. Preserve the source URL, "
                "artifact requirements, progress checks, peer discussion, and final manager review."
            ),
            assign_to="ceo",
            acceptance_criteria=[
                "Delegation reaches the research worker through at least four management levels",
                "The worker fetches the source URL and submits a web_research_summary artifact",
                "The worker discusses the result with the peer reviewer",
                "A manager review accepts the final report",
            ],
        )
        _pause(delay_seconds)

        hr_task = _mcp_tool_call(
            server,
            "assign",
            {
                "from_agent_id": "ceo",
                "to_agent_id": "hr_manager",
                "title": "Check long-run staffing budget",
                "message": "Confirm the current organization has enough headcount and token budget for the long RFC research demo.",
                "parent_task_id": company_goal.task_id,
                "root_goal_id": company_goal.task_id,
                "acceptance_criteria": ["HR records a staffing and budget note"],
            },
        )
        _pause(delay_seconds)
        hr_run = _management_run_start(
            runtime,
            "hr_manager",
            str(hr_task["task_id"]),
            "Inspecting headcount and token budget before the research chain starts.",
        )
        _pause(delay_seconds)
        _mcp_tool_call(
            server,
            "update_status",
            {"agent_id": "hr_manager", "task_id": str(hr_task["task_id"]), "status": "completed"},
        )
        _mcp_tool_call(
            server,
            "report",
            {
                "from_agent_id": "hr_manager",
                "to_agent_id": "ceo",
                "task_id": str(hr_task["task_id"]),
                "summary": "Headcount and token budget are sufficient for the predefined long RFC demo.",
                "status": "completed",
                "work_done": ["Checked headcount", "Checked allocated token budget"],
                "evidence": [{"type": "org_context", "path": "runtime database"}],
                "risks": [],
                "blockers": [],
                "confidence": 0.82,
                "cost": {"tokens_used": 0, "runtime_seconds": 0, "tool_calls": 1},
                "next_action": "CEO can continue delegation.",
                "requires_decision": False,
                "alignment_check": "No hiring needed for this demo run.",
            },
        )
        _management_run_finish(runtime, "hr_manager", str(hr_task["task_id"]), hr_run, "Reported staffing readiness to CEO.")
        _pause(delay_seconds)

        ceo_run = _management_run_start(
            runtime,
            "ceo",
            company_goal.task_id,
            f"Delegating RFC research for {url} to operations while preserving source and artifact requirements.",
        )
        coo_task = _mcp_tool_call(
            server,
            "assign",
            {
                "from_agent_id": "ceo",
                "to_agent_id": "coo",
                "title": f"Delegate RFC source research for {url}",
                "message": f"Route this RFC source research to research leadership. Preserve URL {url}, evidence, and artifact requirements.",
                "parent_task_id": company_goal.task_id,
                "root_goal_id": company_goal.task_id,
                "acceptance_criteria": ["Research leadership receives the source URL and artifact constraints"],
            },
        )
        _mcp_tool_call(server, "update_status", {"agent_id": "ceo", "task_id": company_goal.task_id, "status": "completed"})
        _management_run_finish(runtime, "ceo", company_goal.task_id, ceo_run, f"Delegated main work to COO as {coo_task['task_id']}.")
        _pause(delay_seconds)

        coo_task_id = str(coo_task["task_id"])
        coo_run = _management_run_start(runtime, "coo", coo_task_id, "Routing the research objective to VP Research.")
        vp_task = _mcp_tool_call(
            server,
            "assign",
            {
                "from_agent_id": "coo",
                "to_agent_id": "vp_research",
                "title": f"Plan research execution for {url}",
                "message": f"Turn the RFC source {url} into a manager-ready research execution plan.",
                "parent_task_id": coo_task_id,
                "root_goal_id": company_goal.task_id,
                "acceptance_criteria": ["Research manager receives concrete worker instructions"],
            },
        )
        _mcp_tool_call(server, "update_status", {"agent_id": "coo", "task_id": coo_task_id, "status": "completed"})
        _management_run_finish(runtime, "coo", coo_task_id, coo_run, f"Delegated research planning to VP Research as {vp_task['task_id']}.")
        _pause(delay_seconds)

        vp_task_id = str(vp_task["task_id"])
        vp_run = _management_run_start(runtime, "vp_research", vp_task_id, "Scoping the worker task and review expectations.")
        manager_task = _mcp_tool_call(
            server,
            "assign",
            {
                "from_agent_id": "vp_research",
                "to_agent_id": "research_manager",
                "title": f"Assign and review RFC artifact for {url}",
                "message": f"Assign a worker to fetch {url}, submit web_research_summary, discuss with peer reviewer, and report evidence.",
                "parent_task_id": vp_task_id,
                "root_goal_id": company_goal.task_id,
                "acceptance_criteria": ["Worker receives URL", "Worker report receives manager review"],
            },
        )
        _mcp_tool_call(server, "update_status", {"agent_id": "vp_research", "task_id": vp_task_id, "status": "completed"})
        _management_run_finish(runtime, "vp_research", vp_task_id, vp_run, f"Assigned execution management as {manager_task['task_id']}.")
        _pause(delay_seconds)

        manager_task_id = str(manager_task["task_id"])
        manager_run = _management_run_start(runtime, "research_manager", manager_task_id, "Creating worker task and progress checkpoint.")
        worker_task = _mcp_tool_call(
            server,
            "assign",
            {
                "from_agent_id": "research_manager",
                "to_agent_id": "codex_worker",
                "title": f"Fetch and summarize RFC source {url}",
                "message": (
                    f"Fetch {url}, write a concise artifact with source URL, fetch metadata, extracted findings, "
                    "and status. Discuss the result with claude_worker before reporting."
                ),
                "parent_task_id": manager_task_id,
                "root_goal_id": company_goal.task_id,
                "context_refs": [url],
                "acceptance_criteria": ["Source URL is fetched", "web_research_summary artifact is submitted"],
                "required_artifacts": ["web_research_summary"],
            },
        )
        _pause(delay_seconds)
        _mcp_tool_call(
            server,
            "check_progress",
            {
                "from_agent_id": "research_manager",
                "target_agent_id": "codex_worker",
                "task_id": str(worker_task["task_id"]),
                "message": "Confirm assignment before worker execution starts.",
            },
        )
        _mcp_tool_call(server, "update_status", {"agent_id": "research_manager", "task_id": manager_task_id, "status": "completed"})
        _management_run_finish(runtime, "research_manager", manager_task_id, manager_run, f"Worker task {worker_task['task_id']} is ready.")
        _pause(delay_seconds)

        worker = GenericCLIWorker([sys.executable, str(WEB_RESEARCH_WORKER)], timeout_seconds=90)
        run = worker.start_task(
            runtime.require_task(str(worker_task["task_id"])),
            RuntimeContext(
                runtime=runtime,
                db_path=db,
                workspace=workdir,
                agent_id="codex_worker",
                manager_id="research_manager",
            ),
        )
        _pause(delay_seconds)

        reports = runtime.store.list_reports_by_task(str(worker_task["task_id"]))
        review_events = [event for event in runtime.store.list_events() if event.event_type == "manager_review_decided"]
        runtime.record_event(
            event_type="demo_run_finished",
            actor_id="system",
            task_id=str(worker_task["task_id"]),
            payload={
                "demo": "long-rfc",
                "status": runtime.require_task(str(worker_task["task_id"])).status,
                "worker_returncode": run.returncode,
                "report_id": reports[-1].report_id if reports else "",
                "review_decision": review_events[-1].payload.get("decision") if review_events else "",
            },
        )
        return {
            "ok": True,
            "demo": "long-rfc",
            "workspace": str(workdir),
            "company_goal_task_id": company_goal.task_id,
            "worker_task_id": str(worker_task["task_id"]),
            "worker_returncode": run.returncode,
            "final_status": runtime.require_task(str(worker_task["task_id"])).status,
            "report_id": reports[-1].report_id if reports else "",
        }


def _ensure_demo_org(runtime: WorkforceRuntime, workspace: Path) -> None:
    if runtime.store.list_agents():
        return
    org_path = workspace / "long_rfc_org.yaml"
    org_path.write_text(_ORG_YAML)
    runtime.initialize_org(org_path)


def _require_demo_agents(runtime: WorkforceRuntime) -> None:
    existing = {agent.id for agent in runtime.store.list_agents()}
    missing = sorted(REQUIRED_AGENT_IDS - existing)
    if missing:
        raise ValueError(f"long RFC demo requires missing agents: {', '.join(missing)}")


def _ensure_workspace(workspace: Path) -> None:
    (workspace / "README.md").write_text("# Long RFC Dashboard Demo\n")
    if not (workspace / ".git").exists():
        subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True, capture_output=True, text=True)


def _mcp_tool_call(server: MCPServer, name: str, arguments: dict[str, object]) -> dict[str, object]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": uuid4().hex,
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
        adapter="dashboard-long-rfc-demo",
        model=agent.model if agent else "",
    )
    runtime.record_agent_output(run_id=run_id, task_id=task_id, actor_id=agent_id, stream="assistant", text=message)
    return run_id


def _management_run_finish(runtime: WorkforceRuntime, agent_id: str, task_id: str, run_id: str, message: str) -> None:
    runtime.record_agent_output(run_id=run_id, task_id=task_id, actor_id=agent_id, stream="assistant", text=message)
    runtime.record_agent_run_finished(
        run_id=run_id,
        task_id=task_id,
        actor_id=agent_id,
        status="completed",
        usage={"tool_calls": 1},
    )


def _pause(delay_seconds: float) -> None:
    if delay_seconds > 0:
        time.sleep(delay_seconds)


_ORG_YAML = """company:
  name: Long RFC Demo Workforce
  mission: Validate streamed multi-agent delegation on a public RFC research task.
  headcount_limit: 8
  token_budget: 900000

agents:
  - id: ceo
    name: CEO Agent
    role: CEO
    department: Executive
    manager_id: null
    worker_type: generic_cli
    model: openai/gpt-oss-120b:free
    responsibilities: [Convert human goals into executive priorities, Preserve source and artifact requirements]
    permissions: [delegate_task, report, hire_agent]
    budget: {max_tokens: 120000, max_runtime_seconds: 3600, max_tool_calls: 80}

  - id: coo
    name: COO Agent
    role: COO
    department: Operations
    manager_id: ceo
    worker_type: generic_cli
    model: openai/gpt-oss-120b:free
    responsibilities: [Coordinate operational handoff from CEO to research leadership]
    permissions: [delegate_task, report]
    budget: {max_tokens: 120000, max_runtime_seconds: 3600, max_tool_calls: 80}

  - id: hr_manager
    name: HR Manager Agent
    role: HR Manager
    department: People
    manager_id: ceo
    worker_type: generic_cli
    model: openai/gpt-oss-120b:free
    responsibilities: [Track headcount and hiring budget]
    permissions: [hire_agent, report]
    budget: {max_tokens: 60000, max_runtime_seconds: 1800, max_tool_calls: 40}

  - id: vp_research
    name: VP Research Agent
    role: VP Research
    department: Research
    manager_id: coo
    worker_type: generic_cli
    model: openai/gpt-oss-120b:free
    responsibilities: [Convert research goals into manager-ready tasks]
    permissions: [delegate_task, report]
    budget: {max_tokens: 120000, max_runtime_seconds: 3600, max_tool_calls: 80}

  - id: research_manager
    name: Research Manager Agent
    role: Research Manager
    department: Research
    manager_id: vp_research
    worker_type: generic_cli
    model: openai/gpt-oss-120b:free
    responsibilities: [Assign web research to workers, Check progress and review final reports]
    permissions: [delegate_task, report]
    budget: {max_tokens: 120000, max_runtime_seconds: 3600, max_tool_calls: 100}

  - id: codex_worker
    name: Laguna Research Worker
    role: Research Engineer
    department: Research
    manager_id: research_manager
    worker_type: generic_cli
    model: poolside/laguna-m.1:free
    responsibilities: [Fetch public source material, Submit artifacts and report with evidence]
    permissions: [read_repo, submit_artifact, report]
    budget: {max_tokens: 90000, max_runtime_seconds: 1800, max_tool_calls: 80}

  - id: claude_worker
    name: Claude Peer Reviewer
    role: Peer Reviewer
    department: Research
    manager_id: research_manager
    worker_type: claude_code
    model: claude-code
    responsibilities: [Receive peer review discussions from the worker]
    permissions: [read_repo, report]
    budget: {max_tokens: 60000, max_runtime_seconds: 1800, max_tool_calls: 40}
"""
