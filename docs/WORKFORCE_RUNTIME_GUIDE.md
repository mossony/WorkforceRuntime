# Workforce Runtime Guide

This is the single operational guide for the current Workforce Runtime branch.
It replaces the older split notes for quickstart, examples, MCP tools, worker
adapters, runtime dependencies, storage backends, Codex integration, and
dashboard startup.

## What Workforce Runtime Is

Workforce Runtime is an organization-level control plane for AI workers. It
stores companies, agents, positions, tasks, reports, artifacts, budgets, events,
queues, traces, and dashboard state. It does not implement a custom LLM agent
loop. Codex, Claude Code, generic CLI workers, and other executors remain
external worker processes. Direct OpenRouter API calls are reserved for
lightweight summaries, model metadata, and legacy eval harnesses; real
decision-making agents should use Codex or Claude Code worker backends.

## What Workforce Runtime Is Not

It is not a prompt framework, a replacement for Codex or Claude Code, a browser
automation framework, or a single-agent planner. The runtime is the management
and governance layer above those workers.

## Install For Local Development

```bash
python3 -m pip install --user uv
uv venv
source .venv/bin/activate
uv sync --extra dev
```

Provider keys are read from environment variables. Do not put secrets in JSON
config files.

```bash
export OPENROUTER_API_KEY=...
export NVIDIA_API_KEY=...
export CEREBRAS_API_KEY=...
export GROQ_API_KEY=...
```

## Required Local Services

The default local backend uses MySQL for durable runtime state and RabbitMQ for
per-agent inbox delivery. SQLite is still supported for tests and legacy demos
when `--db` points to a `.sqlite`, `.sqlite3`, or `.db` path.

RabbitMQ:

```bash
docker run -d --name workforce-rabbitmq \
  -p 5672:5672 -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=workforce \
  -e RABBITMQ_DEFAULT_PASS=workforce \
  rabbitmq:3-management
```

MySQL:

```bash
docker run -d --name workforce-mysql \
  -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=workforce_root \
  -e MYSQL_DATABASE=workforce_runtime \
  -e MYSQL_USER=workforce \
  -e MYSQL_PASSWORD=workforce \
  mysql:8.4
```

Health checks:

```bash
docker exec workforce-rabbitmq rabbitmq-diagnostics -q ping
docker exec workforce-mysql mysqladmin ping -h 127.0.0.1 -uworkforce -pworkforce --silent
```

Default service settings live in `workforce_runtime_config.json` and
`examples/workforce_runtime_config.json`.

## Configuration

Use one JSON file for runtime settings:

```bash
cp examples/workforce_runtime_config.json workforce_runtime_config.json
```

Important sections:

- `runtime`: backend, workspace root, task trace export settings.
- `mysql`: MySQL host, port, database, username, password.
- `agent_inbox`: RabbitMQ exchange, queue prefix, and connection settings.
- `queue`: active agent, model, tool, and work-kind concurrency limits.
- `external_mcp`: external MCP server registry, auth env var names, exposed
  clone tool prefixes, agent/role allowlists, and queue policy.
- `models`: provider, context window, output limit, reasoning, tool, and
  response-format metadata.
- `model_failover`: fallback chains and unavailable-model error fragments.
- `designed_task`: dashboard design/run defaults.
- `dashboard`: host, port, refresh, org display, and activity settings.
- `workers`: Codex, Claude Code, and generic CLI worker settings.
- `execution`: full-access or sandbox process wrapper settings.

## Run The Dashboard

Default configured backend:

```bash
workforce-runtime --config workforce_runtime_config.json dashboard --serve
```

Legacy SQLite demo backend:

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --serve
```

The web dashboard runs at `http://127.0.0.1:8765` by default. Simple mode is the
operator view: task input, config expansion, design/run controls, human reports,
and per-task ELK org tree. Debug mode keeps the detailed tables, output streams,
trace export, replay, trajectories, demo launchers, and raw config editor.

## Common Commands

