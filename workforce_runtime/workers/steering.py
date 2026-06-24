from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol


class SteerableSession(Protocol):
    run_id: str
    task_id: str
    agent_id: str

    def steer(self, message: str, *, from_agent_id: str = "human") -> None:
        ...

    def interrupt(self, *, from_agent_id: str = "human") -> None:
        ...


@dataclass(frozen=True)
class SteerResult:
    ok: bool
    status: str
    run_id: str = ""
    task_id: str = ""
    agent_id: str = ""
    message: str = ""


class SteerableSessionRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, SteerableSession] = {}

    def register(self, session: SteerableSession) -> None:
        with self._lock:
            self._sessions[_session_key(session.agent_id, session.task_id)] = session

    def unregister(self, *, agent_id: str, task_id: str) -> None:
        with self._lock:
            self._sessions.pop(_session_key(agent_id, task_id), None)

    def find(self, *, agent_id: str, task_id: str | None = None) -> SteerableSession | None:
        with self._lock:
            if task_id:
                return self._sessions.get(_session_key(agent_id, task_id))
            matches = [session for session in self._sessions.values() if session.agent_id == agent_id]
            return matches[-1] if matches else None

    def list_sessions(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {"run_id": session.run_id, "task_id": session.task_id, "agent_id": session.agent_id}
                for session in self._sessions.values()
            ]

    def steer(self, *, agent_id: str, message: str, task_id: str | None = None, from_agent_id: str = "human") -> SteerResult:
        session = self.find(agent_id=agent_id, task_id=task_id)
        if session is None:
            return SteerResult(ok=False, status="no_active_session", agent_id=agent_id, task_id=task_id or "", message=message)
        session.steer(message, from_agent_id=from_agent_id)
        return SteerResult(
            ok=True,
            status="sent",
            run_id=session.run_id,
            task_id=session.task_id,
            agent_id=session.agent_id,
            message=message,
        )

    def interrupt(self, *, agent_id: str, task_id: str | None = None, from_agent_id: str = "human") -> SteerResult:
        session = self.find(agent_id=agent_id, task_id=task_id)
        if session is None:
            return SteerResult(ok=False, status="no_active_session", agent_id=agent_id, task_id=task_id or "")
        session.interrupt(from_agent_id=from_agent_id)
        return SteerResult(ok=True, status="interrupted", run_id=session.run_id, task_id=session.task_id, agent_id=session.agent_id)


def _session_key(agent_id: str, task_id: str) -> str:
    return f"{agent_id}\0{task_id}"


STEERABLE_SESSIONS = SteerableSessionRegistry()
