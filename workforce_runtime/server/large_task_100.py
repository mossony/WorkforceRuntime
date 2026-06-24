from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from workforce_runtime.config.model_registry import model_capabilities
from workforce_runtime.core import AgentProfile, Budget, Company, Organization, WorkQueuePolicy
from workforce_runtime.core.permissions import DELEGATE_TASK, READ_REPO, REPORT, REPORT_TO_HUMAN, SUBMIT_ARTIFACT
from workforce_runtime.evals.benchmark import _run_agent_json, _usage_token_count
from workforce_runtime.llm import RoutedLLMClient, extract_json_object
from workforce_runtime.mcp.server import MCPServer
from workforce_runtime.org_designer import OrgDesignRequest, organization_from_mapping
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.server.tracing import write_trace_file
from workforce_runtime.core.system_prompt import generate_system_prompt
from workforce_runtime.v2.v1_bridge import analyze_v1_runtime


DEFAULT_PLAN_PATH = Path("examples/Large_Task_100_v0.md")
DEFAULT_MANAGEMENT_MODELS = [
    "openai/gpt-oss-120b:free",
    "openrouter/owl-alpha",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]
DEFAULT_WORKER_MODELS = [
    "poolside/laguna-m.1:free",
    "cohere/north-mini-code:free",
    "openrouter/owl-alpha",
    "poolside/laguna-xs.2:free",
    "openai/gpt-oss-20b:free",
]
DEFAULT_DESIGN_MODELS = [
    "openrouter/owl-alpha",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-120b:free",
]


@dataclass(frozen=True)
class PositionSpec:
    number: int
    role: str
    department: str


class LargeTask100Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    db_path: str
    workspace: str
    plan_path: str
    root_task_id: str
    agent_count: int
    llm_attempted_agent_count: int
    llm_completed_agent_count: int
    llm_failed_agent_count: int
    task_count: int
    report_count: int
    artifact_count: int
    human_report_count: int
    model_counts: dict[str, int] = Field(default_factory=dict)
    failed_agents: list[dict[str, str]] = Field(default_factory=list)
    trace_path: str
    task_trace_path: str
    v2_analysis_path: str = ""
    summary_path: str


def load_large_task_positions(plan_path: str | Path = DEFAULT_PLAN_PATH) -> list[PositionSpec]:
    path = Path(plan_path)
    text = path.read_text()
    positions: list[PositionSpec] = []
    department = "General"
    in_positions = False
    for line in text.splitlines():
        if line.startswith("# 6. The 100 Positions"):
            in_positions = True
            continue
        if in_positions and line.startswith("# 7. "):
            break
        section = re.match(r"^##\s+6\.\d+\s+(.+?)(?:\s+—\s+\d+\s+Positions)?\s*$", line)
        if section:
            department = section.group(1).strip()
            continue
        match = re.match(r"^(\d+)\.\s+\*\*(.+?)\*\*", line.strip())
        if match:
            positions.append(PositionSpec(number=int(match.group(1)), role=match.group(2).strip(), department=department))
    if len(positions) < 90:
        raise ValueError(f"expected around 100 positions in {path}, found {len(positions)}")
    return positions


def build_large_task_100_organization(
    *,
    plan_path: str | Path = DEFAULT_PLAN_PATH,
    max_agents: int = 100,
    management_models: list[str] | None = None,
    worker_models: list[str] | None = None,
) -> Organization:
    positions = load_large_task_positions(plan_path)[:max_agents]
    management_models = management_models or DEFAULT_MANAGEMENT_MODELS
    worker_models = worker_models or DEFAULT_WORKER_MODELS
    manager_by_number = _manager_map(positions)
    direct_reports = _direct_reports(positions, manager_by_number)

    company = Company(
        name="OpenForge 100-Role Launch Workforce",
        mission=(
            "Design, implement, test, release, and operate OpenForge: a multi-tenant AI code review "
            "and repository intelligence platform, while observing organizational bottlenecks."
        ),
        headcount_limit=len(positions),
        token_budget=25_000_000,
    )
    agents: list[AgentProfile] = []
    for index, position in enumerate(positions):
        agent_id = _agent_id(position)
        manager_number = manager_by_number.get(position.number)
        manager_id = _agent_id(_position_by_number(positions, manager_number)) if manager_number else None
        has_reports = bool(direct_reports.get(position.number))
        permissions = [READ_REPO, SUBMIT_ARTIFACT, REPORT]
        if has_reports:
            permissions.append(DELEGATE_TASK)
        if manager_id is None:
            permissions.extend([DELEGATE_TASK, REPORT_TO_HUMAN])
        model_pool = management_models if has_reports or manager_id is None else worker_models
        model = model_pool[index % len(model_pool)]
        agent = AgentProfile(
            id=agent_id,
            name=f"{position.role} Agent",
            role=position.role,
            department=position.department,
            manager_id=manager_id,
            worker_type="openrouter_manager" if has_reports or manager_id is None else "openrouter_worker",
            model=model,
            responsibilities=_responsibilities_for(position),
            permissions=permissions,
            budget=Budget(
                max_tokens=350_000 if has_reports else 180_000,
                max_runtime_seconds=7200 if has_reports else 3600,
                max_tool_calls=120 if has_reports else 60,
            ),
        )
        agent.system_prompt = generate_system_prompt(company, agent)
        agents.append(agent)
    return Organization(company=company, agents=agents)


