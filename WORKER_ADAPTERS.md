# Worker Adapters

Worker adapters connect Workforce Runtime task contracts to external executors. The runtime does not implement a custom LLM agent loop.

## Adapter Responsibilities

An adapter should:

- write the `TaskContract` to the task artifact directory
- start the external worker in the assigned workspace
- set worker environment variables or CLI arguments
- capture stdout and stderr
- collect durable artifacts such as diffs, logs, and final messages
- stream stdout/stderr chunks into runtime events while the process is running
- update task status
- register artifacts
- submit or synthesize a structured report to the worker's direct manager
- record usage where available

## Generic CLI Worker

`GenericCLIWorker` runs any command that can read the runtime environment:

- `WORKFORCE_RUN_ID`
- `WORKFORCE_TASK_ID`
- `WORKFORCE_AGENT_ID`
- `WORKFORCE_MANAGER_ID`
- `WORKFORCE_TASK_CONTRACT_PATH`
- `WORKFORCE_RUNTIME_DB`
- `WORKFORCE_WORKSPACE`
- `WORKFORCE_MCP_COMMAND`

Use this adapter for deterministic tests, local scripts, and prototype workers. The packaged demo uses `examples/mock_worker/fix_parser_worker.py`.

All worker adapters record `worker_run_started`, `worker_output`, and `worker_run_finished` events. The dashboard consumes these events for live status and output display.

For non-CLI management agents such as CEO, VP, HR, or engineering managers, external executors can use the generic agent stream events instead:

- `agent_run_started`
- `agent_output`
- `agent_run_finished`

These events are intentionally adapter-level hooks, not a custom Workforce Runtime LLM loop. A manager executor that streams from OpenRouter should write each model chunk through `record_agent_output(...)`; the web dashboard receives it over `/api/events/stream`.

## Codex Worker

`CodexWorker` launches official Codex noninteractively:

```bash
codex --profile workforce-openrouter -a never -s workspace-write -C <workspace> exec --json --output-last-message <path> <prompt>
```

It captures JSONL stdout, stderr, `codex-final.md`, git diff, usage from `turn.completed` events, and registers a final report.

The default profile is `workforce-openrouter`. The expected user-level config is:

```toml
model = "openai/gpt-oss-120b:free"
model_provider = "openrouter"

[model_providers.openrouter]
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
```

Keep `OPENROUTER_API_KEY` in the shell environment, such as `.zshrc`, not in repository files. See `docs/CODEX_AGENT_INTEGRATION.md`.

Model context limits are configured separately from runtime budgets. See
`examples/openrouter_models.json` for the JSON model registry and
`workforce_runtime/config/model_registry.py` for the built-in defaults used in
generated prompts and MCP context responses. OpenRouter `max_tokens` is an
output cap, not the model context window.

## Claude Code Worker

`ClaudeCodeWorker` launches Claude Code in print mode:

```bash
claude -p <prompt> --output-format json
```

It captures JSON stdout, stderr, `claude-final.md`, git diff, usage fields when present, and registers a final report.

## Adding Another Adapter

Add a class with the same surface as the existing workers:

- `declare_capabilities()`
- `start_task(task, runtime_context)`
- `collect_artifacts(run_id)`
- `stop_task(run_id)`
- `get_usage(run_id)`

Prefer using MCP tools from the worker process when the external agent can call them. If it cannot, the adapter may synthesize the final report from process output, as the Codex and Claude Code adapters do today.

## System Prompts

Workforce Runtime generates a default system prompt for each agent when an organization is loaded or HR hires a new agent. The prompt is based on company mission, role, manager, responsibilities, permissions, and whether the agent is a CEO, HR agent, manager, or worker.

Managers can update subordinate prompts through the `update_system_prompt` MCP tool. This keeps prompt changes inside the same auditable communication path as reports, assignments, discussions, and hiring.
