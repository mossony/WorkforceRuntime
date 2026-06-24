from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from workforce_runtime.core import (
    AgentExperience,
    AgentPersonalProfile,
    AgentProfile,
    Artifact,
    Budget,
    Event,
    Organization,
    ReportContract,
    TaskDocument,
    TaskContract,
    TaskStatus,
    WorkItem,
    WorkItemKind,
    WorkQueuePolicy,
    generate_system_prompt,
)
from workforce_runtime.core.permissions import DELEGATE_TASK, REPORT, REPORT_TO_HUMAN, SUBMIT_ARTIFACT, Capability
from workforce_runtime.config.model_failover import choose_agent_replacement_model, is_unavailable_model_error
from workforce_runtime.config.runtime_config import load_runtime_config
from workforce_runtime.scheduler.manager_review import ManagerReviewDecision, ManagerReviewPolicy
from workforce_runtime.storage import RuntimeStore, RuntimeStoreFactory, load_org_from_yaml, runtime_store_factory


class WorkforceRuntime:
    def __init__(
        self,
        db_path: str | Path = ".workforce_runtime/runtime.sqlite",
        *,
        store: RuntimeStore | None = None,
        store_factory: RuntimeStoreFactory | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._owns_store = store is None
        self.store = store or (store_factory or runtime_store_factory("sqlite"))(self.db_path)
        self.manager_review_policy = ManagerReviewPolicy()

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> WorkforceRuntime:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def initialize_org(self, org_path: str | Path) -> Organization:
        organization = load_org_from_yaml(org_path)
        return self.initialize_organization(organization, source=str(org_path))

    def initialize_organization(self, organization: Organization, *, source: str = "direct") -> Organization:
        self.store.save_company(organization.company)
        for agent in organization.agents:
            self.store.save_agent(agent)
            self._ensure_agent_personal_profile(agent.id, updated_by="system")
        self.record_event(
            event_type="org_initialized",
            actor_id="system",
            payload={"org_path": source, "agent_count": len(organization.agents)},
        )
        return organization

    def get_agent(self, agent_id: str) -> AgentProfile | None:
        return self.store.get_agent(agent_id)

    def auto_replace_unavailable_agent_model(
        self,
        *,
        agent_id: str,
        failed_model: str,
        error: object,
        task_id: str | None = None,
        actor_id: str = "runtime",
    ) -> AgentProfile | None:
        if not bool(load_runtime_config().get("model_failover", {}).get("enabled", True)):
            return None
        if not is_unavailable_model_error(error):
            return None
        agent = self.store.get_agent(agent_id)
        company = self.store.get_company()
        if agent is None or company is None:
            return None
        if agent.model != failed_model:
            return agent
        replacement = choose_agent_replacement_model(agent, failed_model=failed_model)
        if not replacement:
            self.record_event(
                event_type="agent_model_auto_replace_failed",
                actor_id=actor_id,
                task_id=task_id,
                payload={"agent_id": agent_id, "failed_model": failed_model, "error": str(error)[:1000]},
            )
            return None
        updated = agent.model_copy(update={"model": replacement})
        updated = updated.model_copy(update={"system_prompt": generate_system_prompt(company, updated)})
        self.store.save_agent(updated)
        self.record_event(
            event_type="agent_model_auto_replaced",
            actor_id=actor_id,
            task_id=task_id,
            payload={
                "agent_id": agent_id,
                "old_model": failed_model,
                "new_model": replacement,
                "error": str(error)[:1000],
            },
        )
        return updated

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
            task_id=self._next_task_id(title),
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
        if status in {"completed", "failed", "cancelled"}:
            self._refresh_related_task_trace_exports(task_id)
        return updated

    def require_task(self, task_id: str) -> TaskContract:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    def list_tasks(self) -> list[TaskContract]:
        return self.store.list_tasks()

    def upsert_task_document(
        self,
        *,
        actor_id: str,
        task_id: str,
        title: str,
        content: str,
        doc_type: str = "note",
        doc_id: str | None = None,
    ) -> TaskDocument:
        self.require_task_access(actor_id, task_id)
        existing = self.store.get_task_document(doc_id) if doc_id else None
        if existing is not None and existing.task_id != task_id:
            raise ValueError(f"document {doc_id} belongs to another task")

        values = {
            "doc_id": doc_id or f"doc_{uuid4().hex[:12]}",
            "task_id": task_id,
            "title": title,
            "doc_type": doc_type,
            "content": content,
            "created_by": existing.created_by if existing is not None else actor_id,
            "updated_by": actor_id,
            "version": (existing.version + 1) if existing is not None else 1,
        }
        if existing is not None:
            values["created_at"] = existing.created_at
        document = TaskDocument.model_validate(values)

        self.store.save_task_document(document)
        self.record_event(
            event_type="task_document_upserted",
            actor_id=actor_id,
            task_id=task_id,
            payload={
                "doc_id": document.doc_id,
                "doc_type": document.doc_type,
                "title": title,
                "version": document.version,
            },
        )
        self._refresh_related_task_trace_exports(task_id)
        return document

    def list_task_documents(self, task_id: str, *, actor_id: str = "runtime") -> list[TaskDocument]:
        self.require_task_access(actor_id, task_id)
        return self.store.list_task_documents_by_task(task_id)

    def get_task_dossier(
        self,
        *,
        actor_id: str,
        task_id: str,
        include_events: bool = True,
        event_limit: int = 20,
    ) -> dict[str, object]:
        self.require_task_access(actor_id, task_id)
        task = self.require_task(task_id)
        child_tasks = [item for item in self.store.list_tasks() if item.parent_task_id == task_id]
        documents = self.store.list_task_documents_by_task(task_id)
        reports = self.store.list_reports_by_task(task_id)
        artifacts = self.store.list_artifacts_by_task(task_id)
        events = [
            event
            for event in self.store.list_events()
            if event.task_id == task_id
            or event.payload.get("task_id") == task_id
            or event.payload.get("parent_task_id") == task_id
        ]
        return {
            "ok": True,
            "task": task.model_dump(mode="json"),
            "requirements": {
                "constraints": task.constraints,
                "acceptance_criteria": task.acceptance_criteria,
                "required_artifacts": task.required_artifacts,
            },
            "division_of_work": [
                {
                    "task_id": item.task_id,
                    "title": item.title,
                    "assigned_to": item.assigned_to,
                    "assigned_by": item.assigned_by,
                    "status": item.status,
                }
                for item in child_tasks
            ],
            "documents": [document.model_dump(mode="json") for document in documents],
            "reports": [report.model_dump(mode="json") for report in reports],
            "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
            "recent_events": [event.model_dump(mode="json") for event in events[-event_limit:]] if include_events else [],
        }

    def register_report(self, report: ReportContract) -> None:
        self.require_permission(report.from_agent_id, REPORT, task_id=report.task_id)
        expected_recipient = self.get_report_recipient(report.from_agent_id)
        if report.to_agent_id != expected_recipient:
            raise ValueError(
                f"reports from {report.from_agent_id} must go to direct manager {expected_recipient}"
            )
        self.store.save_report(report)
        if report.status.lower() in {"completed", "done", "success", "succeeded"}:
            self._auto_update_agent_personal_profile_from_report(report)
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
        self._refresh_related_task_trace_exports(report.task_id)

    def _ensure_agent_personal_profile(self, agent_id: str, *, updated_by: str) -> AgentPersonalProfile:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise KeyError(f"agent not found: {agent_id}")
        existing = self.store.get_agent_personal_profile(agent_id)
        if existing is not None:
            return existing
        profile = AgentPersonalProfile(
            agent_id=agent_id,
            summary=agent.performance_summary,
            knows_about=[],
            can_do=list(agent.responsibilities),
            specialty_tags=_profile_tags_from_text(" ".join([agent.role, agent.department, *agent.responsibilities])),
            preferred_tools=[],
            boundaries=[],
            updated_by=updated_by,
        )
        self.store.save_agent_personal_profile(profile)
        return profile

    def require_agent_profile_access(self, actor_id: str, agent_id: str) -> None:
        if actor_id in {"human", "system", "runtime"}:
            return
        if actor_id == agent_id:
            return
        if self.is_manager_of(actor_id, agent_id):
            return
        self.record_event(
            event_type="permission_violation",
            actor_id=actor_id,
            payload={"capability": "agent_profile_access", "target_agent_id": agent_id},
        )
        raise PermissionError(f"agent {actor_id} cannot access profile for {agent_id}")

    def _auto_update_agent_personal_profile_from_report(self, report: ReportContract) -> None:
        task = self.store.get_task(report.task_id)
        title = task.title if task is not None else ""
        objective = task.objective if task is not None else ""
        skills = _profile_tags_from_text(" ".join([title, objective, report.summary, *report.work_done]))
        evidence = [
            str(item.get("path"))
            for item in report.evidence
            if isinstance(item, dict) and item.get("path")
        ][:6]
        experience = AgentExperience(
            task_id=report.task_id,
            title=title,
            summary=report.summary,
            outcome=report.status,
            skills=skills,
            evidence=evidence,
            confidence=report.confidence,
        )
        knows_about = [title, objective, *report.work_done]
        can_do = [report.summary, *report.work_done]
        profile = self.update_agent_personal_profile(
            actor_id="runtime",
            agent_id=report.from_agent_id,
            summary=f"Recent completed work: {report.summary}",
            knows_about=knows_about,
            can_do=can_do,
            specialty_tags=skills,
            experience=experience,
        )
        agent = self.store.get_agent(report.from_agent_id)
        if agent is not None:
            updated_agent = agent.model_copy(update={"performance_summary": profile.summary or report.summary})
            self.store.save_agent(updated_agent)

    def report_to_human(
        self,
        *,
        from_agent_id: str,
        message: str,
        task_id: str | None = None,
        title: str = "",
        status: str = "",
        confidence: float | None = None,
        next_action: str = "",
        requires_decision: bool = False,
    ) -> Event:
        self.require_permission(from_agent_id, REPORT_TO_HUMAN, task_id=task_id)
        agent = self.store.get_agent(from_agent_id)
        if agent is None:
            raise KeyError(f"agent not found: {from_agent_id}")
        if agent.manager_id is not None:
            self.record_event(
                event_type="permission_violation",
                actor_id=from_agent_id,
                task_id=task_id,
                payload={"capability": REPORT_TO_HUMAN, "required_role": "ceo"},
            )
            raise PermissionError("report_to_human is reserved for the CEO or top-level agent")
        event = self.record_event(
            event_type="human_report_registered",
            actor_id=from_agent_id,
            task_id=task_id,
            payload={
                "human_report_id": f"human_report_{uuid4().hex[:12]}",
                "title": _clip_event_text(title or "CEO report to human"),
                "message": _clip_event_text(message),
                "status": _clip_event_text(status),
                "confidence": confidence,
                "next_action": _clip_event_text(next_action),
                "requires_decision": requires_decision,
            },
        )
        if task_id is not None:
            self._refresh_related_task_trace_exports(task_id)
        return event

    def register_artifact(self, artifact: Artifact) -> None:
        self.require_permission(artifact.agent_id, SUBMIT_ARTIFACT, task_id=artifact.task_id)
        self.store.save_artifact(artifact)
        self.record_event(
            event_type="artifact_registered",
            actor_id=artifact.agent_id,
            task_id=artifact.task_id,
            payload={"artifact_id": artifact.artifact_id, "type": artifact.type},
        )
        self._refresh_related_task_trace_exports(artifact.task_id)

    def enqueue_work_item(
        self,
        *,
        actor_id: str,
        agent_id: str,
        kind: WorkItemKind,
        task_id: str | None = None,
        payload: dict[str, object] | None = None,
        priority: int = 0,
        model: str = "",
        tool_name: str = "",
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> WorkItem:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise KeyError(f"agent not found: {agent_id}")
        if task_id is not None:
            self.require_task_access(actor_id, task_id)
        if idempotency_key:
            existing = self.store.get_work_item_by_idempotency_key(idempotency_key)
            if existing is not None and not existing.is_terminal():
                self.record_event(
                    event_type="work_item_deduplicated",
                    actor_id=actor_id,
                    task_id=task_id,
                    payload={"work_item_id": existing.work_item_id, "idempotency_key": idempotency_key},
                )
                return existing

        item_payload = payload or {}
        resolved_model = model or (agent.model if kind in {"llm_request", "worker_run"} else "")
        resolved_tool = tool_name or str(item_payload.get("tool_name") or "")
        now = _utc_now()
        item = WorkItem(
            work_item_id=f"work_{uuid4().hex[:12]}",
            kind=kind,
            agent_id=agent_id,
            task_id=task_id,
            payload=item_payload,
            priority=priority,
            model=resolved_model,
            tool_name=resolved_tool,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
            created_at=now,
            updated_at=now,
        )
        self.store.save_work_item(item)
        self.record_event(
            event_type="work_item_enqueued",
            actor_id=actor_id,
            task_id=task_id,
            payload=_work_item_event_payload(item),
        )
        return item

    def claim_work_items(
        self,
        *,
        lease_owner: str,
        limit: int = 1,
        policy: WorkQueuePolicy | dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> list[WorkItem]:
        if limit <= 0:
            return []
        queue_policy = _queue_policy(policy)
        effective_now = now or _utc_now()
        self.release_expired_work_item_leases(now=effective_now)

        items = self.store.list_work_items()
        active = [item for item in items if item.is_active(effective_now)]
        active_agents = {item.agent_id for item in active}
        active_kind_counts = _queue_counts(active, "kind")
        active_model_counts = _queue_counts(active, "model")
        active_tool_counts = _queue_counts(active, "tool_name")

        claimed: list[WorkItem] = []
        queued = sorted(
            [item for item in items if item.status == "queued"],
            key=lambda item: (-item.priority, item.created_at, item.work_item_id),
        )
        for item in queued:
            if len(claimed) >= limit:
                break
            if item.attempts >= item.max_attempts:
                failed = item.model_copy(
                    update={
                        "status": "failed",
                        "error": "max attempts exhausted before claim",
                        "updated_at": effective_now,
                    }
                )
                self.store.save_work_item(failed)
                self.record_event(
                    event_type="work_item_failed",
                    actor_id=lease_owner,
                    task_id=failed.task_id,
                    payload={**_work_item_event_payload(failed), "error": failed.error},
                )
                continue
            if not queue_policy.allow_same_agent_parallel and item.agent_id in active_agents:
                continue
            if item.agent_id not in active_agents and len(active_agents) >= queue_policy.max_active_agents:
                continue
            if not _under_limit(active_kind_counts.get(item.kind, 0), queue_policy.per_kind_limits.get(item.kind, 0)):
                continue
            if item.model and not _under_limit(active_model_counts.get(item.model, 0), queue_policy.per_model_limits.get(item.model, 0)):
                continue
            if item.tool_name and not _under_limit(active_tool_counts.get(item.tool_name, 0), queue_policy.per_tool_limits.get(item.tool_name, 0)):
                continue

            leased = item.model_copy(
                update={
                    "status": "leased",
                    "lease_owner": lease_owner,
                    "leased_at": effective_now,
                    "lease_until": effective_now + timedelta(seconds=queue_policy.lease_seconds),
                    "attempts": item.attempts + 1,
                    "updated_at": effective_now,
                }
            )
            self.store.save_work_item(leased)
            claimed.append(leased)
            active.append(leased)
            active_agents.add(leased.agent_id)
            active_kind_counts[leased.kind] = active_kind_counts.get(leased.kind, 0) + 1
            if leased.model:
                active_model_counts[leased.model] = active_model_counts.get(leased.model, 0) + 1
            if leased.tool_name:
                active_tool_counts[leased.tool_name] = active_tool_counts.get(leased.tool_name, 0) + 1
            self.record_event(
                event_type="work_item_claimed",
                actor_id=lease_owner,
                task_id=leased.task_id,
                payload=_work_item_event_payload(leased),
            )
        return claimed

    def claim_work_item(
        self,
        work_item_id: str,
        *,
        lease_owner: str,
        policy: WorkQueuePolicy | dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> WorkItem | None:
        queue_policy = _queue_policy(policy)
        effective_now = now or _utc_now()
        self.release_expired_work_item_leases(now=effective_now)

        item = self.store.get_work_item(work_item_id)
        if item is None:
            raise KeyError(f"work item not found: {work_item_id}")
        if item.status != "queued":
            return None
        if item.attempts >= item.max_attempts:
            failed = item.model_copy(
                update={
                    "status": "failed",
                    "error": "max attempts exhausted before claim",
                    "updated_at": effective_now,
                }
            )
            self.store.save_work_item(failed)
            self.record_event(
                event_type="work_item_failed",
                actor_id=lease_owner,
                task_id=failed.task_id,
                payload={**_work_item_event_payload(failed), "error": failed.error},
            )
            return None

        active = [candidate for candidate in self.store.list_work_items() if candidate.is_active(effective_now)]
        active_agents = {candidate.agent_id for candidate in active}
        active_kind_counts = _queue_counts(active, "kind")
        active_model_counts = _queue_counts(active, "model")
        active_tool_counts = _queue_counts(active, "tool_name")
        if not queue_policy.allow_same_agent_parallel and item.agent_id in active_agents:
            return None
        if item.agent_id not in active_agents and len(active_agents) >= queue_policy.max_active_agents:
            return None
        if not _under_limit(active_kind_counts.get(item.kind, 0), queue_policy.per_kind_limits.get(item.kind, 0)):
            return None
        if item.model and not _under_limit(active_model_counts.get(item.model, 0), queue_policy.per_model_limits.get(item.model, 0)):
            return None
        if item.tool_name and not _under_limit(active_tool_counts.get(item.tool_name, 0), queue_policy.per_tool_limits.get(item.tool_name, 0)):
            return None

        leased = item.model_copy(
            update={
                "status": "leased",
                "lease_owner": lease_owner,
                "leased_at": effective_now,
                "lease_until": effective_now + timedelta(seconds=queue_policy.lease_seconds),
                "attempts": item.attempts + 1,
                "updated_at": effective_now,
            }
        )
        self.store.save_work_item(leased)
        self.record_event(
            event_type="work_item_claimed",
            actor_id=lease_owner,
            task_id=leased.task_id,
            payload=_work_item_event_payload(leased),
        )
        return leased

    def complete_work_item(
        self,
        work_item_id: str,
        *,
        actor_id: str,
        result: dict[str, object] | None = None,
    ) -> WorkItem:
        item = self._require_work_item(work_item_id)
        now = _utc_now()
        completed = item.model_copy(
            update={
                "status": "completed",
                "completed_at": now,
                "updated_at": now,
                "result": result or {},
                "lease_owner": "",
                "leased_at": None,
                "lease_until": None,
            }
        )
        self.store.save_work_item(completed)
        self.record_event(
            event_type="work_item_completed",
            actor_id=actor_id,
            task_id=completed.task_id,
            payload={**_work_item_event_payload(completed), "result": _clip_payload(result or {})},
        )
        return completed

    def fail_work_item(
        self,
        work_item_id: str,
        *,
        actor_id: str,
        error: str,
        retry: bool = True,
    ) -> WorkItem:
        item = self._require_work_item(work_item_id)
        now = _utc_now()
        should_retry = retry and item.attempts < item.max_attempts
        update = {
            "status": "queued" if should_retry else "failed",
            "error": _clip_event_text(error),
            "updated_at": now,
            "lease_owner": "",
            "leased_at": None,
            "lease_until": None,
        }
        failed = item.model_copy(update=update)
        self.store.save_work_item(failed)
        event_type = "work_item_requeued" if should_retry else "work_item_failed"
        self.record_event(
            event_type=event_type,
            actor_id=actor_id,
            task_id=failed.task_id,
            payload={**_work_item_event_payload(failed), "error": failed.error},
        )
        return failed

    def cancel_work_item(self, work_item_id: str, *, actor_id: str, reason: str = "") -> WorkItem:
        item = self._require_work_item(work_item_id)
        now = _utc_now()
        cancelled = item.model_copy(
            update={
                "status": "cancelled",
                "error": _clip_event_text(reason),
                "updated_at": now,
                "lease_owner": "",
                "leased_at": None,
                "lease_until": None,
            }
        )
        self.store.save_work_item(cancelled)
        self.record_event(
            event_type="work_item_cancelled",
            actor_id=actor_id,
            task_id=cancelled.task_id,
            payload={**_work_item_event_payload(cancelled), "reason": _clip_event_text(reason)},
        )
        return cancelled

    def release_expired_work_item_leases(self, *, now: datetime | None = None) -> list[WorkItem]:
        effective_now = now or _utc_now()
        released: list[WorkItem] = []
        for item in self.store.list_work_items(status="leased"):
            if item.lease_until is None or item.lease_until > effective_now:
                continue
            update = {
                "status": "queued" if item.attempts < item.max_attempts else "failed",
                "lease_owner": "",
                "leased_at": None,
                "lease_until": None,
                "updated_at": effective_now,
                "error": "lease expired",
            }
            released_item = item.model_copy(update=update)
            self.store.save_work_item(released_item)
            released.append(released_item)
            self.record_event(
                event_type="work_item_lease_expired" if released_item.status == "queued" else "work_item_failed",
                actor_id=item.lease_owner or "runtime",
                task_id=item.task_id,
                payload=_work_item_event_payload(released_item),
            )
        return released

    def work_queue_snapshot(self) -> dict[str, object]:
        items = self.store.list_work_items()
        status_counts = _queue_counts(items, "status")
        kind_counts = _queue_counts(items, "kind")
        active_items = [item for item in items if item.is_active()]
        return {
            "total": len(items),
            "status_counts": status_counts,
            "kind_counts": kind_counts,
            "active_agents": len({item.agent_id for item in active_items}),
            "active_items": len(active_items),
            "items": [item.model_dump(mode="json") for item in items],
        }

    def _require_work_item(self, work_item_id: str) -> WorkItem:
        item = self.store.get_work_item(work_item_id)
        if item is None:
            raise KeyError(f"work item not found: {work_item_id}")
        return item

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
        event = self.record_event(
            event_type="worker_run_finished",
            actor_id=actor_id,
            task_id=task_id,
            payload={"run_id": run_id, "returncode": returncode, "timed_out": timed_out},
        )
        self._refresh_related_task_trace_exports(task_id)
        return event

    def record_provider_session(
        self,
        *,
        provider: str,
        provider_session_id: str,
        run_id: str,
        task_id: str,
        actor_id: str,
        workspace: str,
        resume_command: str,
        worker_type: str = "",
        metadata: dict[str, object] | None = None,
    ) -> Event:
        return self.record_event(
            event_type="provider_session_registered",
            actor_id=actor_id,
            task_id=task_id,
            payload={
                "provider": provider,
                "provider_session_id": provider_session_id,
                "run_id": run_id,
                "workspace": workspace,
                "resume_command": resume_command,
                "worker_type": worker_type,
                "metadata": metadata or {},
            },
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
        event = self.record_event(
            event_type="agent_run_finished",
            actor_id=actor_id,
            task_id=task_id,
            payload=payload,
        )
        if task_id is not None:
            self._refresh_related_task_trace_exports(task_id)
        return event

    def export_task_trace(
        self,
        task_id: str,
        *,
        workspace: str | Path | None = None,
        trace_id: str | None = None,
        include_descendants: bool = True,
        include_file_contents: bool = True,
        max_file_bytes: int = 500_000,
        record_event: bool = True,
    ):
        from workforce_runtime.server.tracing import export_task_trace as write_task_trace_export

        output_dir = Path(workspace) if workspace is not None else self._default_task_trace_dir()
        trace = write_task_trace_export(
            self,
            task_id=task_id,
            workspace=output_dir,
            trace_id=trace_id,
            include_descendants=include_descendants,
            include_file_contents=include_file_contents,
            max_file_bytes=max_file_bytes,
        )
        if record_event:
            self.record_event(
                event_type="task_trace_exported",
                actor_id="system",
                task_id=task_id,
                payload={
                    "trace_id": trace.trace_id,
                    "trace_path": trace.path,
                    "format": trace.format,
                    "task_ids": trace.payload.get("scope", {}).get("task_ids", [task_id]),
                },
            )
        return trace

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

    def require_task_access(self, actor_id: str, task_id: str) -> None:
        if actor_id in {"human", "system", "runtime"}:
            return
        actor = self.store.get_agent(actor_id)
        if actor is None:
            raise KeyError(f"agent not found: {actor_id}")
        task = self.require_task(task_id)
        if actor_id in {task.assigned_to, task.assigned_by}:
            return
        if task.assigned_to and self.is_manager_of(actor_id, task.assigned_to):
            return
        if task.assigned_by and self.is_manager_of(actor_id, task.assigned_by):
            return
        if task.parent_task_id:
            parent = self.store.get_task(task.parent_task_id)
            if parent and actor_id in {parent.assigned_to, parent.assigned_by}:
                return
        self.record_event(
            event_type="permission_violation",
            actor_id=actor_id,
            task_id=task_id,
            payload={"capability": "task_dossier_access"},
        )
        raise PermissionError(f"agent {actor_id} cannot access task dossier: {task_id}")

    def require_tool_request_approver(
        self,
        actor_id: str,
        approval_level: str,
        *,
        task_id: str | None = None,
    ) -> None:
        if actor_id == "human":
            return
        agent = self.store.get_agent(actor_id)
        if agent is None:
            raise KeyError(f"agent not found: {actor_id}")
        role = agent.role.lower()
        if approval_level == "human_ceo" and (agent.manager_id is None or "ceo" in role):
            return
        if approval_level == "vp" and ("vp" in role or agent.manager_id is None or "ceo" in role):
            return
        if approval_level == "manager" and task_id is not None:
            task = self.require_task(task_id)
            if task.assigned_to and self.is_manager_of(actor_id, task.assigned_to):
                return
        self.record_event(
            event_type="permission_violation",
            actor_id=actor_id,
            task_id=task_id,
            payload={"capability": "approve_tool_request", "approval_level": approval_level},
        )
        raise PermissionError(f"agent {actor_id} cannot approve tool requests at level {approval_level}")

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

    def request_tool(
        self,
        *,
        actor_id: str,
        tool_name: str,
        problem: str,
        proposed_capability: str,
        task_id: str | None = None,
        frequency: str = "",
        current_workaround: str = "",
        requested_approval_level: str = "human_ceo",
    ) -> dict[str, object]:
        if task_id is not None:
            self.require_task_access(actor_id, task_id)
        elif actor_id not in {"human", "system", "runtime"} and self.store.get_agent(actor_id) is None:
            raise KeyError(f"agent not found: {actor_id}")

        request_id = f"toolreq_{uuid4().hex[:12]}"
        payload = {
            "request_id": request_id,
            "tool_name": tool_name,
            "problem": problem,
            "proposed_capability": proposed_capability,
            "frequency": frequency,
            "current_workaround": current_workaround,
            "requested_approval_level": requested_approval_level,
            "status": "pending",
        }
        self.record_event(event_type="tool_request_submitted", actor_id=actor_id, task_id=task_id, payload=payload)
        if task_id is not None:
            self.upsert_task_document(
                actor_id=actor_id,
                task_id=task_id,
                title=f"Tool request: {tool_name}",
                doc_type="tool_request",
                content=(
                    f"Problem: {problem}\n\n"
                    f"Proposed capability: {proposed_capability}\n\n"
                    f"Frequency: {frequency or 'not specified'}\n\n"
                    f"Current workaround: {current_workaround or 'not specified'}"
                ),
            )
        return {"ok": True, "request_id": request_id, "status": "pending", "approval_level": requested_approval_level}

    def decide_tool_request(
        self,
        *,
        actor_id: str,
        request_id: str,
        decision: str,
        notes: str = "",
        approval_level: str = "",
    ) -> dict[str, object]:
        request_event = next(
            (
                event
                for event in self.store.list_events()
                if event.event_type == "tool_request_submitted" and event.payload.get("request_id") == request_id
            ),
            None,
        )
        if request_event is None:
            raise KeyError(f"tool request not found: {request_id}")
        effective_level = approval_level or str(request_event.payload.get("requested_approval_level") or "human_ceo")
        self.require_tool_request_approver(actor_id, effective_level, task_id=request_event.task_id)
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        self.record_event(
            event_type=f"tool_request_{decision}",
            actor_id=actor_id,
            task_id=request_event.task_id,
            payload={
                "request_id": request_id,
                "decision": decision,
                "notes": notes,
                "approval_level": effective_level,
            },
        )
        return {"ok": True, "request_id": request_id, "decision": decision}

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
        model: str = "",
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
            model=model,
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
        self._ensure_agent_personal_profile(agent.id, updated_by=requested_by)
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

    def get_agent_personal_profile(self, *, actor_id: str, agent_id: str) -> AgentPersonalProfile:
        self.require_agent_profile_access(actor_id, agent_id)
        return self._ensure_agent_personal_profile(agent_id, updated_by="runtime")

    def list_visible_agent_personal_profiles(self, *, actor_id: str) -> list[AgentPersonalProfile]:
        agents = self.store.list_agents()
        if actor_id in {"human", "system", "runtime"}:
            visible_ids = {agent.id for agent in agents}
        else:
            if self.store.get_agent(actor_id) is None:
                raise KeyError(f"agent not found: {actor_id}")
            visible_ids = {agent.id for agent in agents if agent.id == actor_id or self.is_manager_of(actor_id, agent.id)}
        return [
            self._ensure_agent_personal_profile(agent_id, updated_by="runtime")
            for agent_id in sorted(visible_ids)
        ]

    def update_agent_personal_profile(
        self,
        *,
        actor_id: str,
        agent_id: str,
        summary: str | None = None,
        knows_about: list[str] | None = None,
        can_do: list[str] | None = None,
        specialty_tags: list[str] | None = None,
        preferred_tools: list[str] | None = None,
        boundaries: list[str] | None = None,
        experience: AgentExperience | None = None,
    ) -> AgentPersonalProfile:
        if actor_id not in {"human", "system", "runtime"} and actor_id != agent_id:
            self.record_event(
                event_type="permission_violation",
                actor_id=actor_id,
                payload={"capability": "update_agent_profile", "target_agent_id": agent_id},
            )
            raise PermissionError("agents can only update their own personal profile")
        if self.store.get_agent(agent_id) is None:
            raise KeyError(f"agent not found: {agent_id}")
        existing = self._ensure_agent_personal_profile(agent_id, updated_by=actor_id)
        updates: dict[str, object] = {
            "updated_by": actor_id,
            "revision": existing.revision + 1,
            "updated_at": datetime.now(timezone.utc),
        }
        if summary is not None:
            updates["summary"] = _clip_event_text(summary)
        if knows_about is not None:
            updates["knows_about"] = _merge_limited(existing.knows_about, knows_about, limit=24)
        if can_do is not None:
            updates["can_do"] = _merge_limited(existing.can_do, can_do, limit=24)
        if specialty_tags is not None:
            updates["specialty_tags"] = _merge_limited(existing.specialty_tags, specialty_tags, limit=12)
        if preferred_tools is not None:
            updates["preferred_tools"] = _merge_limited(existing.preferred_tools, preferred_tools, limit=16)
        if boundaries is not None:
            updates["boundaries"] = _merge_limited(existing.boundaries, boundaries, limit=16)
        if experience is not None:
            updates["experiences"] = [*existing.experiences, experience][-20:]
        updates = {key: value for key, value in updates.items() if value is not None}
        profile = existing.model_copy(update=updates)
        self.store.save_agent_personal_profile(profile)
        self.record_event(
            event_type="agent_profile_updated",
            actor_id=actor_id,
            task_id=experience.task_id if experience and experience.task_id else None,
            payload={
                "profile_agent_id": agent_id,
                "revision": profile.revision,
                "summary": _clip_event_text(profile.summary, limit=300),
                "specialty_tags": profile.specialty_tags[:8],
            },
        )
        return profile

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

    def _next_task_id(self, title: str = "task") -> str:
        existing = self.store.list_tasks()
        slug = _slugify(title)
        return f"task_{len(existing) + 1:03d}_{slug}_{uuid4().hex[:6]}"

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
            task_id=self._next_task_id(f"Review report {report.report_id}"),
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
        self._refresh_related_task_trace_exports(task.task_id)
        self._refresh_related_task_trace_exports(review_task.task_id)

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

    def _default_task_trace_dir(self) -> Path:
        return self.db_path.parent / "task_traces"

    def _refresh_related_task_trace_exports(self, task_id: str) -> None:
        for related_task_id in self._related_trace_task_ids(task_id):
            self._refresh_task_trace_export(related_task_id)

    def _refresh_task_trace_export(self, task_id: str) -> None:
        try:
            self.export_task_trace(task_id, workspace=self._default_task_trace_dir(), record_event=True)
        except Exception as exc:  # noqa: BLE001 - trace export must not break task execution.
            self.record_event(
                event_type="task_trace_export_failed",
                actor_id="system",
                task_id=task_id,
                payload={"error": _clip_event_text(str(exc))},
            )

    def _related_trace_task_ids(self, task_id: str) -> list[str]:
        related: list[str] = []
        seen: set[str] = set()

        def add(candidate: str | None) -> None:
            if candidate and candidate not in seen:
                seen.add(candidate)
                related.append(candidate)

        task = self.store.get_task(task_id)
        add(task_id)
        if task is None:
            return related
        add(task.root_goal_id)
        current = task
        while current.parent_task_id:
            add(current.parent_task_id)
            parent = self.store.get_task(current.parent_task_id)
            if parent is None:
                break
            current = parent
        return related

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _queue_policy(policy: WorkQueuePolicy | dict[str, object] | None) -> WorkQueuePolicy:
    if policy is None:
        return WorkQueuePolicy()
    if isinstance(policy, WorkQueuePolicy):
        return policy
    return WorkQueuePolicy.model_validate(policy)


def _queue_counts(items: list[WorkItem], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(getattr(item, attr, "") or "")
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _under_limit(current: int, limit: int) -> bool:
    return limit <= 0 or current < limit


def _work_item_event_payload(item: WorkItem) -> dict[str, object]:
    payload: dict[str, object] = {
        "work_item_id": item.work_item_id,
        "kind": item.kind,
        "agent_id": item.agent_id,
        "status": item.status,
        "priority": item.priority,
        "attempts": item.attempts,
        "max_attempts": item.max_attempts,
    }
    if item.task_id:
        payload["task_id"] = item.task_id
    if item.model:
        payload["model"] = item.model
    if item.tool_name:
        payload["tool_name"] = item.tool_name
    if item.lease_owner:
        payload["lease_owner"] = item.lease_owner
    if item.lease_until:
        payload["lease_until"] = item.lease_until.isoformat()
    return payload


def _clip_payload(payload: dict[str, object], *, limit: int = 500) -> dict[str, object]:
    clipped: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            clipped[key] = _clip_event_text(value, limit=limit)
        else:
            clipped[key] = value
    return clipped


def _merge_limited(existing: list[str], incoming: list[str], *, limit: int) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *incoming]:
        item = _profile_item(value)
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-limit:]


def _profile_item(value: object, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _profile_tags_from_text(text: str, *, limit: int = 8) -> list[str]:
    stopwords = {
        "about",
        "agent",
        "and",
        "are",
        "can",
        "for",
        "from",
        "into",
        "manager",
        "report",
        "task",
        "that",
        "the",
        "this",
        "with",
        "work",
    }
    tags: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower()):
        token = raw.strip("_-")
        if token in stopwords or token in seen:
            continue
        seen.add(token)
        tags.append(token)
        if len(tags) >= limit:
            break
    return tags


def _slugify(value: str, *, limit: int = 42) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not slug:
        return "task"
    return slug[:limit].strip("_") or "task"