def run_large_task_100_real_llm(
    db_path: str | Path,
    workspace: str | Path,
    *,
    plan_path: str | Path = DEFAULT_PLAN_PATH,
    max_agents: int = 100,
    active_agent_limit: int = 25,
    reset: bool = True,
    management_models: list[str] | None = None,
    worker_models: list[str] | None = None,
    llm_json_config: dict[str, Any] | None = None,
    require_llm_org_design: bool = True,
    allow_position_fallback: bool = False,
) -> LargeTask100Result:
    db = Path(db_path)
    workdir = Path(workspace)
    if reset and db.exists():
        db.unlink()
    if reset and workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    client = RoutedLLMClient()
    design_dir = workdir / "org_design"
    management_models = management_models or DEFAULT_MANAGEMENT_MODELS
    worker_models = worker_models or DEFAULT_WORKER_MODELS
    try:
        organization, design_metadata, governor_design_review = _design_and_govern_large_task_organization(
            client=client,
            plan_path=plan_path,
            max_agents=max_agents,
            design_dir=design_dir,
            management_models=management_models,
            worker_models=worker_models,
            requested_active_agent_limit=active_agent_limit,
        )
    except Exception as exc:
        if require_llm_org_design and not allow_position_fallback:
            raise
        organization = build_large_task_100_organization(
            plan_path=plan_path,
            max_agents=max_agents,
            management_models=management_models,
            worker_models=worker_models,
        )
        design_metadata = {
            "source": "position_fallback_after_design_failure",
            "error": str(exc),
            "agent_count": len(organization.agents),
        }
        organization, governor_design_review = _governor_manage_organization_design(
            client=client,
            organization=organization,
            plan_path=Path(plan_path),
            design_dir=design_dir,
            management_models=management_models,
            worker_models=worker_models,
            requested_active_agent_limit=active_agent_limit,
        )
    root_agent = _root_agent(organization)
    active_agent_limit = _governor_active_agent_limit(governor_design_review, default=active_agent_limit)
    policy = WorkQueuePolicy(
        max_active_agents=max(1, active_agent_limit),
        lease_seconds=900,
        per_kind_limits={"llm_request": max(1, active_agent_limit), "tool_call": max(2, active_agent_limit * 2)},
        per_model_limits={},
        per_tool_limits={},
        allow_same_agent_parallel=False,
    )

    with WorkforceRuntime(db) as runtime:
        runtime.initialize_organization(organization, source=f"large_task_100:{plan_path}")
        server = MCPServer(runtime)
        runtime.record_event(
            event_type="large_task_100_run_started",
            actor_id="system",
            payload={
                "plan_path": str(plan_path),
                "agent_count": len(organization.agents),
                "active_agent_limit": active_agent_limit,
                "management_models": management_models,
                "worker_models": worker_models,
                "org_design": design_metadata,
                "governor_design_review": governor_design_review,
                "effective_active_agent_limit": active_agent_limit,
            },
        )
        root_task = runtime.create_task(
            title="OpenForge public beta launch and operations challenge",
            objective=_root_objective(Path(plan_path)),
            assign_to=root_agent.id,
            constraints=[
                "Use real LLM calls for each agent role.",
                "Do not spend real money or use production credentials.",
                "Report failures and coordination problems rather than hiding them.",
                "Keep at most the configured active-agent limit leased in the work queue.",
            ],
            acceptance_criteria=[
                "Approximately 100 agents are initialized from the plan.",
                "Every initialized agent receives a task and attempts an LLM run.",
                "Agents communicate through Workforce Runtime MCP tools.",
                "CEO reports final outcome to the human operator.",
            ],
            required_artifacts=["agent_artifact", "ceo_final_report", "trace"],
        )
        task_by_agent = {root_agent.id: root_task.task_id}
        agents_by_manager = _agents_by_manager(organization)
        ordered_agents = _breadth_first_agents(organization)
        agent_payloads: dict[str, dict[str, Any]] = {}
        failed_agents: list[dict[str, str]] = []

        for agent in ordered_agents:
            task_id = task_by_agent.get(agent.id)
            if task_id is None:
                continue
            child_agents = agents_by_manager.get(agent.id, [])
            runtime.update_task_status(task_id, status="in_progress", actor_id=agent.id)
            work_item = runtime.enqueue_work_item(
                actor_id="runtime",
                agent_id=agent.id,
                kind="llm_request",
                task_id=task_id,
                payload={"purpose": "large_task_100_agent_execution"},
                priority=10 if agent.manager_id is None else 0,
                model=_model_candidates_for_agent(agent)[0],
                idempotency_key=f"large-task-100:{task_id}:{agent.id}",
                max_attempts=1,
            )
            claimed = runtime.claim_work_items(lease_owner="large_task_100_runner", limit=1, policy=policy)
            if not any(item.work_item_id == work_item.work_item_id for item in claimed):
                runtime.record_event(
                    event_type="large_task_100_scheduler_wait",
                    actor_id="system",
                    task_id=task_id,
                    payload={"agent_id": agent.id, "work_item_id": work_item.work_item_id},
                )
                claimed = runtime.claim_work_items(lease_owner="large_task_100_runner", limit=1, policy=policy)
            payload = _run_agent_contribution(
                runtime=runtime,
                client=client,
                agent=agent,
                task_id=task_id,
                root_task_id=root_task.task_id,
                child_agents=child_agents,
                workspace=workdir,
                llm_json_config=llm_json_config,
            )
            agent_payloads[agent.id] = payload
            error = str(payload.get("_runtime_error") or "")
            if error:
                failed_agents.append({"agent_id": agent.id, "role": agent.role, "model": agent.model or "", "error": error[:500]})
                runtime.fail_work_item(work_item.work_item_id, actor_id=agent.id, error=error, retry=False)
            else:
                runtime.complete_work_item(work_item.work_item_id, actor_id=agent.id, result={"status": "completed"})

            artifact_path = _write_agent_artifact(workdir, agent=agent, task_id=task_id, payload=payload)
            _mcp_tool_call(
                server,
                "submit_artifact",
                {
                    "agent_id": agent.id,
                    "task_id": task_id,
                    "artifact_type": "agent_artifact",
                    "path": str(artifact_path),
                    "description": f"Large Task 100 contribution from {agent.role}.",
                },
            )
            for child in child_agents:
                assigned = _mcp_tool_call(
                    server,
                    "assign",
                    {
                        "from_agent_id": agent.id,
                        "to_agent_id": child.id,
                        "title": f"{child.role}: OpenForge work packet",
                        "message": _child_assignment_message(parent=agent, child=child, payload=payload),
                        "parent_task_id": task_id,
                        "root_goal_id": root_task.task_id,
                        "context_refs": [str(plan_path)],
                        "constraints": ["Preserve OpenForge launch objective.", "Report evidence, risks, and blockers."],
                        "acceptance_criteria": [
                            "Attempt the assigned work with a real LLM call.",
                            "Submit an artifact and report to the direct manager.",
                        ],
                        "required_artifacts": ["agent_artifact"],
                    },
                )
                task_by_agent[child.id] = str(assigned["task_id"])

            if agent.manager_id is not None:
                _mcp_tool_call(server, "report", _report_args(agent=agent, task_id=task_id, payload=payload, artifact_path=artifact_path))
            else:
                runtime.update_task_status(task_id, status="completed" if not error else "failed", actor_id=agent.id)

        final_payload = _run_ceo_final_report(
            runtime=runtime,
            client=client,
            ceo=root_agent,
            task_id=root_task.task_id,
            workspace=workdir,
            agent_payloads=agent_payloads,
            failed_agents=failed_agents,
            llm_json_config=llm_json_config,
        )
        final_path = _write_final_report(workdir, final_payload, failed_agents=failed_agents)
        _mcp_tool_call(
            server,
            "submit_artifact",
            {
                "agent_id": root_agent.id,
                "task_id": root_task.task_id,
                "artifact_type": "ceo_final_report",
                "path": str(final_path),
                "description": "CEO final report to the human operator.",
            },
        )
        _mcp_tool_call(
            server,
            "report_to_human",
            {
                "from_agent_id": root_agent.id,
                "task_id": root_task.task_id,
                "title": "OpenForge 100-agent run report",
                "message": str(final_payload.get("human_message") or final_payload.get("summary") or "Large Task 100 run completed."),
                "status": "completed" if not failed_agents else "needs_review",
                "confidence": float(final_payload.get("confidence") or (0.65 if not failed_agents else 0.45)),
                "next_action": str(final_payload.get("next_action") or "Review failed agents, trace, and artifacts."),
                "requires_decision": bool(final_payload.get("requires_decision", False)),
            },
        )
        runtime.update_task_status(root_task.task_id, status="completed" if not failed_agents else "failed", actor_id=root_agent.id)
        trace_path = write_trace_file(
            runtime,
            workspace=workdir,
            run_id=f"large_task_100_{root_task.task_id}",
            label="large-task-100",
            task_id=root_task.task_id,
            metadata={"agent_count": len(organization.agents), "failed_agent_count": len(failed_agents)},
        )
        task_trace = runtime.export_task_trace(
            root_task.task_id,
            workspace=workdir / "task_traces",
            trace_id=f"large_task_100_{root_task.task_id}",
            include_descendants=True,
            include_file_contents=False,
        )
        v2_export_dir = workdir / "v2_analysis"
        try:
            analyze_v1_runtime(
                v1_db_path=db,
                task_id=root_task.task_id,
                v2_db_path=workdir / "large_task_100_v2.sqlite",
                export_dir=v2_export_dir,
            )
            v2_analysis_path = str(v2_export_dir / "v2_analysis.json")
        except Exception as exc:  # noqa: BLE001 - final report should still preserve V1 evidence.
            runtime.record_event(
                event_type="large_task_100_v2_analysis_failed",
                actor_id="system",
                task_id=root_task.task_id,
                payload={"error": str(exc)[:1000], "export_dir": str(v2_export_dir)},
            )
            v2_analysis_path = ""
        result = _build_result(
            runtime=runtime,
            db=db,
            workdir=workdir,
            plan_path=Path(plan_path),
            root_task_id=root_task.task_id,
            trace_path=trace_path,
            task_trace_path=Path(task_trace.path),
            v2_analysis_path=v2_analysis_path,
            summary_path=final_path,
            failed_agents=failed_agents,
        )
        summary_path = workdir / "large_task_100_result.json"
        summary_path.write_text(json.dumps(result.model_dump(mode="json"), indent=2))
        result = result.model_copy(update={"summary_path": str(summary_path)})
        summary_path.write_text(json.dumps(result.model_dump(mode="json"), indent=2))
        return result


def _design_and_govern_large_task_organization(
    *,
    client: RoutedLLMClient,
    plan_path: str | Path,
    max_agents: int,
    design_dir: Path,
    management_models: list[str],
    worker_models: list[str],
    requested_active_agent_limit: int,
) -> tuple[Organization, dict[str, Any], dict[str, Any]]:
    design_rounds: list[dict[str, Any]] = []
    governor_feedback = ""
    last_error = ""
    last_governor_review: dict[str, Any] = {}
    for round_index in range(1, 3):
        round_dir = design_dir / f"round_{round_index:02d}"
        organization, round_metadata = _design_large_task_organization_with_llm(
            client=client,
            plan_path=plan_path,
            max_agents=max_agents,
            design_dir=round_dir,
            management_models=management_models,
            worker_models=worker_models,
            governor_feedback=governor_feedback,
        )
        governed, governor_review = _governor_manage_organization_design(
            client=client,
            organization=organization,
            plan_path=Path(plan_path),
            design_dir=round_dir,
            management_models=management_models,
            worker_models=worker_models,
            requested_active_agent_limit=requested_active_agent_limit,
        )
        last_governor_review = governor_review
        design_rounds.append(
            {
                "round": round_index,
                "design": round_metadata,
                "governor": {
                    "approved": bool(governor_review.get("approved")),
                    "summary": governor_review.get("summary"),
                    "risks": governor_review.get("risks", []),
                    "required_changes": governor_review.get("required_changes", []),
                    "applied_actions": governor_review.get("applied_actions", []),
                    "action_errors": governor_review.get("action_errors", []),
                    "model": governor_review.get("_model"),
                },
            }
        )
        runtime_validator = _runtime_validator_assessment(
            governed,
            management_models=management_models,
            worker_models=worker_models,
        )
        governor_review["runtime_validator"] = runtime_validator
        if runtime_validator["ok"]:
            if not _governor_allows_execution(governor_review):
                governor_review["runtime_validator_override"] = True
            design_dir.mkdir(parents=True, exist_ok=True)
            (design_dir / "organization.json").write_text(organization.model_dump_json(indent=2))
            (design_dir / "organization_after_governor.json").write_text(governed.model_dump_json(indent=2))
            (design_dir / "governor_design_review.json").write_text(json.dumps(governor_review, indent=2))
            (design_dir / "design_rounds.json").write_text(json.dumps(design_rounds, indent=2))
            return governed, {"source": "governed_llm_org_design_agent", "rounds": design_rounds}, governor_review
        governor_feedback = _format_governor_feedback(governor_review)
        last_error = f"governor rejected design round {round_index}: {governor_feedback[:1000]}"

    raise RuntimeError(last_error or f"governor rejected final design: {json.dumps(last_governor_review)[:1000]}")


