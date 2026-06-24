from __future__ import annotations

from workforce_runtime.server.runtime import WorkforceRuntime


def claim_work(runtime: WorkforceRuntime, arguments: dict[str, object]) -> dict[str, object]:
    policy = arguments.get("policy")
    if policy is not None and not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    items = runtime.claim_work_items(
        lease_owner=str(arguments.get("lease_owner") or arguments.get("from_agent_id") or "dispatcher"),
        limit=int(arguments.get("limit") or 1),
        policy=policy,
    )
    return {
        "ok": True,
        "claimed": [item.model_dump(mode="json") for item in items],
        "claimed_count": len(items),
    }
