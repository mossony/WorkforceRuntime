# Workforce Runtime Design

Workforce Runtime is the governance layer above third-party AI workers. It owns organization structure, task contracts, reports, budgets, permissions, artifacts, storage, logs, and user-facing summaries. It does not implement a custom LLM agent loop.

The runtime treats systems such as Codex and Claude Code as external worker executors. A worker adapter is responsible for translating a structured task contract into that worker's native invocation format, starting an isolated process, capturing stdout and stderr, collecting artifacts, and recording the final report through the runtime.

The first implementation phases stay intentionally small: Pydantic contracts, an in-memory organization graph, SQLite persistence, a local runtime service, MCP tools, then worker adapters. Real Codex and Claude Code execution should arrive only after the generic CLI adapter proves the lifecycle.