def _design_large_task_organization_with_llm(
    *,
    client: RoutedLLMClient,
    plan_path: str | Path,
    max_agents: int,
    design_dir: Path,
    management_models: list[str],
    worker_models: list[str],
    governor_feedback: str = "",
) -> tuple[Organization, dict[str, Any]]:
    design_dir.mkdir(parents=True, exist_ok=True)
    positions = load_large_task_positions(plan_path)[:max_agents]
    position_lines = "\n".join(f"{item.number}. {item.role} | {item.department}" for item in positions)
    request = OrgDesignRequest(
        goal=_root_objective(Path(plan_path)),
        company_name="OpenForge 100-Role Launch Workforce",
        headcount_limit=max_agents,
        token_budget=25_000_000,
        management_model=management_models[0],
        worker_model=worker_models[0],
        include_hr=True,
        max_management_depth=5,
    )
    if max_agents > 30:
        organization, metadata = _design_large_task_organization_segmented(
            client=client,
            plan_path=plan_path,
            max_agents=max_agents,
            design_dir=design_dir,
            management_models=management_models,
            worker_models=worker_models,
            previous_error="single-shot org JSON skipped for scale; using segmented LLM design policy",
            governor_feedback=governor_feedback,
        )
        return organization, {
            **metadata,
            "single_shot_skipped": True,
        }
    last_error = ""
    design_models = _ordered_design_models(management_models=management_models, worker_models=worker_models)
    for attempt, model in enumerate(design_models[:4], start=1):
        prompt = _org_design_agent_prompt(
            request=request,
            positions=position_lines,
            management_models=management_models,
            worker_models=worker_models,
            attempt=attempt,
            last_error=last_error,
            governor_feedback=governor_feedback,
        )
        (design_dir / f"attempt_{attempt:02d}_prompt.txt").write_text(prompt)
        started = time.perf_counter()
        try:
            response = client.chat(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are the Workforce Runtime architecture design agent. "
                            "Design the organization; do not execute the work. Return only JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=min(12000, max(2500, max_agents * 120)),
                reasoning=None,
                stream=False,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 - try the next configured design model.
            last_error = str(exc)
            (design_dir / f"attempt_{attempt:02d}_error.txt").write_text(last_error)
            continue
        elapsed = time.perf_counter() - started
        (design_dir / f"attempt_{attempt:02d}_response.txt").write_text(response.content)
        (design_dir / f"attempt_{attempt:02d}_response.json").write_text(
            json.dumps({"raw": response.raw, "usage": response.usage}, indent=2)
        )
        try:
            data = extract_json_object(response.content)
            organization = organization_from_mapping(data, request=request)
            organization = _normalize_designed_organization(
                organization,
                max_agents=max_agents,
                management_models=management_models,
                worker_models=worker_models,
            )
            _validate_designed_organization(organization, min_agents=max(3, int(max_agents * 0.9)))
            (design_dir / "organization.json").write_text(organization.model_dump_json(indent=2))
            return organization, {
                "source": "llm_org_design_agent",
                "model": model,
                "attempt": attempt,
                "elapsed_seconds": elapsed,
                "agent_count": len(organization.agents),
                "design_dir": str(design_dir),
                "usage": response.usage,
            }
        except Exception as exc:  # noqa: BLE001 - retry with the validation error in context.
            last_error = str(exc)
            (design_dir / f"attempt_{attempt:02d}_error.txt").write_text(last_error)
    organization, metadata = _design_large_task_organization_segmented(
        client=client,
        plan_path=plan_path,
        max_agents=max_agents,
        design_dir=design_dir,
        management_models=management_models,
        worker_models=worker_models,
        previous_error=last_error,
        governor_feedback=governor_feedback,
    )
    return organization, {
        **metadata,
        "single_shot_failed": True,
        "single_shot_last_error": last_error,
    }


def _design_large_task_organization_segmented(
    *,
    client: RoutedLLMClient,
    plan_path: str | Path,
    max_agents: int,
    design_dir: Path,
    management_models: list[str],
    worker_models: list[str],
    previous_error: str,
    governor_feedback: str = "",
) -> tuple[Organization, dict[str, Any]]:
    positions = load_large_task_positions(plan_path)[:max_agents]
    departments = sorted({position.department for position in positions})
    feedback_text = f"\nGovernor feedback from the previous rejected design:\n{governor_feedback}\n" if governor_feedback else ""
    prompt = (
        "The single-shot 100-agent JSON design failed. Design a compact organization policy instead. "
        "The runtime will instantiate the provided challenge positions according to your policy.\n\n"
        f"Previous error: {previous_error}\n"
        f"{feedback_text}"
        f"Target agent count: {max_agents}\n"
        f"Departments: {departments}\n"
        f"Management models: {management_models}\n"
        f"Worker models: {worker_models}\n"
        "Return JSON only: {"
        "\"company_name\": string, "
        "\"mission\": string, "
        "\"department_leads\": {department: role}, "
        "\"executive_alignment\": {department: executive_role}, "
        "\"manager_role_keywords\": [string], "
        "\"role_model_overrides\": {role: model}, "
        "\"default_management_model\": string, "
        "\"default_worker_model\": string, "
        "\"governance_rationale\": [string]"
        "}."
    )
    design_models = _ordered_design_models(management_models=management_models, worker_models=worker_models)
    last_error = ""
    for attempt, model in enumerate(design_models[:4], start=1):
        policy_path = design_dir / f"segmented_policy_attempt_{attempt:02d}.json"
        try:
            response = client.chat(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are the Workforce Runtime architecture design agent. Return only compact JSON policy.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1800,
                reasoning=None,
                stream=False,
                response_format={"type": "json_object"},
            )
            policy = extract_json_object(response.content)
            policy_path.write_text(json.dumps({"policy": policy, "usage": response.usage, "model": model}, indent=2))
            organization = _organization_from_design_policy(
                positions=positions,
                policy=policy,
                management_models=management_models,
                worker_models=worker_models,
            )
            _validate_designed_organization(organization, min_agents=max(3, int(max_agents * 0.9)))
            (design_dir / "organization.json").write_text(organization.model_dump_json(indent=2))
            return organization, {
                "source": "segmented_llm_org_design_agent",
                "model": model,
                "attempt": attempt,
                "agent_count": len(organization.agents),
                "design_dir": str(design_dir),
                "policy_path": str(policy_path),
                "governance_rationale": policy.get("governance_rationale", []),
                "usage": response.usage,
            }
        except Exception as exc:  # noqa: BLE001 - try the next policy model.
            last_error = str(exc)
            (design_dir / f"segmented_policy_attempt_{attempt:02d}_error.txt").write_text(last_error)
    raise RuntimeError(f"segmented LLM org design failed after {min(4, len(design_models))} attempts: {last_error}")


def _governor_manage_organization_design(
    *,
    client: RoutedLLMClient,
    organization: Organization,
    plan_path: Path,
    design_dir: Path,
    management_models: list[str],
    worker_models: list[str],
    requested_active_agent_limit: int,
) -> tuple[Organization, dict[str, Any]]:
    review = _governor_review_design(
        client=client,
        organization=organization,
        plan_path=plan_path,
        design_dir=design_dir,
        management_models=management_models,
        worker_models=worker_models,
        requested_active_agent_limit=requested_active_agent_limit,
        review_label="governor_design_review",
    )
    governed, applied, errors = _apply_governor_design_actions(
        organization,
        review=review,
        management_models=management_models,
        worker_models=worker_models,
    )
    review["applied_actions"] = applied
    review["action_errors"] = errors
    if applied and not errors:
        post_review = _governor_review_design(
            client=client,
            organization=governed,
            plan_path=plan_path,
            design_dir=design_dir,
            management_models=management_models,
            worker_models=worker_models,
            requested_active_agent_limit=_governor_active_agent_limit(review, default=requested_active_agent_limit),
            review_label="governor_post_action_review",
        )
        post_governed, post_applied, post_errors = _apply_governor_design_actions(
            governed,
            review=post_review,
            management_models=management_models,
            worker_models=worker_models,
        )
        post_review["pre_action_review"] = {
            "approved": bool(review.get("approved")),
            "summary": review.get("summary"),
            "applied_actions": applied,
            "action_errors": errors,
            "model": review.get("_model"),
        }
        post_review["applied_actions"] = post_applied
        post_review["action_errors"] = post_errors
        review = post_review
        governed = post_governed
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "governor_design_review.json").write_text(json.dumps(review, indent=2))
    (design_dir / "organization_after_governor.json").write_text(governed.model_dump_json(indent=2))
    return governed, review


