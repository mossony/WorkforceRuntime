from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workforce_runtime.v2.models import NormalizedEvent


def parse_github_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


class GitHubShadowConnector:
    """Read-only converter from GitHub webhook/archive payloads to normalized events."""

    source = "github"

    def load_payloads(self, path: Path) -> list[dict[str, Any]]:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            if "events" in data and isinstance(data["events"], list):
                return list(data["events"])
            return [data]
        if isinstance(data, list):
            return data
        raise ValueError("GitHub shadow fixture must be a JSON object or array")

    def ingest_file(
        self,
        path: Path,
        *,
        organization_id: str,
        project_id: str,
        identity_map: dict[str, dict[str, str]],
        cursor: str | None = None,
    ) -> tuple[list[NormalizedEvent], str | None]:
        return self.ingest_payloads(
            self.load_payloads(path),
            organization_id=organization_id,
            project_id=project_id,
            identity_map=identity_map,
            cursor=cursor,
        )

    def ingest_payloads(
        self,
        payloads: list[dict[str, Any]],
        *,
        organization_id: str,
        project_id: str,
        identity_map: dict[str, dict[str, str]],
        cursor: str | None = None,
    ) -> tuple[list[NormalizedEvent], str | None]:
        events: list[NormalizedEvent] = []
        start_index = int(cursor or 0)
        for index, payload in enumerate(payloads[start_index:], start=start_index):
            converted = self.payload_to_events(
                payload,
                organization_id=organization_id,
                project_id=project_id,
                identity_map=identity_map,
                sequence=index,
            )
            events.extend(converted)
        next_cursor = str(len(payloads)) if payloads else cursor
        return events, next_cursor

    def payload_to_events(
        self,
        payload: dict[str, Any],
        *,
        organization_id: str,
        project_id: str,
        identity_map: dict[str, dict[str, str]],
        sequence: int,
    ) -> list[NormalizedEvent]:
        event_kind = str(payload.get("event") or payload.get("type") or "")
        action = str(payload.get("action") or "").lower()
        base_id = str(payload.get("id") or payload.get("node_id") or payload.get("delivery_id") or sequence)
        sender = self._login(payload.get("sender") or payload.get("actor") or payload.get("user"))
        actor = self._identity(sender, identity_map)
        occurred_at = self._occurred_at(payload)

        if event_kind in {"pull_request", "PullRequestEvent"}:
            return [
                self._pull_request_event(
                    payload,
                    organization_id=organization_id,
                    project_id=project_id,
                    source_event_id=base_id,
                    actor=actor,
                    action=action,
                    occurred_at=occurred_at,
                )
            ]
        if event_kind in {"pull_request_review", "PullRequestReviewEvent"}:
            return [
                self._review_event(
                    payload,
                    organization_id=organization_id,
                    project_id=project_id,
                    source_event_id=base_id,
                    actor=actor,
                    action=action,
                    identity_map=identity_map,
                    occurred_at=occurred_at,
                )
            ]
        if event_kind in {"issues", "IssuesEvent"}:
            return [
                self._issue_event(
                    payload,
                    organization_id=organization_id,
                    project_id=project_id,
                    source_event_id=base_id,
                    actor=actor,
                    action=action,
                    occurred_at=occurred_at,
                )
            ]
        if event_kind in {"issue_comment", "IssueCommentEvent"}:
            return [
                self._message_event(
                    payload,
                    organization_id=organization_id,
                    project_id=project_id,
                    source_event_id=base_id,
                    actor=actor,
                    identity_map=identity_map,
                    occurred_at=occurred_at,
                )
            ]
        if event_kind in {"workflow_run", "check_suite", "CheckSuiteEvent"}:
            return [
                self._ci_event(
                    payload,
                    organization_id=organization_id,
                    project_id=project_id,
                    source_event_id=base_id,
                    actor=actor,
                    occurred_at=occurred_at,
                )
            ]
        return [
            NormalizedEvent(
                id=f"github_{base_id}",
                organization_id=organization_id,
                project_id=project_id,
                actor_position_id=actor.get("position_id"),
                actor_occupant_id=actor.get("occupant_id"),
                event_type="message_sent",
                object_type="github_event",
                object_id=base_id,
                occurred_at=occurred_at,
                source=self.source,
                source_event_id=base_id,
                metadata={"raw_event_type": event_kind, "action": action},
            )
        ]

    def _pull_request_event(
        self,
        payload: dict[str, Any],
        *,
        organization_id: str,
        project_id: str,
        source_event_id: str,
        actor: dict[str, str],
        action: str,
        occurred_at: datetime,
    ) -> NormalizedEvent:
        pr = payload.get("pull_request") or payload.get("payload", {}).get("pull_request") or payload
        pr_id = str(pr.get("id") or pr.get("number") or payload.get("id") or source_event_id)
        if action in {"opened", "reopened", "synchronize"}:
            event_type = "task_created"
        elif action in {"closed", "merged"} or pr.get("merged"):
            event_type = "task_completed"
        else:
            event_type = "message_sent"
        return NormalizedEvent(
            id=f"github_pr_{source_event_id}",
            organization_id=organization_id,
            project_id=project_id,
            actor_position_id=actor.get("position_id"),
            actor_occupant_id=actor.get("occupant_id"),
            event_type=event_type,
            object_type="pull_request",
            object_id=pr_id,
            occurred_at=occurred_at,
            source=self.source,
            source_event_id=source_event_id,
            task_id=f"pr_{pr_id}",
            metadata={
                "action": action,
                "title": pr.get("title") or payload.get("title"),
                "state": pr.get("state"),
                "merged": bool(pr.get("merged")),
                "task_category": "code_review",
            },
        )

    def _review_event(
        self,
        payload: dict[str, Any],
        *,
        organization_id: str,
        project_id: str,
        source_event_id: str,
        actor: dict[str, str],
        action: str,
        identity_map: dict[str, dict[str, str]],
        occurred_at: datetime,
    ) -> NormalizedEvent:
        review = payload.get("review") or payload
        pr = payload.get("pull_request") or payload.get("payload", {}).get("pull_request") or {}
        pr_id = str(pr.get("id") or pr.get("number") or review.get("pull_request_id") or source_event_id)
        pr_author = self._login(pr.get("user"))
        target = self._identity(pr_author, identity_map)
        state = str(review.get("state") or action).lower()
        if state in {"approved", "approve"}:
            event_type = "approval_granted"
        elif state in {"changes_requested", "rejected"}:
            event_type = "approval_rejected"
        else:
            event_type = "review_completed"
        return NormalizedEvent(
            id=f"github_review_{source_event_id}",
            organization_id=organization_id,
            project_id=project_id,
            actor_position_id=actor.get("position_id"),
            actor_occupant_id=actor.get("occupant_id"),
            target_position_id=target.get("position_id"),
            target_occupant_id=target.get("occupant_id"),
            event_type=event_type,
            object_type="pull_request",
            object_id=pr_id,
            occurred_at=occurred_at,
            source=self.source,
            source_event_id=source_event_id,
            task_id=f"pr_{pr_id}",
            metadata={"state": state, "task_category": "code_review"},
        )

    def _issue_event(
        self,
        payload: dict[str, Any],
        *,
        organization_id: str,
        project_id: str,
        source_event_id: str,
        actor: dict[str, str],
        action: str,
        occurred_at: datetime,
    ) -> NormalizedEvent:
        issue = payload.get("issue") or payload
        issue_id = str(issue.get("id") or issue.get("number") or source_event_id)
        if action in {"assigned", "transferred"}:
            event_type = "task_assigned"
        elif action in {"closed", "completed"}:
            event_type = "task_completed"
        else:
            event_type = "task_created"
        assignee = issue.get("assignee") or {}
        return NormalizedEvent(
            id=f"github_issue_{source_event_id}",
            organization_id=organization_id,
            project_id=project_id,
            actor_position_id=actor.get("position_id"),
            actor_occupant_id=actor.get("occupant_id"),
            target_occupant_id=self._login(assignee) or None,
            event_type=event_type,
            object_type="issue",
            object_id=issue_id,
            occurred_at=occurred_at,
            source=self.source,
            source_event_id=source_event_id,
            task_id=f"issue_{issue_id}",
            metadata={"action": action, "title": issue.get("title"), "task_category": "issue"},
        )

    def _message_event(
        self,
        payload: dict[str, Any],
        *,
        organization_id: str,
        project_id: str,
        source_event_id: str,
        actor: dict[str, str],
        identity_map: dict[str, dict[str, str]],
        occurred_at: datetime,
    ) -> NormalizedEvent:
        issue = payload.get("issue") or {}
        issue_author = self._login(issue.get("user"))
        target = self._identity(issue_author, identity_map)
        issue_id = str(issue.get("id") or issue.get("number") or source_event_id)
        return NormalizedEvent(
            id=f"github_comment_{source_event_id}",
            organization_id=organization_id,
            project_id=project_id,
            actor_position_id=actor.get("position_id"),
            actor_occupant_id=actor.get("occupant_id"),
            target_position_id=target.get("position_id"),
            target_occupant_id=target.get("occupant_id"),
            event_type="message_sent",
            object_type="issue",
            object_id=issue_id,
            occurred_at=occurred_at,
            source=self.source,
            source_event_id=source_event_id,
            task_id=f"issue_{issue_id}",
            metadata={"task_category": "discussion"},
        )

    def _ci_event(
        self,
        payload: dict[str, Any],
        *,
        organization_id: str,
        project_id: str,
        source_event_id: str,
        actor: dict[str, str],
        occurred_at: datetime,
    ) -> NormalizedEvent:
        workflow_run = payload.get("workflow_run") or payload.get("check_suite") or payload
        run_id = str(workflow_run.get("id") or source_event_id)
        conclusion = str(workflow_run.get("conclusion") or workflow_run.get("status") or "").lower()
        event_type = "task_failed" if conclusion in {"failure", "failed", "timed_out"} else "artifact_created"
        return NormalizedEvent(
            id=f"github_ci_{source_event_id}",
            organization_id=organization_id,
            project_id=project_id,
            actor_position_id=actor.get("position_id"),
            actor_occupant_id=actor.get("occupant_id"),
            event_type=event_type,
            object_type="ci_run",
            object_id=run_id,
            occurred_at=occurred_at,
            source=self.source,
            source_event_id=source_event_id,
            metadata={"conclusion": conclusion, "task_category": "ci"},
        )

    def _occurred_at(self, payload: dict[str, Any]) -> datetime:
        candidates = [
            payload.get("created_at"),
            payload.get("updated_at"),
            payload.get("pull_request", {}).get("created_at") if isinstance(payload.get("pull_request"), dict) else None,
            payload.get("review", {}).get("submitted_at") if isinstance(payload.get("review"), dict) else None,
            payload.get("issue", {}).get("created_at") if isinstance(payload.get("issue"), dict) else None,
            payload.get("workflow_run", {}).get("updated_at") if isinstance(payload.get("workflow_run"), dict) else None,
        ]
        for candidate in candidates:
            if candidate:
                return parse_github_time(str(candidate))
        return parse_github_time(None)

    def _identity(self, login: str, identity_map: dict[str, dict[str, str]]) -> dict[str, str]:
        if not login:
            return {}
        mapped = identity_map.get(login)
        if mapped is not None:
            return mapped
        return {"external_actor_id": f"github:{login}"}

    def _login(self, value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("login") or value.get("name") or "")
        if value is None:
            return ""
        return str(value)
