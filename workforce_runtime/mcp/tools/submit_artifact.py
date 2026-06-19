from __future__ import annotations

from uuid import uuid4

from workforce_runtime.core import Artifact
from workforce_runtime.server.runtime import WorkforceRuntime


def submit_artifact(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    artifact = Artifact(
        artifact_id=str(arguments.get("artifact_id") or f"artifact_{uuid4().hex[:12]}"),
        task_id=str(arguments["task_id"]),
        agent_id=str(arguments["agent_id"]),
        type=str(arguments["artifact_type"]),
        path=str(arguments["path"]),
        description=str(arguments.get("description") or ""),
    )
    runtime.register_artifact(artifact)
    return {"ok": True, "artifact_id": artifact.artifact_id}