def _governor_review_design(
    *,
    client: RoutedLLMClient,
    organization: Organization,
    plan_path: Path,
    design_dir: Path,
    management_models: list[str],
    worker_models: list[str],
    requested_active_agent_limit: int,
    review_label: str,
) -> dict[str, Any]:
    fallback = {
        "approved": False,
        "summary": "Governor review failed; continuing only if runner policy allows execution.",
        "risks": ["No governor LLM review was available."],
        "required_changes": [],
        "reporting_overrides": [],
        "model_overrides": {},
        "recommended_active_agent_limit": requested_active_agent_limit,
        "confidence": 0.2,
    }
    prompt = (
        "You are the Workforce Runtime governor. Review and manage this LLM-designed organization "
        "before execution of the Large Task 100 challenge. You may approve, reject, or request safe "
        "pre-execution adjustments to reporting lines, model routing, and active-agent limit. "
        "Use overrides only when they reduce concrete coordination risk.\n\n"
        "Return JSON only: {"
        "\"approved\": boolean, "
        "\"summary\": string, "
        "\"risks\": [string], "
        "\"required_changes\": [string], "
        "\"reporting_overrides\": [{\"agent_id\": string, \"manager_id\": string}], "
        "\"model_overrides\": {\"agent_id_or_role\": \"model\"}, "
        "\"recommended_active_agent_limit\": integer, "
        "\"confidence\": number"
        "}.\n\n"
        f"Plan path: {plan_path}\n"
        f"Agent count: {len(organization.agents)}\n"
        f"Requested active-agent limit: {requested_active_agent_limit}\n"
        f"Allowed management models: {management_models}\n"
        f"Allowed worker models: {worker_models}\n"
        f"Organization:\n{json.dumps(_compact_org_for_design_review(organization), indent=2)[:60000]}"
    )
    design_models = _ordered_design_models(management_models=management_models, worker_models=worker_models)
    last_error = ""
    for attempt, model in enumerate(design_models[:4], start=1):
        try:
            response = client.chat(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are the Workforce Runtime governor. Review org design risk, "
                            "return only JSON, and do not claim runtime results."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1100,
                reasoning=None,
                stream=True,
                response_format={"type": "json_object"},
            )
            payload = extract_json_object(response.content)
            payload["_model"] = model
            payload["_attempt"] = attempt
            payload["_usage"] = response.usage
            design_dir.mkdir(parents=True, exist_ok=True)
            (design_dir / f"{review_label}_attempt_{attempt:02d}.json").write_text(json.dumps(payload, indent=2))
            return payload
        except Exception as exc:  # noqa: BLE001 - preserve failure and try next configured model.
            last_error = str(exc)
            design_dir.mkdir(parents=True, exist_ok=True)
            (design_dir / f"{review_label}_attempt_{attempt:02d}_error.txt").write_text(last_error)
    payload = {**fallback, "error": last_error, "_model": design_models[0] if design_models else ""}
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / f"{review_label}.json").write_text(json.dumps(payload, indent=2))
    return payload


def _apply_governor_design_actions(
    organization: Organization,
    *,
    review: dict[str, Any],
    management_models: list[str],
    worker_models: list[str],
) -> tuple[Organization, list[dict[str, str]], list[str]]:
    applied: list[dict[str, str]] = []
    errors: list[str] = []
    governed = organization
    allowed_models = {*management_models, *worker_models}

    for target_ref, manager_ref in _iter_reporting_overrides(review.get("reporting_overrides")):
        target_id = _resolve_agent_ref(governed, target_ref)
        manager_id = _resolve_agent_ref(governed, manager_ref)
        if not target_id or not manager_id:
            errors.append(f"could not resolve reporting override {target_ref!r} -> {manager_ref!r}")
            continue
        target = governed.require_agent(target_id)
        if target.manager_id is None:
            errors.append(f"refused to move root agent {target_id}")
            continue
        manager_ids = {agent.manager_id for agent in governed.agents if agent.manager_id}
        direct_reports = {agent.id: governed.get_direct_reports(agent.id) for agent in governed.agents}
        manager_has_manager_reports = any(child.id in manager_ids for child in direct_reports.get(manager_id, []))
        if manager_has_manager_reports and target_id not in manager_ids:
            errors.append(f"refused to flatten leaf {target_id} under manager-of-managers {manager_id}")
            continue
        proposed = _replace_agent(governed, target_id, target.model_copy(update={"manager_id": manager_id}))
        try:
            _validate_designed_organization(proposed, min_agents=len(proposed.agents))
        except Exception as exc:  # noqa: BLE001 - keep the last valid governor-managed organization.
            errors.append(f"invalid reporting override {target_id} -> {manager_id}: {exc}")
            continue
        proposed_assessment = _runtime_validator_assessment(
            proposed,
            management_models=management_models,
            worker_models=worker_models,
        )
        if not proposed_assessment["ok"] or int(proposed_assessment["max_direct_reports"]) > 8:
            errors.append(f"unsafe reporting override {target_id} -> {manager_id}: {proposed_assessment}")
            continue
        governed = _refresh_system_prompts(proposed)
        applied.append({"type": "reporting_override", "agent_id": target_id, "manager_id": manager_id})

    model_overrides = review.get("model_overrides") if isinstance(review.get("model_overrides"), dict) else {}
    for target_ref, model in model_overrides.items():
        target_id = _resolve_agent_ref(governed, str(target_ref))
        model = str(model)
        if not target_id:
            errors.append(f"could not resolve model override target {target_ref!r}")
            continue
        manager_ids = {agent.manager_id for agent in governed.agents if agent.manager_id}
        target = governed.require_agent(target_id)
        role_allowed_models = management_models if target_id in manager_ids or target.manager_id is None else worker_models
        if model not in allowed_models or model not in role_allowed_models or not _model_is_configured(model):
            errors.append(f"refused unconfigured or disallowed model override for {target_id}: {model}")
            continue
        governed = _replace_agent(governed, target_id, target.model_copy(update={"model": model}))
        applied.append({"type": "model_override", "agent_id": target_id, "model": model})

    governed = _refresh_system_prompts(governed)
    return governed, applied, errors


def _iter_reporting_overrides(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [(str(target), str(manager)) for target, manager in value.items()]
    if not isinstance(value, list):
        return []
    overrides: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        target = item.get("agent_id") or item.get("role") or item.get("target") or item.get("position")
        manager = item.get("manager_id") or item.get("manager_role") or item.get("reports_to")
        if target and manager:
            overrides.append((str(target), str(manager)))
    return overrides


def _resolve_agent_ref(organization: Organization, value: str) -> str | None:
    if not value:
        return None
    if organization.find_agent(value):
        return value
    normalized = _normalize_role(value)
    normalized_by_id = {_normalize_role(agent.id): agent.id for agent in organization.agents}
    if normalized in normalized_by_id:
        return normalized_by_id[normalized]
    normalized_by_role = {_normalize_role(agent.role): agent.id for agent in organization.agents}
    if normalized in normalized_by_role:
        return normalized_by_role[normalized]
    terms = set(normalized.split())
    best_id = None
    best_score = 0
    for agent in organization.agents:
        role_terms = set(_normalize_role(agent.role).split())
        score = len(terms & role_terms)
        if score > best_score:
            best_score = score
            best_id = agent.id
    return best_id if best_score >= 2 else None


def _replace_agent(organization: Organization, agent_id: str, replacement: AgentProfile) -> Organization:
    return Organization(
        company=organization.company,
        agents=[replacement if agent.id == agent_id else agent for agent in organization.agents],
    )


def _refresh_system_prompts(organization: Organization) -> Organization:
    return Organization(
        company=organization.company,
        agents=[
            agent.model_copy(update={"system_prompt": generate_system_prompt(organization.company, agent)})
            for agent in organization.agents
        ],
    )


def _governor_active_agent_limit(review: dict[str, Any], *, default: int) -> int:
    value = review.get("recommended_active_agent_limit")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, default))


def _governor_allows_execution(review: dict[str, Any]) -> bool:
    if not bool(review.get("approved")):
        return False
    return not bool(review.get("action_errors"))