Run demos:

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite demo sample-repo-fix
workforce-runtime --db .workforce_runtime/simple.sqlite demo simple-status
workforce-runtime --db .workforce_runtime/web.sqlite demo web-research --workspace .workforce_runtime/demo/web-research
workforce-runtime --db .workforce_runtime/large-org-scale.sqlite demo large-org-scale --agent-count 3000 --active-agent-limit 20
```

The `sample-repo-fix` demo uses the deterministic mock worker at
`examples/mock_worker/fix_parser_worker.py`; it does not require Codex or
Claude Code. The placeholder workspaces in `examples/codex_worker_task/` and
`examples/claude_worker_task/` are for real CLI worker runs.

Inspect state:

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --replay
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --trajectories
```

Design an organization:

```bash
workforce-runtime org design \
  --goal "Research a public RFC and produce an evidence-backed summary" \
  --headcount-limit 6 \
  --use-llm \
  --out .workforce_runtime/designed_org.yaml
```

Run a benchmark case:

```bash
workforce-runtime --db .workforce_runtime/benchmark.sqlite benchmark run \
  --case examples/benchmarks/web_research_real_llm.json \
  --workspace .workforce_runtime/benchmark/workspace \
  --use-llm --judge heuristic
```

Export a complete task trace:

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite task export-trace <task_id>
```

## Organization And Task Model

An org chart is a company plus agents. Agents have ids, names, roles,
departments, managers, worker types, responsibilities, permissions, budgets,
status, model settings, and generated system prompts. Tasks are structured
contracts with objective, acceptance criteria, assignee, required artifacts,
budget, status, and parent/root relationships.

Managers use MCP tools to assign work, discuss with peers, check progress,
review reports, update docs, and report upward. `report()` creates a durable
manager-review inbox item; the manager must explicitly call `review_report()`
with accept, reject, request_retry, escalate, or request_human_review. Workers
execute assigned tasks, submit artifacts, and report to their direct manager.
The CEO can report to the human operator with `report_to_human`.

## MCP Tools

Core communication tools:

- `assign`: enqueue a task for a managed subordinate.
- `report`: submit a structured report to the direct manager and enqueue a
  manager review item.
- `review_report`: record an explicit manager decision for a subordinate report.
- `report_to_human`: CEO-visible human report.
- `discuss`: enqueue a message to another agent inbox.
- `check_progress`: ask a subordinate for progress and enqueue a subordinate
  inbox notice.
- `get_task_dossier`: read requirements, task docs, artifacts, reports, and
  division of work.
- `upsert_task_doc`: write task requirements, decisions, notes, risks, and
  work splits.
- `request_tool`: request a new tool when repeated manual work is detected.
- `get_inbox`, `claim_inbox`, `complete_inbox`, `fail_inbox`: inspect and
  process per-agent inbox items.

Work queue tools coordinate `llm_request`, `tool_call`, and `worker_run` items
with the limits in `queue`.

## External MCP Servers

External MCP servers should be added to `external_mcp.servers` instead of being
added directly to each worker. Workforce Runtime exposes each remote tool as a
local clone through the internal MCP server, using `<tool_prefix>__<remote_tool>`
names. Agents call the clone tool; Workforce Runtime checks permissions, queues
the call as a `tool_call`, invokes the external MCP server centrally, records
events, and returns the remote result.

Example:

```json
{
  "id": "github_copilot",
  "enabled": true,
  "transport": "http",
  "url": "https://api.githubcopilot.com/mcp/",
  "tool_prefix": "github",
  "auth": {"type": "bearer", "token_env": "GITHUB_PAT_TOKEN"},
  "allowed_agent_ids": ["*"],
  "allowed_tools": ["*"],
  "queue": {"enabled": true}
}
```

Authentication is configured by environment-variable names, not secret values.
Supported auth modes are:

- `{"type": "bearer", "token_env": "GITHUB_PAT_TOKEN"}`;
- `{"type": "header", "header": "X-API-Key", "value_env": "SERVICE_API_KEY"}`;
- `{"type": "oauth", "access_token_env": "SERVICE_ACCESS_TOKEN"}`;
- OAuth client credentials with `token_url`, `client_id_env`,
  `client_secret_env`, and optional `scope`.

OAuth authorization-code flows are supported from both the dashboard and the
CLI. The dashboard uses the running dashboard server as the callback endpoint
(`/api/settings/mcp/oauth/callback/...`), so Docker deployments should redirect
back to the exposed dashboard port, usually `http://127.0.0.1:8765`. The CLI
flow uses MCP well-known OAuth metadata discovery, starts a local loopback
callback server, opens the authorization URL, exchanges the callback code, and
stores tokens under `.workforce_runtime/secrets/external_mcp_oauth.json`.
Override that path with `WORKFORCE_EXTERNAL_MCP_OAUTH_STORE` when needed.

