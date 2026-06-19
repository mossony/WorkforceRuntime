from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from workforce_runtime.core import (
    AgentProfile,
    Artifact,
    Budget,
    Event,
    Organization,
    ReportContract,
    TaskContract,
    TaskStatus,
    generate_system_prompt,
)
from workforce_runtime.core.permissions import DELEGATE_TASK, REPORT, SUBMIT_ARTIFACT, Capability
from workforce_runtime.scheduler.manager_review import ManagerReviewDecision, ManagerReviewPolicy
from workforce_runtime.storage import SQLiteStore, load_org_from_yaml


class WorkforceRuntime:
    def __init__(self, db_path: str | Path = ".workforce_runtime/runtime.sqlite") -> None:
        self.db_path = Path(db_path)
        self.store = SQLiteStore(self.db_path)
        self.manager_review_policy = ManagerReviewPolicy()

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> WorkforceRuntime:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def initialize_org(self, org_path: str | Path) -> Organization:
        organization = load_org_from_yaml(org_path)
        self.store.save_company(organization.company)
        for agent in organization.agents:
            self.store.save_agent(agent)
        self.record_event(
            event_type="org_initialized",
            actor_id="system",
            payload={"org_path": str(org_path), "agent_count": len(organization.agents)},
        )
        return organization

    def get_agent(self, agent_id: str) -> AgentProfile | None:
        return self.store.get_agent(agent_id)

    def create_task(
        self,
        *,
        title: str,
        objective: str,
        assign_to: str | None = None,
        assigned_by: str = "human",
        parent_task_id: str | None = None,
        root_goal_id: str | None = None,
        context_refs: list[str] | None = None,
        constraints: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
        required_artifacts: list[str] | None = None,
    ) -> TaskContract:
        if assign_to is not None and self.store.get_agent(assign_to) is None:
            raise ValueError(f"cannot assign task to unknown agent: {assign_to}")
        if assign_to is not None:
            self.require_permission(assigned_by, DELEGATE_TASK)
            self.require_manager_of(assigned_by, assign_to)

        task = TaskContract(
            task_id=self._next_task_id(),
            title=title,
            objective=objective,
            assigned_to=assign_to,
            assigned_by=assigned_by,
            parent_task_id=parent_task_id,
            root_goal_id=root_goal_id,
            context_refs=context_refs or [],
            constraints=constraints or [],
            acceptance_criteria=acceptance_criteria or [],
            required_artifacts=required_artifacts or [],
            status="assigned" if assign_to else "created",
        )
        self.store.save_task(task)
        self.record_event(
            event_type="task_created",
            actor_id=assigned_by,
            task_id=task.task_id,
            payload={"title": title, "assigned_to": assign_to},
        )
        if assign_to:
            self._mark_agent_assigned(assign_to, task.task_id)
            self.record_event(
                event_type="task_assigned",
                actor_id=assigned_by,
                task_id=task.task_id,
                payload={"assigned_to": assign_to},
            )
        return task

    def assign_task(self, task_id: str, *, assign_to: str, assigned_by: str = "human") -> TaskContract:
        task = self.require_task(task_id)
        if self.store.get_agent(assign_to) is None:
            raise ValueError(f"cannot assign task to unknown agent: {assign_to}")
        self.require_permission(assigned_by, DELEGATE_TASK, task_id=task_id)
        self.require_manager_of(assigned_by, assign_to, task_id=task_id)
        task.assigned_to = assign_to
        task.assigned_by = assigned_by
        task.status = "assigned"
        self.store.save_task(task)
        self._mark_agent_assigned(assign_to, task.task_id)
        self.record_event(
            event_type="task_assigned",
            actor_id=assigned_by,
            task_id=task.task_id,
            payload={"assigned_to": assign_to},
        )
        return task

    def update_task_status(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        actor_id: str,
    ) -> TaskContract:
        task = self.require_task(task_id)
        updated = task.model_copy(update={"status": status})
        self.store.save_task(updated)
        if status in {"completed", "failed", "cancelled"} and updated.assigned_to:
            self._mark_agent_released(updated.assigned_to, task_id)
        elif status in {"assigned", "in_progress"} and updated.assigned_to:
            self._mark_agent_assigned(updated.assigned_to, task_id)
        self.record_event(
            event_type="task_status_updated",
            actor_id=actor_id,
            task_id=task_id,
            payload={"status": status},
        )
        return updated

    def require_task(self, task_id: str) -> TaskContract:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    def list_tasks(self) -> list[TaskContract]:
        return self.store.list_tasks()

    def register_report(self, report: ReportContract) -> None:
        self.require_permission(report.from_agent_id, REPORT, task_id=report.task_id)
        expected_recipient = self.get_report_recipient(report.from_agent_id)
        if report.to_agent_id != expected_recipient:
            raise ValueError(
                f"reports from {report.from_agent_id} must go to direct manager {expected_recipient}"
            )
        self.store.save_report(report)
        self.record_event(
            event_type="report_registered",
            actor_id=report.from_agent_id,
            task_id=report.task_id,
            payload={"report_id": report.report_id, "status": report.status},
        )
        task = self.store.get_task(report.task_id)
        if task is not None:
            self._record_budget_overrun_if_needed(task, report)
        if task is not None and not self._is_review_task(task):
            self._create_and_run_manager_review(task, report)

    def register_artifact(self, artifact: Artifact) -> None:
        self.require_permission(artifact.agent_id, SUBMIT_ARTIFACT, task_id=artifact.task_id)
        self.store.save_artifact(artifact)
        self.record_event(
            event_type="artifact_registered",
            actor_id=artifact.agent_id,
            task_id=artifact.task_id,
            payload={"artifact_id": artifact.artifact_id, "type": artifact.type},
        )

    def record_event(
        self,
        *,
        event_type: str,
        actor_id: str,
        task_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> Event:
        event = Event(
            event_id=f"event_{uuid4().hex[:12]}",
            event_type=event_type,
            actor_id=actor_id,
            task_id=task_id,
            payload=payload or {},
        )
        self.store.save_event(event)
        return event

    def record_worker_run_started(
        self,
        *,
        run_id: str,
        task_id: str,
        actor_id: str,
        executable: str,
    ) -> Event:
        return self.record_event(
            event_type="worker_run_started",
            actor_id=actor_id,
            task_id=task_id,
            payload={"run_id": run_id, "executable": executable},
        )

    def record_worker_output(
        self,
        *,
        run_id: str,
        task_id: str,
        actor_id: str,
        stream: str,
        text: str,
    ) -> Event:
        return self.record_event(
            event_type="worker_output",
            actor_id=actor_id,
            task_id=task_id,
            payload={"run_id": run_id, "stream": stream, "text": _clip_event_text(text)},
        )

    def record_worker_run_finished(
        self,
        *,
        run_id: str,
        task_id: str,
        actor_id: str,
        returncode: int,
        timed_out: bool = False,
    ) -> Event:
        return self.record_event(
            event_type="worker_run_finished",
            actor_id=actor_id,
            task_id=task_id,
            payload={"run_id": run_id, "returncode": returncode, "timed_out": timed_out},
        )

    def record_agent_run_started(
        self,
        *,
        run_id: str,
        task_id: str | None,
        actor_id: str,
        adapter: str,
        model: str = "",
    ) -> Event:
        return self.record_event(
            event_type="agent_run_started",
            actor_id=actor_id,
            task_id=task_id,
            payload={"run_id": run_id, "adapter": adapter, "model": model},
        )

    def record_agent_output(
        self,
        *,
        run_id: str,
        task_id: str | None,
        actor_id: str,
        stream: str,
        text: str,
    ) -> Event:
        return self.record_event(
            event_type="agent_output",
            actor_id=actor_id,
            task_id=task_id,
            payload={"run_id": run_id, "stream": stream, "text": _clip_event_text(text)},
        )

    def record_agent_run_finished(
        self,
        *,
        run_id: str,
        task_id: str | None,
        actor_id: str,
        status: str,
        usage: dict[str, object] | None = None,
        error: str = "",
    ) -> Event:
        payload: dict[str, object] = {"run_id": run_id, "status": status}
        if usage:
            payload["usage"] = usage
        if error:
            payload["error"] = _clip_event_text(error)
        return self.record_event(
            event_type="agent_run_finished",
            actor_id=actor_id,
            task_id=task_id,
            payload=payload,
        )

    def get_report_recipient(self, agent_id: str) -> str:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            if agent_id in {"human", "system", "runtime"}:
                return "human"
            raise KeyError(f"agent not found: {agent_id}")
        return agent.manager_id or "human"

    def require_manager_of(
        self,
        manager_id: str | None,
        agent_id: str,
        *,
        task_id: str | None = None,
    ) -> None:
        if manager_id in {"human", "system", "runtime"}:
            return
        if manager_id is not None and self.is_manager_of(manager_id, agent_id):
            return
        self.record_event(
            event_type="permission_violation",
            actor_id=manager_id or "unknown",
            task_id=task_id,
            payload={"capability": DELEGATE_TASK, "managed_agent_id": agent_id},
        )
        raise PermissionError(f"agent {manager_id} does not manage {agent_id}")

    def is_manager_of(self, manager_id: str, agent_id: str) -> bool:
        current = self.store.get_agent(agent_id)
        seen: set[str] = set()
        while current is not None and current.manager_id is not None:
            if current.manager_id == manager_id:
                return True
            if current.manager_id in seen:
                return False
            seen.add(current.manager_id)
            current = self.store.get_agent(current.manager_id)
        return False

    def send_discussion_message(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        message: str,
        task_id: str | None = None,
        thread_id: str | None = None,
    ) -> Event:
        if from_agent_id not in {"human", "system", "runtime"} and self.store.get_agent(from_agent_id) is None:
            raise KeyError(f"agent not found: {from_agent_id}")
        if to_agent_id not in {"human", "system", "runtime"} and self.store.get_agent(to_agent_id) is None:
            raise KeyError(f"agent not found: {to_agent_id}")
        return self.record_event(
            event_type="discussion_message",
            actor_id=from_agent_id,
            task_id=task_id,
            payload={"to_agent_id": to_agent_id, "message": message, "thread_id": thread_id},
        )

    def check_progress(
        self,
        *,
        manager_id: str,
        target_agent_id: str,
        message: str = "",
        task_id: str | None = None,
    ) -> dict[str, object]:
        target = self.store.get_agent(target_agent_id)
        if target is None:
            raise KeyError(f"agent not found: {target_agent_id}")
        self.require_manager_of(manager_id, target_agent_id, task_id=task_id)

        active_tasks = [
            task
            for task in self.store.list_tasks()
            if task.assigned_to == target_agent_id and task.status in {"assigned", "in_progress", "blocked"}
        ]
        relevant_reports = [
            report
            for report in self.store.list_reports()
            if report.from_agent_id == target_agent_id or report.to_agent_id == target_agent_id
        ][-5:]
        relevant_events = [
            event
            for event in self.store.list_events()
            if event.actor_id == target_agent_id
            or event.payload.get("assigned_to") == target_agent_id
            or event.payload.get("to_agent_id") == target_agent_id
        ][-10:]

        event = self.record_event(
            event_type="progress_check_requested",
            actor_id=manager_id,
            task_id=task_id,
            payload={
                "target_agent_id": target_agent_id,
                "message": message,
                "active_task_ids": [task.task_id for task in active_tasks],
            },
        )
        return {
            "event_id": event.event_id,
            "target_agent": target.model_dump(mode="json"),
            "active_tasks": [task.model_dump(mode="json") for task in active_tasks],
            "recent_reports": [report.model_dump(mode="json") for report in relevant_reports],
            "recent_events": [event.model_dump(mode="json") for event in relevant_events],
        }

    def hire_agent(
        self,
        *,
        requested_by: str,
        agent_id: str,
        name: str,
        role: str,
        department: str,
        manager_id: str,
        worker_type: str,
        responsibilities: list[str] | None = None,
        permissions: list[str] | None = None,
        budget: Budget | None = None,
        system_prompt: str | None = None,
    ) -> AgentProfile:
        self.require_permission(requested_by, "hire_agent")
        if self.store.get_agent(agent_id) is not None:
            raise ValueError(f"agent already exists: {agent_id}")
        if self.store.get_agent(manager_id) is None:
            raise ValueError(f"manager not found: {manager_id}")

        company = self.store.get_company()
        existing_agents = self.store.list_agents()
        if company is not None and company.headcount_limit > 0 and len(existing_agents) + 1 > company.headcount_limit:
            self.record_event(
                event_type="hire_rejected",
                actor_id=requested_by,
                payload={
                    "agent_id": agent_id,
                    "reason": "headcount_limit",
                    "headcount_limit": company.headcount_limit,
                },
            )
            raise ValueError("cannot hire agent: headcount limit exceeded")

        candidate_budget = budget or Budget()
        if company is not None and company.token_budget > 0:
            allocated_tokens = sum(agent.budget.max_tokens for agent in existing_agents)
            if allocated_tokens + candidate_budget.max_tokens > company.token_budget:
                self.record_event(
                    event_type="hire_rejected",
                    actor_id=requested_by,
                    payload={
                        "agent_id": agent_id,
                        "reason": "token_budget",
                        "token_budget": company.token_budget,
                        "allocated_tokens": allocated_tokens,
                        "requested_tokens": candidate_budget.max_tokens,
                    },
                )
                raise ValueError("cannot hire agent: token budget exceeded")

        agent = AgentProfile(
            id=agent_id,
            name=name,
            role=role,
            department=department,
            manager_id=manager_id,
            worker_type=worker_type,
            responsibilities=responsibilities or [],
            permissions=permissions or [],
            budget=candidate_budget,
            system_prompt=system_prompt or "",
        )
        if not agent.system_prompt.strip():
            if company is None:
                raise ValueError("cannot generate system prompt without company metadata")
            agent.system_prompt = generate_system_prompt(company, agent)

        self.store.save_agent(agent)
        self.record_event(
            event_type="agent_hired",
            actor_id=requested_by,
            payload={"agent_id": agent.id, "manager_id": manager_id, "worker_type": worker_type},
        )
        return agent

    def update_system_prompt(
        self,
        *,
        actor_id: str,
        target_agent_id: str,
        system_prompt: str,
    ) -> AgentProfile:
        target = self.store.get_agent(target_agent_id)
        if target is None:
            raise KeyError(f"agent not found: {target_agent_id}")
        self.require_manager_of(actor_id, target_agent_id)
        updated = target.model_copy(update={"system_prompt": system_prompt})
        self.store.save_agent(updated)
        self.record_event(
            event_type="system_prompt_updated",
            actor_id=actor_id,
            payload={"target_agent_id": target_agent_id},
        )
        return updated

    def record_budget_violation(
        self,
        *,
        task_id: str,
        actor_id: str,
        reason: str,
        usage: dict[str, int] | None = None,
    ) -> Event:
        return self.record_event(
            event_type="budget_violation",
            actor_id=actor_id,
            task_id=task_id,
            payload={"reason": reason, "usage": usage or {}},
        )

    def require_permission(
        self,
        agent_id: str,
        capability: Capability,
        *,
        task_id: str | None = None,
    ) -> None:
        if agent_id in {"human", "system", "runtime"}:
            return
        agent = self.store.get_agent(agent_id)
        if agent is not None and agent.has_permission(capability):
            return
        self.record_event(
            event_type="permission_violation",
            actor_id=agent_id,
            task_id=task_id,
            payload={"capability": capability},
        )
        raise PermissionError(f"agent {agent_id} lacks permission: {capability}")

    def _next_task_id(self) -> str:
        existing = self.store.list_tasks()
        return f"task_{len(existing) + 1:03d}"

    def review_report(
        self,
        report_id: str,
        *,
        reviewer_id: str,
        decision: str | None = None,
        notes: str = "",
    ) -> TaskContract:
        report = self.store.get_report(report_id)
        if report is None:
            raise KeyError(f"report not found: {report_id}")

        review_decision = decision or self._default_review_decision(report)
        if review_decision == "accept":
            task_status: TaskStatus = "completed"
        elif review_decision == "reject":
            task_status = "failed"
        elif review_decision == "request_retry":
            task_status = "assigned"
        elif review_decision in {"escalate", "request_human_review"}:
            task_status = "blocked"
        else:
            raise ValueError(f"unsupported review decision: {review_decision}")

        task = self.update_task_status(report.task_id, status=task_status, actor_id=reviewer_id)
        self.record_event(
            event_type=f"manager_review_{review_decision}",
            actor_id=reviewer_id,
            task_id=report.task_id,
            payload={"report_id": report_id, "notes": notes},
        )

        for review_task in self.list_tasks():
            if (
                review_task.parent_task_id == report.task_id
                and review_task.context_refs == [f"report:{report_id}"]
                and review_task.status in {"created", "assigned", "in_progress"}
            ):
                self.update_task_status(review_task.task_id, status="completed", actor_id=reviewer_id)

        return task

    def _create_manager_review_task(self, report: ReportContract) -> TaskContract | None:
        if report.to_agent_id == report.from_agent_id or self.store.get_agent(report.to_agent_id) is None:
            return None

        for task in self.list_tasks():
            if task.parent_task_id == report.task_id and task.context_refs == [f"report:{report.report_id}"]:
                return task

        source_task = self.require_task(report.task_id)
        return self.create_task(
            title=f"Review {source_task.title}",
            objective=(
                f"Review report {report.report_id} for task {report.task_id}. "
                "Accept, reject, request retry, escalate, or request human review."
            ),
            assign_to=report.to_agent_id,
            assigned_by="runtime",
            parent_task_id=report.task_id,
            root_goal_id=source_task.root_goal_id or source_task.task_id,
            context_refs=[f"report:{report.report_id}"],
            constraints=[
                "Inspect worker report",
                "Inspect submitted artifacts",
                "Compare against acceptance criteria",
                "Check budget usage, risks, and blockers",
            ],
            acceptance_criteria=[
                "Review decision is recorded",
                "Original task status is updated",
            ],
            required_artifacts=[],
        )

    def _default_review_decision(self, report: ReportContract) -> str:
        if report.blockers:
            return "request_retry"
        if report.status not in {"completed", "success", "done"}:
            return "reject"
        if report.confidence < 0.5:
            return "request_human_review"
        return "accept"

    def _create_and_run_manager_review(self, task: TaskContract, report: ReportContract) -> TaskContract:
        review_task = TaskContract(
            task_id=self._next_task_id(),
            title=f"Review report {report.report_id} for {task.task_id}",
            objective=f"Review worker output for task {task.task_id}.",
            assigned_to=report.to_agent_id,
            assigned_by="system",
            parent_task_id=task.task_id,
            root_goal_id=task.root_goal_id or task.task_id,
            context_refs=[f"report:{report.report_id}", f"task:{task.task_id}"],
            acceptance_criteria=[
                "Inspect worker report",
                "Check acceptance criteria, risks, blockers, evidence, and budget usage",
                "Record accept, reject, retry, escalate, or human-review decision",
            ],
            status="assigned",
        )
        self.store.save_task(review_task)
        self._mark_agent_assigned(report.to_agent_id, review_task.task_id)
        self.record_event(
            event_type="manager_review_created",
            actor_id="system",
            task_id=review_task.task_id,
            payload={"report_id": report.report_id, "reviewed_task_id": task.task_id},
        )

        decision = self.manager_review_policy.decide(task=task, report=report)
        self._apply_manager_review_decision(review_task, task, report, decision)
        return review_task

    def _apply_manager_review_decision(
        self,
        review_task: TaskContract,
        task: TaskContract,
        report: ReportContract,
        decision: ManagerReviewDecision,
    ) -> None:
        if decision.action == "accept":
            final_status: TaskStatus = "completed"
        elif decision.action == "request_human_review":
            final_status = "blocked"
        else:
            final_status = "failed"

        reviewed_task = task.model_copy(update={"status": final_status})
        self.store.save_task(reviewed_task)
        if reviewed_task.assigned_to and final_status in {"completed", "failed", "cancelled"}:
            self._mark_agent_released(reviewed_task.assigned_to, reviewed_task.task_id)

        completed_review = review_task.model_copy(update={"status": "completed"})
        self.store.save_task(completed_review)
        if completed_review.assigned_to:
            self._mark_agent_released(completed_review.assigned_to, completed_review.task_id)

        self.record_event(
            event_type="manager_review_decided",
            actor_id=report.to_agent_id,
            task_id=review_task.task_id,
            payload={
                "reviewed_task_id": task.task_id,
                "report_id": report.report_id,
                "decision": decision.action,
                "reason": decision.reason,
                "accepted": decision.accepted,
                "final_task_status": final_status,
            },
        )

    def _is_review_task(self, task: TaskContract) -> bool:
        return any(ref.startswith("report:") for ref in task.context_refs)

    def _record_budget_overrun_if_needed(self, task: TaskContract, report: ReportContract) -> None:
        violations: list[str] = []
        if task.budget.max_tokens > 0 and report.cost.tokens_used > task.budget.max_tokens:
            violations.append("tokens")
        if task.budget.max_runtime_seconds > 0 and report.cost.runtime_seconds > task.budget.max_runtime_seconds:
            violations.append("runtime_seconds")
        if task.budget.max_tool_calls > 0 and report.cost.tool_calls > task.budget.max_tool_calls:
            violations.append("tool_calls")
        if not violations:
            return

        self.record_budget_violation(
            task_id=task.task_id,
            actor_id=report.from_agent_id,
            reason="report usage exceeded task budget: " + ", ".join(violations),
            usage={
                "tokens_used": report.cost.tokens_used,
                "runtime_seconds": report.cost.runtime_seconds,
                "tool_calls": report.cost.tool_calls,
            },
        )

    def _mark_agent_assigned(self, agent_id: str, task_id: str) -> None:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            return
        if task_id not in agent.current_task_ids:
            agent.current_task_ids.append(task_id)
        if agent.status == "idle":
            agent.status = "busy"
        self.store.save_agent(agent)

    def _mark_agent_released(self, agent_id: str, task_id: str) -> None:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            return
        agent.current_task_ids = [existing for existing in agent.current_task_ids if existing != task_id]
        if not agent.current_task_ids and agent.status == "busy":
            agent.status = "idle"
        self.store.save_agent(agent)


def _clip_event_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...[truncated]..."