def _runtime_validator_assessment(
    organization: Organization,
    *,
    management_models: list[str],
    worker_models: list[str],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        _validate_designed_organization(organization, min_agents=len(organization.agents))
    except Exception as exc:  # noqa: BLE001 - export validator state into the trace.
        errors.append(str(exc))

    direct_reports: dict[str, list[AgentProfile]] = {}
    for agent in organization.agents:
        if agent.manager_id:
            direct_reports.setdefault(agent.manager_id, []).append(agent)
    max_span = max((len(items) for items in direct_reports.values()), default=0)
    max_depth = max((len(organization.get_reporting_chain(agent.id)) for agent in organization.agents), default=0)
    if max_span > 10:
        errors.append(f"max direct reports {max_span} exceeds hard limit 10")
    elif max_span > 8:
        warnings.append(f"max direct reports {max_span} exceeds preferred limit 8")
    if max_depth > 6:
        errors.append(f"max reporting depth {max_depth} exceeds hard limit 6")

    manager_ids = {agent.manager_id for agent in organization.agents if agent.manager_id}
    manager_model_violations = [
        agent.id
        for agent in organization.agents
        if (agent.id in manager_ids or agent.manager_id is None) and agent.model not in management_models
    ]
    worker_model_violations = [
        agent.id
        for agent in organization.agents
        if agent.id not in manager_ids and agent.manager_id is not None and agent.model not in worker_models
    ]
    if manager_model_violations:
        errors.append(f"manager model violations: {manager_model_violations[:10]}")
    if worker_model_violations:
        errors.append(f"worker model violations: {worker_model_violations[:10]}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "max_direct_reports": max_span,
        "max_reporting_depth": max_depth,
        "manager_count": len(manager_ids),
        "worker_count": len(organization.agents) - len(manager_ids),
    }


def _format_governor_feedback(review: dict[str, Any]) -> str:
    feedback = {
        "approved": bool(review.get("approved")),
        "summary": review.get("summary"),
        "risks": review.get("risks", []),
        "required_changes": review.get("required_changes", []),
        "applied_actions": review.get("applied_actions", []),
        "action_errors": review.get("action_errors", []),
        "runtime_validator": review.get("runtime_validator", {}),
    }
    return json.dumps(feedback, indent=2)[:4000]


def _org_design_agent_prompt(
    *,
    request: OrgDesignRequest,
    positions: str,
    management_models: list[str],
    worker_models: list[str],
    attempt: int,
    last_error: str,
    governor_feedback: str = "",
) -> str:
    retry_note = f"\nPrevious validation error: {last_error}\nFix it in this attempt.\n" if last_error else ""
    governor_note = (
        f"\nGovernor feedback from a rejected prior design:\n{governor_feedback}\nAddress these issues before returning JSON.\n"
        if governor_feedback
        else ""
    )
    return f"""
Design the Workforce Runtime organization for the OpenForge 100-role challenge.

Use the challenge position list as required role coverage, but you decide the reporting structure, worker_type, model routing, responsibilities, and permissions.
Target exactly {request.headcount_limit} agents unless validation constraints force a small deviation.

Available management models:
{management_models}

Available worker models:
{worker_models}

Position list:
{positions}

Return JSON only:
{{
  "company": {{
    "name": string,
    "mission": string,
    "headcount_limit": integer,
    "token_budget": integer
  }},
  "agents": [
    {{
      "id": "stable_snake_case_id",
      "role": string,
      "department": string,
      "manager_id": string or null,
      "worker_type": "openrouter_manager" or "openrouter_worker",
      "model": string,
      "permissions": [string]
    }}
  ]
}}

Validation constraints:
- Include exactly one root CEO/top-level agent with manager_id null.
- Include close to {request.headcount_limit} agents and at least {int(request.headcount_limit * 0.9)}.
- Every non-root manager_id must reference an existing id.
- Any agent with direct reports must have delegate_task and report.
- The root CEO must have delegate_task, report, and report_to_human.
- Every agent must have report and submit_artifact, because every role must produce an auditable contribution.
- Use only the available models listed above.
- Keep reporting depth at or below 6 levels including the CEO.
- Prefer feature/project coordination where useful; do not simply mirror a flat list if better governance requires squads.
{retry_note}
{governor_note}
""".strip()


def _normalize_designed_organization(
    organization: Organization,
    *,
    max_agents: int,
    management_models: list[str],
    worker_models: list[str],
) -> Organization:
    agents = organization.agents[:max_agents]
    reports_by_manager: dict[str, list[AgentProfile]] = {}
    for agent in agents:
        if agent.manager_id:
            reports_by_manager.setdefault(agent.manager_id, []).append(agent)
    root_seen = False
    normalized: list[AgentProfile] = []
    allowed_models = {*management_models, *worker_models}
    for index, agent in enumerate(agents):
        is_root = agent.manager_id is None and not root_seen
        if agent.manager_id is None:
            root_seen = True
        has_reports = bool(reports_by_manager.get(agent.id))
        permissions = list(dict.fromkeys([*agent.permissions, REPORT, SUBMIT_ARTIFACT, READ_REPO]))
        if has_reports or is_root:
            permissions = list(dict.fromkeys([*permissions, DELEGATE_TASK]))
        if is_root:
            permissions = list(dict.fromkeys([*permissions, REPORT_TO_HUMAN]))
        model_pool = management_models if has_reports or is_root else worker_models
        model = agent.model if agent.model in allowed_models and _model_is_configured(agent.model) else model_pool[index % len(model_pool)]
        worker_type = "openrouter_manager" if has_reports or is_root else "openrouter_worker"
        normalized.append(
            agent.model_copy(
                update={
                    "model": model,
                    "permissions": permissions,
                    "worker_type": worker_type,
                    "responsibilities": agent.responsibilities or _responsibilities_for(
                        PositionSpec(number=index + 1, role=agent.role, department=agent.department)
                    ),
                    "system_prompt": "",
                }
            )
        )
    org = organization.model_copy(update={"agents": normalized})
    org = org.model_copy(
        update={"agents": [agent.model_copy(update={"system_prompt": generate_system_prompt(org.company, agent)}) for agent in org.agents]}
    )
    return org


def _validate_designed_organization(organization: Organization, *, min_agents: int) -> None:
    if len(organization.agents) < min_agents:
        raise ValueError(f"designed organization has {len(organization.agents)} agents, expected at least {min_agents}")
    roots = [agent for agent in organization.agents if agent.manager_id is None]
    if len(roots) != 1:
        raise ValueError(f"designed organization must have exactly one root, found {len(roots)}")
    ids = {agent.id for agent in organization.agents}
    missing = [agent.id for agent in organization.agents if agent.manager_id and agent.manager_id not in ids]
    if missing:
        raise ValueError(f"agents have missing managers: {missing[:10]}")
    if REPORT_TO_HUMAN not in roots[0].permissions:
        raise ValueError("root CEO is missing report_to_human")
    attempted_depth = max((len(organization.get_reporting_chain(agent.id)) for agent in organization.agents), default=0)
    if attempted_depth > 6:
        raise ValueError(f"designed organization depth {attempted_depth} exceeds limit 6")


def _compact_org_for_design_review(organization: Organization) -> dict[str, Any]:
    return {
        "company": organization.company.model_dump(mode="json"),
        "agents": [
            {
                "id": agent.id,
                "role": agent.role,
                "department": agent.department,
                "manager_id": agent.manager_id,
                "worker_type": agent.worker_type,
                "model": agent.model,
                "permissions": agent.permissions,
                "responsibilities": agent.responsibilities[:4],
            }
            for agent in organization.agents
        ],
    }


def _organization_from_design_policy(
    *,
    positions: list[PositionSpec],
    policy: dict[str, Any],
    management_models: list[str],
    worker_models: list[str],
) -> Organization:
    role_to_position = {position.role: position for position in positions}
    root = next((position for position in positions if "chief executive officer" in position.role.lower()), positions[0])
    department_leads = {
        str(department): str(role)
        for department, role in (policy.get("department_leads") or {}).items()
        if isinstance(policy.get("department_leads"), dict)
    }
    executive_alignment = {
        str(department): str(role)
        for department, role in (policy.get("executive_alignment") or {}).items()
        if isinstance(policy.get("executive_alignment"), dict)
    }
    manager_keywords = [str(item).lower() for item in policy.get("manager_role_keywords") or []]
    if not manager_keywords:
        manager_keywords = ["chief", "head", "lead", "director", "manager", "architect"]
    role_model_overrides = {
        (_resolve_role(str(role), role_to_position) or str(role)): str(model)
        for role, model in (policy.get("role_model_overrides") or {}).items()
        if isinstance(policy.get("role_model_overrides"), dict)
    }
    default_management_model = _valid_model_or_default(str(policy.get("default_management_model") or ""), management_models[0])
    default_worker_model = _valid_model_or_default(str(policy.get("default_worker_model") or ""), worker_models[0])
    manager_by_role: dict[str, str | None] = {root.role: None}
    departments = sorted({position.department for position in positions})
    for department in departments:
        department_positions = [position for position in positions if position.department == department]
        if department == root.department:
            for position in department_positions:
                if position.role == root.role:
                    continue
                manager_by_role[position.role] = _executive_manager_for_role(position, role_to_position, root.role)
            continue
        lead_role = _resolve_role(department_leads.get(department, ""), role_to_position)
        if lead_role not in role_to_position or role_to_position[lead_role].department != department:
            lead_role = _default_department_lead(department_positions, manager_keywords).role
        executive_role = _resolve_role(executive_alignment.get(department, ""), role_to_position)
        if executive_role == lead_role:
            executive_role = None
        if executive_role not in role_to_position:
            executive_role = _default_executive_for_department(department, role_to_position) or root.role
        if (
            ("security" in department.lower() or "privacy" in department.lower() or "compliance" in department.lower())
            and "Chief Risk and Governance Officer" in role_to_position
        ):
            executive_role = "Chief Risk and Governance Officer"
        if "finance" in department.lower():
            finance_manager = _default_executive_for_department(department, role_to_position) or executive_role or root.role
            for position in department_positions:
                manager_by_role[position.role] = finance_manager
            continue
        manager_by_role[lead_role] = executive_role
        submanagers = _department_submanagers(department_positions, lead_role=lead_role, manager_keywords=manager_keywords)
        if len(department_positions) > 8 and submanagers:
            for position in submanagers:
                manager_by_role[position.role] = lead_role
            for index, position in enumerate(department_positions):
                if position.role == lead_role or position.role in {item.role for item in submanagers}:
                    continue
                manager_by_role[position.role] = _select_department_submanager(position, submanagers, index=index).role
        else:
            for position in department_positions:
                if position.role == lead_role:
                    continue
                manager_by_role[position.role] = lead_role

    ids_by_role = {position.role: _agent_id(position) for position in positions}
    direct_report_roles: dict[str, list[str]] = {}
    for role, manager_role in manager_by_role.items():
        if manager_role:
            direct_report_roles.setdefault(manager_role, []).append(role)

    company = Company(
        name=str(policy.get("company_name") or "OpenForge 100-Role Launch Workforce"),
        mission=str(
            policy.get("mission")
            or "Deliver OpenForge public beta while observing and improving the 100-role organization."
        ),
        headcount_limit=len(positions),
        token_budget=25_000_000,
    )
    agents: list[AgentProfile] = []
    for index, position in enumerate(positions):
        manager_role = manager_by_role.get(position.role)
        has_reports = bool(direct_report_roles.get(position.role))
        is_root = position.role == root.role
        model = role_model_overrides.get(position.role)
        role_model_pool = management_models if has_reports or is_root else worker_models
        if not role_model_pool:
            role_model_pool = [default_management_model if has_reports or is_root else default_worker_model]
        if model not in role_model_pool:
            model = role_model_pool[index % len(role_model_pool)]
        permissions = [READ_REPO, SUBMIT_ARTIFACT, REPORT]
        if has_reports or is_root:
            permissions.append(DELEGATE_TASK)
        if is_root:
            permissions.append(REPORT_TO_HUMAN)
        agent = AgentProfile(
            id=ids_by_role[position.role],
            name=f"{position.role} Agent",
            role=position.role,
            department=position.department,
            manager_id=ids_by_role.get(manager_role or ""),
            worker_type="openrouter_manager" if has_reports or is_root else "openrouter_worker",
            model=model,
            responsibilities=_responsibilities_for(position),
            permissions=list(dict.fromkeys(permissions)),
            budget=Budget(
                max_tokens=350_000 if has_reports or is_root else 180_000,
                max_runtime_seconds=7200 if has_reports or is_root else 3600,
                max_tool_calls=120 if has_reports or is_root else 60,
            ),
        )
        agent.system_prompt = generate_system_prompt(company, agent)
        agents.append(agent)
    return Organization(company=company, agents=agents)


def _executive_manager_for_role(position: PositionSpec, role_to_position: dict[str, PositionSpec], root_role: str) -> str:
    lowered = position.role.lower()
    if any(
        term in lowered
        for term in (
            "chief financial officer",
            "chief of staff",
            "portfolio management",
        )
    ):
        if "Chief Operating Officer" in role_to_position:
            return "Chief Operating Officer"
    return root_role


def _department_submanagers(
    positions: list[PositionSpec],
    *,
    lead_role: str,
    manager_keywords: list[str],
) -> list[PositionSpec]:
    result: list[PositionSpec] = []
    for position in positions:
        if position.role == lead_role:
            continue
        lowered = position.role.lower()
        if _role_matches_keywords(position.role, manager_keywords) or any(
            term in lowered for term in ("principal", "architect", "lead", "director", "manager")
        ):
            result.append(position)
    if not result and len(positions) > 8:
        result = [position for position in positions if position.role != lead_role][::3]
    return result[:8]


def _select_department_submanager(position: PositionSpec, submanagers: list[PositionSpec], *, index: int) -> PositionSpec:
    lowered = position.role.lower()
    preferred_terms: list[str] = []
    if "backend" in lowered or any(term in lowered for term in ("authentication", "tenant", "repository")):
        preferred_terms = ["backend"]
    elif "frontend" in lowered or any(term in lowered for term in ("dashboard", "administration", "onboarding", "review experience")):
        preferred_terms = ["frontend"]
    elif any(term in lowered for term in ("research", "interview", "competitive", "intelligence")):
        preferred_terms = ["research", "ux"]
    elif any(term in lowered for term in ("roadmap", "pricing", "packaging")):
        preferred_terms = ["product", "growth", "enterprise"]
    elif any(term in lowered for term in ("sdk", "cli", "mcp", "integration", "migration", "compatibility")):
        preferred_terms = ["integration", "runtime"]
    elif any(term in lowered for term in ("data", "search", "retrieval", "pipeline", "analytics")):
        preferred_terms = ["data"]
    elif any(term in lowered for term in ("runtime", "orchestration", "event processing")):
        preferred_terms = ["runtime"]
    elif any(term in lowered for term in ("observability", "incident", "chaos", "reliability")):
        preferred_terms = ["reliability", "sre", "site"]

    for term in preferred_terms:
        for candidate in submanagers:
            if term in candidate.role.lower():
                return candidate

    position_terms = set(_normalize_role(position.role).split())
    best = submanagers[0]
    best_score = 0
    for candidate in submanagers:
        candidate_terms = set(_normalize_role(candidate.role).split())
        score = len(position_terms & candidate_terms)
        if score > best_score:
            best = candidate
            best_score = score
    if best_score:
        return best
    return submanagers[index % len(submanagers)]


def _default_department_lead(positions: list[PositionSpec], manager_keywords: list[str]) -> PositionSpec:
    for position in positions:
        if _role_matches_keywords(position.role, manager_keywords):
            return position
    return positions[0]


def _resolve_role(value: str, role_to_position: dict[str, PositionSpec]) -> str | None:
    if not value:
        return None
    if value in role_to_position:
        return value
    normalized = _normalize_role(value)
    normalized_lookup = {_normalize_role(role): role for role in role_to_position}
    if normalized in normalized_lookup:
        return normalized_lookup[normalized]
    aliases = {
        "ceo": "Chief Executive Officer",
        "coo": "Chief Operating Officer",
        "cto": "Chief Technology Officer",
        "cpo": "Chief Product Officer",
        "cfo": "Chief Financial Officer",
        "ciso": "Security Architect",
        "cro": "Growth Lead",
        "cmo": "Growth Lead",
        "chief architect": "Chief Software Architect",
        "chief data scientist": "AI Systems Lead",
        "chief information security officer": "Security Architect",
        "head of platform engineering": "Platform Engineering Lead",
        "head of quality assurance": "Quality Engineering Director",
        "head of developer experience": "Design Director",
        "chief revenue officer": "Growth Lead",
    }
    alias = aliases.get(normalized)
    if alias in role_to_position:
        return alias
    value_terms = set(normalized.split())
    best_role = None
    best_score = 0
    for role in role_to_position:
        role_terms = set(_normalize_role(role).split())
        score = len(value_terms & role_terms)
        if score > best_score:
            best_score = score
            best_role = role
    return best_role if best_score >= 2 else None


def _normalize_role(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _default_executive_for_department(department: str, role_to_position: dict[str, PositionSpec]) -> str | None:
    lowered = department.lower()
    candidates: list[str]
    if "product" in lowered or "design" in lowered or "growth" in lowered:
        candidates = ["Chief Product Officer", "Chief Operating Officer", "Chief Executive Officer"]
    elif "engineering" in lowered or "ai" in lowered or "platform" in lowered:
        candidates = ["Chief Technology Officer", "Chief Operating Officer", "Chief Executive Officer"]
    elif "security" in lowered or "privacy" in lowered or "compliance" in lowered:
        candidates = ["Chief Risk and Governance Officer", "Chief Executive Officer"]
    elif "quality" in lowered or "reliability" in lowered:
        candidates = ["Chief Operating Officer", "Chief Technology Officer", "Chief Executive Officer"]
    elif "finance" in lowered or "operations" in lowered:
        candidates = ["Chief Financial Officer", "Chief Operating Officer", "Chief Executive Officer"]
    else:
        candidates = ["Chief Operating Officer", "Chief Executive Officer"]
    for role in candidates:
        if role in role_to_position:
            return role
    return None


def _role_matches_keywords(role: str, keywords: list[str]) -> bool:
    lowered = role.lower()
    return any(keyword and keyword in lowered for keyword in keywords)


def _valid_model_or_default(model: str, default: str) -> str:
    return model if model and model_capabilities(model) is not None and _model_is_configured(model) else default


def _run_agent_contribution(
    *,
    runtime: WorkforceRuntime,
    client: RoutedLLMClient,
    agent: AgentProfile,
    task_id: str,
    root_task_id: str,
    child_agents: list[AgentProfile],
    workspace: Path,
    llm_json_config: dict[str, Any] | None,
) -> dict[str, Any]:
    child_roles = [f"{child.id}: {child.role}" for child in child_agents]
    fallback = {
        "summary": f"{agent.role} attempted its OpenForge work packet but structured LLM output was unavailable.",
        "work_done": [f"Reviewed responsibility area: {agent.role}", "Recorded fallback failure context"],
        "risks": ["LLM output failed or could not be parsed; downstream work may be low quality."],
        "blockers": [],
        "child_priorities": child_roles[:8],
        "human_message": "",
        "confidence": 0.25,
        "next_action": "Inspect the agent run error and retry with a different model if necessary.",
        "requires_decision": False,
    }
    return _run_agent_json_with_model_failover(
        runtime=runtime,
        client=client,
        actor_id=agent.id,
        task_id=task_id,
        model_candidates=_model_candidates_for_agent(agent),
        system=(
            f"You are {agent.name}, the {agent.role} in the OpenForge launch workforce. "
            "Return only JSON. Be concise, evidence-aware, and explicit about risks."
        ),
        user=(
            f"Root task id: {root_task_id}\n"
            f"Your task id: {task_id}\n"
            f"Department: {agent.department}\n"
            f"Responsibilities: {agent.responsibilities}\n"
            f"Direct reports to delegate to: {child_roles}\n"
            "OpenForge mission: build and operate a multi-tenant AI code review and repository intelligence platform. "
            "This is a compressed validation run, so produce realistic planning/execution output, not a claim that the full product is already shipped.\n\n"
            "Return JSON: {"
            "\"summary\": string, "
            "\"work_done\": [string], "
            "\"risks\": [string], "
            "\"blockers\": [string], "
            "\"child_priorities\": [string], "
            "\"human_message\": string, "
            "\"confidence\": number, "
            "\"next_action\": string, "
            "\"requires_decision\": boolean"
            "}."
        ),
        fallback=fallback,
        max_tokens=900,
        workspace=workspace,
        retry_config=llm_json_config,
    )


def _run_ceo_final_report(
    *,
    runtime: WorkforceRuntime,
    client: RoutedLLMClient,
    ceo: AgentProfile,
    task_id: str,
    workspace: Path,
    agent_payloads: dict[str, dict[str, Any]],
    failed_agents: list[dict[str, str]],
    llm_json_config: dict[str, Any] | None,
) -> dict[str, Any]:
    compact_payloads = [
        {
            "agent_id": agent_id,
            "summary": payload.get("summary"),
            "risks": payload.get("risks", [])[:3] if isinstance(payload.get("risks"), list) else [],
            "error": payload.get("_runtime_error", ""),
        }
        for agent_id, payload in list(agent_payloads.items())[:120]
    ]
    fallback = {
        "summary": "The 100-agent OpenForge run completed orchestration, but CEO final LLM synthesis failed.",
        "human_message": (
            f"Completed orchestration for {len(agent_payloads)} agents. "
            f"Failed LLM agents: {len(failed_agents)}. Review trace and artifacts for details."
        ),
        "organizational_findings": ["Inspect failed LLM calls and manager-review events."],
        "next_action": "Retry failed agents with alternate models or reduce concurrency/token pressure.",
        "confidence": 0.35,
        "requires_decision": True,
    }
    return _run_agent_json_with_model_failover(
        runtime=runtime,
        client=client,
        actor_id=ceo.id,
        task_id=task_id,
        model_candidates=_model_candidates_for_agent(ceo),
        system="You are the CEO. Return only JSON for the final report to the human operator.",
        user=(
            f"Agent attempts: {len(agent_payloads)}\n"
            f"Failed agents: {json.dumps(failed_agents[:25], indent=2)}\n"
            f"Compact agent outputs: {json.dumps(compact_payloads, indent=2)[:50000]}\n\n"
            "Return JSON: {"
            "\"summary\": string, "
            "\"human_message\": string, "
            "\"organizational_findings\": [string], "
            "\"next_action\": string, "
            "\"confidence\": number, "
            "\"requires_decision\": boolean"
            "}."
        ),
        fallback=fallback,
        max_tokens=1200,
        workspace=workspace,
        retry_config=llm_json_config,
    )


def _run_agent_json_with_model_failover(
    *,
    runtime: WorkforceRuntime,
    client: RoutedLLMClient,
    actor_id: str,
    task_id: str,
    model_candidates: list[str],
    system: str,
    user: str,
    fallback: dict[str, Any],
    max_tokens: int,
    workspace: Path,
    retry_config: dict[str, Any] | None,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    candidates = [model for model in model_candidates if _model_is_configured(model)]
    if not candidates:
        candidates = model_candidates or ["openai/gpt-oss-120b:free"]
    index = 0
    while index < len(candidates):
        model = candidates[index]
        index += 1
        per_model_retry = dict(retry_config or {})
        per_model_retry["max_retries"] = min(int(per_model_retry.get("max_retries", 0) or 0), 1)
        payload = _run_agent_json(
            runtime=runtime,
            client=client,
            actor_id=actor_id,
            task_id=task_id,
            model=model,
            system=system,
            user=user,
            fallback=dict(fallback),
            max_tokens=max_tokens,
            workspace=workspace,
            retry_config=per_model_retry,
        )
        payload["_runtime_model"] = model
        error = str(payload.get("_runtime_error") or "")
        if not error:
            if errors:
                payload["_runtime_failover_errors"] = errors
            return payload
        replacement = runtime.auto_replace_unavailable_agent_model(
            agent_id=actor_id,
            failed_model=model,
            error=error,
            task_id=task_id,
            actor_id="model_failover",
        )
        if replacement is not None and replacement.model and replacement.model not in candidates[index:]:
            candidates.insert(index, replacement.model)
        errors.append({"model": model, "error": error[:500]})
        runtime.record_event(
            event_type="agent_model_failover",
            actor_id=actor_id,
            task_id=task_id,
            payload={
                "failed_model": model,
                "error": error[:1000],
                "next_model_index": len(errors),
                "replacement_model": replacement.model if replacement is not None else "",
            },
        )
    failed = dict(fallback)
    failed["_runtime_error"] = errors[-1]["error"] if errors else "no configured model candidates"
    failed["_runtime_failover_errors"] = errors
    failed["_runtime_model"] = candidates[-1] if candidates else ""
    return failed


def _model_candidates_for_agent(agent: AgentProfile) -> list[str]:
    if agent.manager_id is None:
        candidates = [agent.model or "", *DEFAULT_MANAGEMENT_MODELS, *DEFAULT_WORKER_MODELS]
    elif DELEGATE_TASK in agent.permissions:
        candidates = [agent.model or "", *DEFAULT_MANAGEMENT_MODELS, *DEFAULT_WORKER_MODELS]
    else:
        candidates = [agent.model or "", *DEFAULT_WORKER_MODELS, *DEFAULT_MANAGEMENT_MODELS]
    result: list[str] = []
    for model in candidates:
        if model and model not in result:
            result.append(model)
    return result


def _write_agent_artifact(workspace: Path, *, agent: AgentProfile, task_id: str, payload: dict[str, Any]) -> Path:
    artifact_dir = workspace / "artifacts" / task_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{agent.id}.md"
    lines = [
        f"# {agent.role}",
        "",
        f"Agent: {agent.id}",
        f"Model: {payload.get('_runtime_model') or agent.model}",
        f"Task: {task_id}",
        "",
        "## Summary",
        str(payload.get("summary") or ""),
        "",
        "## Work Done",
        *[f"- {item}" for item in _list_of_strings(payload.get("work_done"))],
        "",
        "## Risks",
        *[f"- {item}" for item in _list_of_strings(payload.get("risks"))],
        "",
        "## Blockers",
        *[f"- {item}" for item in _list_of_strings(payload.get("blockers"))],
    ]
    if payload.get("_runtime_error"):
        lines.extend(["", "## LLM Error", str(payload["_runtime_error"])])
    path.write_text("\n".join(lines) + "\n")
    return path


def _write_final_report(workspace: Path, payload: dict[str, Any], *, failed_agents: list[dict[str, str]]) -> Path:
    path = workspace / "final_report.md"
    findings = _list_of_strings(payload.get("organizational_findings"))
    lines = [
        "# OpenForge 100-Agent Run Final Report",
        "",
        "## Human Message",
        str(payload.get("human_message") or payload.get("summary") or ""),
        "",
        "## Organizational Findings",
        *[f"- {item}" for item in findings],
        "",
        "## Failed Agents",
        *[
            f"- {item.get('agent_id')}: {item.get('role')} ({item.get('model')}) - {item.get('error')}"
            for item in failed_agents[:50]
        ],
        "",
        "## Next Action",
        str(payload.get("next_action") or ""),
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def _report_args(*, agent: AgentProfile, task_id: str, payload: dict[str, Any], artifact_path: Path) -> dict[str, object]:
    usage = payload.get("_runtime_usage") if isinstance(payload.get("_runtime_usage"), dict) else {}
    failed = bool(payload.get("_runtime_error"))
    return {
        "from_agent_id": agent.id,
        "task_id": task_id,
        "summary": str(payload.get("summary") or ""),
        "status": "failed" if failed else "completed",
        "work_done": _list_of_strings(payload.get("work_done")),
        "evidence": [{"type": "agent_artifact", "path": str(artifact_path)}],
        "risks": _list_of_strings(payload.get("risks")),
        "blockers": _list_of_strings(payload.get("blockers")),
        "confidence": float(payload.get("confidence") or (0.25 if failed else 0.7)),
        "cost": {"tokens_used": _usage_token_count(usage), "runtime_seconds": 0, "tool_calls": 2},
        "next_action": str(payload.get("next_action") or ""),
        "requires_decision": bool(payload.get("requires_decision", False)),
        "alignment_check": "Agent attempted real LLM work for the OpenForge Large Task 100 run.",
    }


def _build_result(
    *,
    runtime: WorkforceRuntime,
    db: Path,
    workdir: Path,
    plan_path: Path,
    root_task_id: str,
    trace_path: Path,
    task_trace_path: Path,
    v2_analysis_path: str,
    summary_path: Path,
    failed_agents: list[dict[str, str]],
) -> LargeTask100Result:
    events = runtime.store.list_events()
    agents = runtime.store.list_agents()
    model_counts: dict[str, int] = {}
    for agent in agents:
        model_counts[agent.model or ""] = model_counts.get(agent.model or "", 0) + 1
    attempted = {
        event.actor_id
        for event in events
        if event.event_type == "agent_run_started" and event.actor_id not in {"benchmark_judge"}
    }
    completed = {
        event.actor_id
        for event in events
        if event.event_type == "agent_run_finished" and event.payload.get("status") == "completed"
    }
    human_reports = [event for event in events if event.event_type == "human_report_registered"]
    return LargeTask100Result(
        ok=len(attempted) >= len(agents) and bool(human_reports),
        db_path=str(db),
        workspace=str(workdir),
        plan_path=str(plan_path),
        root_task_id=root_task_id,
        agent_count=len(agents),
        llm_attempted_agent_count=len(attempted),
        llm_completed_agent_count=len(completed),
        llm_failed_agent_count=len(failed_agents),
        task_count=len(runtime.store.list_tasks()),
        report_count=len(runtime.store.list_reports()),
        artifact_count=len(runtime.store.list_artifacts()),
        human_report_count=len(human_reports),
        model_counts=model_counts,
        failed_agents=failed_agents,
        trace_path=str(trace_path),
        task_trace_path=str(task_trace_path),
        v2_analysis_path=v2_analysis_path,
        summary_path=str(summary_path),
    )


def _root_agent(organization: Organization) -> AgentProfile:
    roots = [agent for agent in organization.agents if agent.manager_id is None]
    if len(roots) != 1:
        raise ValueError(f"organization must have exactly one root agent, found {len(roots)}")
    return roots[0]


def _model_is_configured(model: str) -> bool:
    provider = str((model_capabilities(model) or {}).get("provider") or "openrouter")
    if provider == "nvidia":
        import os

        return bool(os.getenv("NVIDIA_API_KEY"))
    if provider == "openrouter":
        import os

        return bool(os.getenv("OPENROUTER_API_KEY"))
    return False


def _ordered_design_models(*, management_models: list[str], worker_models: list[str]) -> list[str]:
    candidates = [*DEFAULT_DESIGN_MODELS, *management_models, *worker_models]
    ordered: list[str] = []
    for model in candidates:
        if model in ordered:
            continue
        if _model_is_configured(model):
            ordered.append(model)
    if ordered:
        return ordered
    return list(dict.fromkeys(candidates))


def _manager_map(positions: list[PositionSpec]) -> dict[int, int | None]:
    numbers = {position.number for position in positions}
    mapping: dict[int, int | None] = {}
    for position in positions:
        number = position.number
        if number == 1:
            mapping[number] = None
        elif number <= 8:
            mapping[number] = 1
        elif 9 <= number <= 18:
            mapping[number] = 4 if number == 9 else 9
        elif 19 <= number <= 40:
            mapping[number] = 3 if number == 19 else 19
        elif 41 <= number <= 50:
            mapping[number] = 3 if number == 41 else 41
        elif 51 <= number <= 60:
            mapping[number] = 2 if number == 51 else 51
        elif 61 <= number <= 70:
            mapping[number] = 6 if number == 61 else 61
        elif 71 <= number <= 78:
            mapping[number] = 3 if number == 71 else 71
        elif 79 <= number <= 86:
            mapping[number] = 4 if number == 79 else 79
        elif 87 <= number <= 94:
            mapping[number] = 4 if number == 87 else 87
        elif 95 <= number <= 100:
            mapping[number] = 5 if number == 95 else 95
        else:
            mapping[number] = 1
        if mapping[number] not in numbers:
            mapping[number] = 1 if number != 1 and 1 in numbers else None
    return mapping


def _direct_reports(positions: list[PositionSpec], manager_by_number: dict[int, int | None]) -> dict[int, list[int]]:
    reports: dict[int, list[int]] = {}
    for number, manager in manager_by_number.items():
        if manager is not None:
            reports.setdefault(manager, []).append(number)
    return reports


def _agents_by_manager(organization: Organization) -> dict[str, list[AgentProfile]]:
    result: dict[str, list[AgentProfile]] = {}
    for agent in organization.agents:
        if agent.manager_id:
            result.setdefault(agent.manager_id, []).append(agent)
    for items in result.values():
        items.sort(key=lambda agent: agent.id)
    return result


def _breadth_first_agents(organization: Organization) -> list[AgentProfile]:
    roots = sorted([agent for agent in organization.agents if agent.manager_id is None], key=lambda agent: agent.id)
    by_manager = _agents_by_manager(organization)
    ordered: list[AgentProfile] = []
    queue = list(roots)
    while queue:
        agent = queue.pop(0)
        ordered.append(agent)
        queue.extend(by_manager.get(agent.id, []))
    return ordered


def _position_by_number(positions: list[PositionSpec], number: int | None) -> PositionSpec:
    if number is None:
        raise ValueError("position number is required")
    for position in positions:
        if position.number == number:
            return position
    raise KeyError(f"position not found: {number}")


def _agent_id(position: PositionSpec) -> str:
    return re.sub(r"[^a-z0-9]+", "_", position.role.lower()).strip("_")


def _responsibilities_for(position: PositionSpec) -> list[str]:
    role = position.role.lower()
    base = [f"Own the {position.role} workstream for OpenForge", "Report evidence, risks, and blockers"]
    if any(term in role for term in ("chief", "head", "lead", "director", "manager")):
        base.append("Coordinate dependent roles and preserve decision traceability")
    if "security" in role or "risk" in role or "privacy" in role:
        base.append("Protect tenant isolation, secrets, and release gates")
    if "engineer" in role or "architect" in role:
        base.append("Translate requirements into implementation-ready technical decisions")
    if "product" in role or "growth" in role or "customer" in role:
        base.append("Preserve customer value and launch-readiness evidence")
    return base


def _child_assignment_message(*, parent: AgentProfile, child: AgentProfile, payload: dict[str, Any]) -> str:
    priorities = _list_of_strings(payload.get("child_priorities"))
    priority_text = "\n".join(f"- {item}" for item in priorities[:12]) or "- Preserve the OpenForge launch objective."
    return (
        f"{parent.role} delegated this OpenForge work packet to {child.role}.\n"
        f"Parent summary: {payload.get('summary') or ''}\n"
        f"Relevant child priorities:\n{priority_text}\n"
        "Attempt realistic compressed work, submit an artifact, and report risks/blockers."
    )


def _root_objective(plan_path: Path) -> str:
    text = plan_path.read_text()
    mission_match = re.search(r"## 2\. Mission\n\n(.+?)\n\n---", text, flags=re.S)
    objective_match = re.search(r"# 3\. V2 Test Objective\n\n(.+?)\n\n---", text, flags=re.S)
    mission = mission_match.group(1).strip() if mission_match else "OpenForge public beta launch challenge."
    objective = objective_match.group(1).strip() if objective_match else ""
    return f"{mission}\n\nV2 objective excerpt:\n{objective[:4000]}"


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


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
