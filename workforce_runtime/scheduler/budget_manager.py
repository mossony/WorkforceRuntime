from __future__ import annotations

from workforce_runtime.core.budget import Budget


class BudgetManager:
    def has_capacity(
        self,
        budget: Budget,
        *,
        tokens: int = 0,
        runtime_seconds: int = 0,
        tool_calls: int = 0,
    ) -> bool:
        return not budget.would_exceed(
            tokens=tokens,
            runtime_seconds=runtime_seconds,
            tool_calls=tool_calls,
        )
