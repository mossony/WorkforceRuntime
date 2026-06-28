from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# open            -> currently held by an agent (current_holder_id) awaiting answer/escalation
# awaiting_human  -> escalated past the CEO; the human operator must answer
# resolved        -> answered; the origin task has been resumed for the asker
# cancelled       -> abandoned (e.g. origin task cancelled)
ClarificationStatus = Literal["open", "awaiting_human", "resolved", "cancelled"]

HUMAN_HOLDER = "human"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Clarification(BaseModel):
    """A question raised by a lower agent that escalates up the management chain.

    A worker that cannot proceed because something is ambiguous raises a
    clarification. It is routed to the worker's direct manager; any holder that
    cannot answer escalates it one level further up. If it passes the top-level
    agent (CEO) unanswered it becomes ``awaiting_human`` and the human operator
    answers it. Once answered the answer is delivered to the original asker and
    the blocked origin task is resumed.
    """

    model_config = ConfigDict(extra="forbid")

    clarification_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    asker_agent_id: str = Field(min_length=1)
    origin_task_id: str | None = None
    current_holder_id: str = Field(min_length=1)
    status: ClarificationStatus = "open"
    answer: str = ""
    answered_by: str = ""
    # Ordered list of everyone the question has reached, starting with the asker.
    chain: list[str] = Field(default_factory=list)
    thread_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def is_terminal(self) -> bool:
        return self.status in {"resolved", "cancelled"}
