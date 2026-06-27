# Workforce Runtime

Workforce Runtime is an organization-level control plane for AI workforces. It
stores the company, agents, tasks, reports, events, queues, traces, budgets, and
dashboard state around third-party workers such as Codex, Claude Code, generic
CLI workers, and other external executors. Runtime-level OpenRouter API calls
are reserved for lightweight summaries, model metadata, and legacy eval
harnesses; real decision-making agents run through Codex or Claude Code.

![Workforce Runtime animation: a single agent grows into an organized AI workforce](docs/workforce-runtime-animation.gif)

[Standalone HTML animation source](docs/Workforce%20Runtime%20Animation%20%28standalone%29.html).

The single current operational guide is
[docs/WORKFORCE_RUNTIME_GUIDE.md](docs/WORKFORCE_RUNTIME_GUIDE.md). Use that
guide for installation, MySQL/RabbitMQ setup, configuration, dashboard usage,
MCP tools, worker integration, sandboxing, Docker packaging, and verification.
It explains what Workforce Runtime is, what it is not, and how to operate it.

## Public Alpha Quickstart

Install locally:

```bash
python3 -m pip install --user uv
uv venv
source .venv/bin/activate
uv sync --extra dev
```

Run the packaged demo:

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite demo sample-repo-fix
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --serve
```

Run with the unified JSON config and default MySQL/RabbitMQ backend:

```bash
cp examples/workforce_runtime_config.json workforce_runtime_config.json
workforce-runtime --config workforce_runtime_config.json dashboard --serve
```

The dashboard defaults to `http://127.0.0.1:8765`.

## What Workforce Runtime Is

Workforce Runtime is the management and governance layer above worker agents. It
provides org charts, task contracts, reports, budgets, permissions, artifacts,
queues, traces, benchmarks, and human-readable dashboards.

## What Workforce Runtime Is Not

It is not a replacement for Codex or Claude Code, not a custom coding-agent
loop, not a prompt framework, and not a browser automation product. The worker
is replaceable; the organization is the product.

## How It Differs From Ordinary Agent Frameworks

Ordinary agent frameworks usually focus on one worker loop: prompting, tool
choice, planning, and execution. Workforce Runtime focuses on the layer above:
who owns a task, who reports to whom, which worker should be reused, how many
agents may run concurrently, where artifacts and traces live, and how the human
operator reviews outcomes.

## Defining An Org Chart

Use a YAML org file with a `company` section and an `agents` list. Each agent
has an id, role, manager, worker type, responsibilities, permissions, budget,
and model metadata. See `examples/simple_engineering_org/org.yaml`.

```bash
workforce-runtime --db .workforce_runtime/runtime.sqlite init --org examples/simple_engineering_org/org.yaml
workforce-runtime --db .workforce_runtime/runtime.sqlite org print examples/simple_engineering_org/org.yaml
```

## Adding A Worker Adapter

Worker adapters translate a structured `TaskContract` into an external executor
invocation, run it in a workspace, stream output into events, collect artifacts,
and register a final report. Start with `workforce_runtime/workers/` and the
adapter notes in the guide.

## MCP Reporting

Workers communicate with the runtime through MCP tools. The core organization
tools include `assign`, `report`, `review_report`, `report_to_human`, `discuss`,
`check_progress`, `get_task_dossier`, `upsert_task_doc`, `request_tool`, and
per-agent inbox tools. Queue tools coordinate worker runs, model requests, and
runtime-mediated tool calls under configured concurrency limits.

## Codex And Claude Code

Codex and Claude Code are launched as external worker processes. Workforce
Runtime generates task prompts, starts the process, streams output, records
run metadata, collects artifacts, and expects MCP reports. Keep provider keys
such as `OPENROUTER_API_KEY` and `NVIDIA_API_KEY` in the environment, not in
repo files.

## Current Docs

- [docs/WORKFORCE_RUNTIME_GUIDE.md](docs/WORKFORCE_RUNTIME_GUIDE.md): the
  single current guide.
- `QUICKSTART.md`, `MCP_TOOLS.md`, `WORKER_ADAPTERS.md`, `EXAMPLES.md`, and
  `ROADMAP.md`: retained public-alpha references.