Common commands:

```bash
workforce-runtime mcp external probe --url https://example.com/mcp
workforce-runtime mcp external login github --url https://example.com/mcp
workforce-runtime mcp external connect --id github --url https://example.com/mcp --tool-prefix github
workforce-runtime mcp external connect --id github_copilot --url https://api.githubcopilot.com/mcp/ --bearer-token-env GITHUB_PAT_TOKEN
```

The `external_mcp.oauth` config section controls default callback URL, callback
port, and login timeout for CLI flows. Leave `callback_url` empty for dashboard
OAuth unless you intentionally proxy the dashboard behind another public URL.

## Storage And Queues

MySQL is the durable source of truth for agents, tasks, reports, events,
artifacts, work queue items, and inbox item status. RabbitMQ carries delivery
messages for each agent's inbox. If RabbitMQ is temporarily unavailable,
durable inbox rows in MySQL still record the intended delivery state.

The work queue controls how many worker runs, model calls, and runtime-mediated
tool calls can execute concurrently. Native tools inside Codex or Claude Code
are controlled by those CLIs and by the configured process sandbox, not by the
runtime work queue unless those operations are routed through Workforce MCP
tools.

## Codex And Claude Code Workers

Codex and Claude Code are launched as external processes. Workforce Runtime
generates the task prompt, starts the process in a workspace, streams stdout and
stderr into events, records run metadata, collects artifacts, and expects MCP
reports.

Codex with OpenRouter uses the `workforce-openrouter` profile:

```toml
model = "openai/gpt-oss-120b:free"
model_provider = "openrouter"

[model_providers.openrouter]
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
```

Recommended shape:

```bash
codex --profile workforce-openrouter -a never -s workspace-write exec --json --cd <workspace> -
```

Claude Code can be launched through its installed CLI or through router tooling
when configured outside the repository. Workforce Runtime only records the
worker command and results; provider credentials remain in the user's
environment.

## Sandboxing

`execution.mode` controls process execution:

- `full_access`: preserve current worker behavior.
- `sandbox`: prepend `execution.sandbox.command_prefix` to Codex and Claude
  worker processes.

This is process-level containment. Runtime MCP tool calls can also be queued
when `execution.sandbox.queue_mcp_tools` is enabled. Native Codex or Claude
tools are not individually visible to Workforce Runtime unless the worker is
configured to use Workforce MCP tools for those actions.

## Dashboard Operation

Simple mode:

- new task input;
- expandable config controls;
- `Design Org`;
- `Run` after a draft exists;
- human CEO reports;
- per-task ELK organization tree;
- click any agent card to open details and streams;
- selecting an existing task turns the input into a CEO chat/steer message.

Debug mode:

- full org tree;
- internal manager reports;
- agent, task, run, report tables;
- live agent output;
- replay and trajectories;
- raw runtime config load/save;
- demo and benchmark launchers;
- trace export.

## Docker Packaging

The service image runs the dashboard by default. The image contains the
container-specific config at `/app/workforce_runtime_config.json`, where MySQL
and RabbitMQ point to the compose service names `workforce-mysql` and
`workforce-rabbitmq`.

```bash
docker build -t workforce-runtime:local .
docker run --rm -p 8765:8765 \
  -e OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  -e NVIDIA_API_KEY="$NVIDIA_API_KEY" \
  workforce-runtime:local
```

For a complete local stack, use Docker Compose:

```bash
docker compose up -d --build
```

Set `WORKFORCE_RUNTIME_PORT` before running Compose or the installer if host
port `8765` is already in use:

```bash
WORKFORCE_RUNTIME_PORT=8877 docker compose up -d --build
```

Or use the one-click installer:

```bash
scripts/install_workforce_runtime.sh
```

The installer builds `workforce-runtime:local`, starts MySQL, RabbitMQ, and the
dashboard service, waits for `/healthz`, and prints the dashboard URL.

## Verification

Before changing runtime behavior, run:

```bash
uv run pytest
```

For dashboard-only changes:

```bash
uv run pytest tests/test_web_dashboard.py -q
```

For service checks:

```bash
curl http://127.0.0.1:8765/healthz
```
