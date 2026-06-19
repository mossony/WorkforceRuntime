from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int = Field(default=0, ge=0)
    max_runtime_seconds: int = Field(default=0, ge=0)
    max_tool_calls: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)
    runtime_seconds_used: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)

    def record_usage(
        self,
        *,
        tokens: int = 0,
        runtime_seconds: int = 0,
        tool_calls: int = 0,
    ) -> None:
        if tokens < 0 or runtime_seconds < 0 or tool_calls < 0:
            raise ValueError("usage increments must be nonnegative")

        self.tokens_used += tokens
        self.runtime_seconds_used += runtime_seconds
        self.tool_calls_used += tool_calls

    def would_exceed(
        self,
        *,
        tokens: int = 0,
        runtime_seconds: int = 0,
        tool_calls: int = 0,
    ) -> bool:
        return (
            self.tokens_used + tokens > self.max_tokens
            or self.runtime_seconds_used + runtime_seconds > self.max_runtime_seconds
            or self.tool_calls_used + tool_calls > self.max_tool_calls
        )


class UsageCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens_used: int = Field(default=0, ge=0)
    runtime_seconds: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
