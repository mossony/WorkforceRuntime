# Workforce Runtime

**Workforce Runtime** is an organization-level runtime for AI workforces.

It treats agents as employees, tasks as contracts, reports as structured management communication, budgets as payroll, and tools as governed capabilities.

The goal is not to build another coding agent, another agent loop, or another prompt framework. The goal is to build the governance layer above existing agents: a runtime that can organize, delegate, supervise, budget, evaluate, and coordinate large numbers of third-party worker agents in a way that resembles a human company.

In the initial version, Workforce Runtime does not implement its own agent loop. All worker agents are third-party executors such as **Codex** and **Claude Code**. Workforce Runtime provides the organizational structure, task lifecycle, reporting protocol, MCP interface, worker adapters, budget control, permission model, and human-readable execution logs.

## Public Alpha Quickstart

This public alpha README explains what Workforce Runtime is, what it is not, how to run the demo, how to define an org chart, how to add a worker adapter, how MCP reporting works, and how Codex and Claude Code fit into the architecture.

Install the package in editable mode:

```bash
python3 -m pip install --user uv  # only if uv is not already installed
uv venv
source .venv/bin/activate
uv sync --extra dev
```

Run the packaged mock-worker demo:

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite demo sample-repo-fix
```

Run the smaller status/trajectory demo:

```bash
workforce-runtime --db .workforce_runtime/simple.sqlite demo simple-status
```

Run the web research demo, which fetches a public IANA page and shows MCP tool-call activity:

```bash
workforce-runtime --db .workforce_runtime/web.sqlite demo web-research --workspace .workforce_runtime/demo/web-research
```

Inspect the resulting organization dashboard:

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard
workforce-runtime --db .workforce_runtime/web.sqlite dashboard --serve
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --watch --iterations 5
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --replay
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard --trajectories
```

Copy the unified runtime config template when you want to adjust models,
dashboard behavior, worker launch settings, benchmark defaults, or OpenRouter
settings from one file:

```bash
cp examples/workforce_runtime_config.json workforce_runtime_config.json
workforce-runtime --config workforce_runtime_config.json --db .workforce_runtime/demo.sqlite dashboard --serve
```

The parser demo creates a small git workspace, assigns a planning task to an engineering manager, delegates a parser bug fix to a worker, receives MCP reports and artifacts, runs manager review, and prints a text dashboard. The smaller status demo uses management agents routed to `openai/gpt-oss-120b:free` and a terminal worker routed to `poolside/laguna-xs.2:free` as local metadata, then prints dashboard snapshots, event replay, and per-agent trajectories. The web research demo uses real network access plus MCP `assign`, `check_progress`, `get_task_context`, `discuss`, `submit_artifact`, and `report` calls.

Worker adapters stream stdout/stderr into runtime events while they run, and manager-style external executors can stream `agent_output` events. The web dashboard shows an org chart, per-agent output, per-agent MCP tool activity, agent run state, event replay, and trajectories. If the local Codex desktop app is installed, Codex workers use its app icon in the org chart.

Design an organization from a short goal, or run a benchmark case with real OpenRouter calls:

```bash
workforce-runtime org design --goal "Research a public web source and produce an evidence-backed artifact" --use-llm
workforce-runtime --db .workforce_runtime/benchmark.sqlite benchmark run \
  --case examples/benchmarks/web_research_real_llm.json \
  --workspace .workforce_runtime/benchmark/workspace \
  --use-llm --judge heuristic
```

## How It Differs From Ordinary Agent Frameworks

Most agent frameworks focus on the worker loop: prompting, tool choice, planning, and execution. Workforce Runtime focuses on the management layer above workers: org charts, task contracts, reporting lines, budget limits, permissions, artifacts, review, and audit logs. Codex, Claude Code, or another CLI agent can be plugged in as replaceable workers.

## Core Files

- `examples/simple_engineering_org/org.yaml` defines the sample company, HR, managers, workers, budgets, and permissions.
- `workforce_runtime/server/runtime.py` owns task assignment, reports, artifacts, reviews, budget checks, and permission checks.
- `workforce_runtime/mcp/server.py` exposes worker-facing MCP tools.
- `workforce_runtime/workers/` contains the generic CLI, Codex, and Claude Code adapters.
- `workforce_runtime/org_designer.py` creates a small organization from a goal with an optional OpenRouter LLM pass.
- `workforce_runtime/evals/benchmark.py` runs JSON benchmark cases and scores task completion, artifacts, communication efficiency, and org design.
- `workforce_runtime/dashboard/text_dashboard.py` renders the local text dashboard.

## Defining An Org Chart

Create a YAML file with a `company` section and an `agents` list. Each agent has an `id`, `name`, `role`, `department`, `manager_id`, `worker_type`, `responsibilities`, `permissions`, and `budget`. See `examples/simple_engineering_org/org.yaml`.

Initialize a runtime database:

```bash
workforce-runtime --db .workforce_runtime/runtime.sqlite init --org examples/simple_engineering_org/org.yaml
workforce-runtime --db .workforce_runtime/runtime.sqlite org print examples/simple_engineering_org/org.yaml
```

Generate a new org from a short goal:

```bash
workforce-runtime org design \
  --goal "Research a public RFC and produce an evidence-backed summary" \
  --headcount-limit 6 \
  --use-llm \
  --out .workforce_runtime/designed_org.yaml
```

The dashboard also has a `Start Real LLM Benchmark` button that runs `examples/benchmarks/web_research_real_llm.json` against the current dashboard database.

## Adding A Worker Adapter

Worker adapters translate a `TaskContract` into an external executor invocation, run inside a workspace, capture stdout/stderr, collect artifacts, and register a `ReportContract`. Start from `GenericCLIWorker` if the executor can read environment variables and call the MCP server. See `WORKER_ADAPTERS.md`.

## MCP Reporting

Workers report through line-delimited JSON-RPC over stdio:

```bash
workforce-runtime --db .workforce_runtime/runtime.sqlite mcp serve
```

The current organization tools are `report`, `assign`, `discuss`, `check_progress`, `hire`, and `update_system_prompt`. The worker support tools are `submit_artifact`, `update_status`, `request_budget`, `request_permission`, `get_task_context`, and `get_org_context`. See `MCP_TOOLS.md`.

Default system prompts are generated by Workforce Runtime from the company mission, role, manager, responsibilities, and permissions when the org is loaded or HR hires a new agent. A manager can edit the system prompt of any subordinate through `update_system_prompt`; the edit is recorded as an event.

## Codex And Claude Code

Codex and Claude Code are treated as third-party worker processes. Workforce Runtime does not replace their native agent loops; it launches them with a structured task prompt, captures their outputs, records artifacts, and expects final reports. The Codex adapter defaults to the `workforce-openrouter` profile, which can point official Codex at OpenRouter's `openai/gpt-oss-120b:free` model. See `docs/CODEX_AGENT_INTEGRATION.md` and `WORKER_ADAPTERS.md`.

## Alpha Docs

- `QUICKSTART.md`: install, demo, dashboard, and first inspection commands.
- `MCP_TOOLS.md`: worker-facing tool protocol.
- `WORKER_ADAPTERS.md`: generic CLI, Codex, Claude Code, and adapter extension notes.
- `EXAMPLES.md`: packaged demo and example org.
- `ROADMAP.md`: current alpha scope and next milestones.

---

## 1. Vision

The next stage of agentic systems will not be a single assistant sitting in a chat box.

It will be an AI workforce.

A human user should be able to act as the CEO, founder, manager, or project owner of a structured AI organization. Under that user, there may be VP agents, manager agents, specialist agents, worker agents, reviewer agents, researcher agents, and operational agents.

These agents should not simply chat with one another. They should operate under a governance system:

* Each agent has a role.
* Each agent has a manager.
* Each agent has a budget.
* Each agent has permissions.
* Each agent has tools.
* Each agent has responsibilities.
* Each agent receives tasks through structured contracts.
* Each agent reports upward through structured reports.
* Each agent produces artifacts.
* Each agent can be evaluated by outcome, cost, reliability, and usefulness.
* The organization can create new agents, suspend agents, change reporting lines, or reallocate budget.

The long-term idea is that an individual human or a small team can operate a much larger AI-native organization.

---

## 2. Core Thesis

Today’s coding agents are becoming increasingly capable at implementation-level work:

* reading a codebase
* editing files
* fixing bugs
* writing tests
* running commands
* opening pull requests
* responding to review comments
* debugging CI failures

But as individual agents become stronger, the bottleneck moves upward.

The hard problem becomes:

How do we coordinate many agents over long-running goals, with budgets, permissions, reporting, accountability, organizational memory, and human oversight?

Workforce Runtime focuses on this higher layer.

It assumes that the underlying worker agents will keep improving. Codex, Claude Code, Gemini CLI, Cursor agents, open-source coding agents, and internal company agents can all become stronger over time.

Workforce Runtime is designed to survive model progress by staying above the worker layer.

The worker is replaceable.

The organization is the product.

---

## 3. What Workforce Runtime Is

Workforce Runtime is an **Agent Organization Runtime**.

It provides:

* an organization graph
* agent employee profiles
* structured task contracts
* structured reporting contracts
* budget management
* permission management
* capability assignment
* MCP-based inter-agent communication
* worker agent spawning
* worker adapters
* artifact collection
* task ledger
* execution logs
* human-readable status reports
* simple text-based dashboard
* future support for human-agent hybrid organizations

---

## 4. What Workforce Runtime Is Not

Workforce Runtime is not:

* a new foundation model
* a new coding model
* a new coding assistant
* a replacement for Codex or Claude Code
* a replacement for LangGraph, AutoGen, CrewAI, or similar frameworks
* a prompt-only multi-agent demo
* a toy simulation of a company
* a dashboard-only product

It is a governance and execution runtime for organizing existing agents as a structured workforce.

---

## 5. Product Positioning

A simple positioning statement:

> Workforce Runtime is an operating system for AI workforces.

A more precise statement:

> Workforce Runtime lets a human or company manage third-party AI agents as structured employees, with org charts, budgets, permissions, reporting, task contracts, and auditable execution.

A developer-facing statement:

> Workforce Runtime is a server and MCP runtime that spawns worker agents such as Codex and Claude Code, assigns them structured tasks, receives structured reports through MCP tools, tracks budget and permissions, and produces human-readable organization-level summaries.

---

## 6. Key Design Principle

### Do not compete with worker agents.

Workforce Runtime should not try to be better than Codex at coding or better than Claude Code at repository understanding.

Instead, Workforce Runtime should treat these systems as workers.

The runtime should answer questions such as:

* Who should do this task?
* What authority should this agent have?
* What budget should this agent receive?
* What tools can this agent use?
* What context should this agent receive?
* What does this task serve at the company level?
* How should this agent report back?
* Has this agent completed the task?
* What artifact was produced?
* Was the cost justified?
* Should this task be escalated?
* Should this agent be promoted, suspended, retried, or replaced?

---

## 7. Initial Scope

The initial MVP should be intentionally small.

### Initial supported worker agents

The first version only needs to support:

* Codex
* Claude Code

Both are treated as third-party worker agents.

Workforce Runtime does not implement the internal reasoning loop of either worker. It simply spawns them, gives them task contracts, gives them access to tools, receives artifacts, and collects reports.

### Initial supported use cases

The first version can focus on engineering tasks:

* turn a GitHub issue into a pull request
* fix a failing test
* summarize a repository
* resolve a review comment
* produce a technical report
* investigate a bug
* generate a design proposal
* run a simple code migration

The goal of the MVP is not to automate a whole company immediately.

The goal is to prove the organization protocol:

Human CEO → VP agent → Manager agent → Worker agent → artifact → report → upward summary.

---

## 8. High-Level Architecture

The initial architecture is a service.

The service contains:

* a core runtime
* an organization graph
* an MCP server
* a worker spawner
* worker adapters
* a task ledger
* a report ledger
* a budget manager
* a permission manager
* a simple text dashboard

```text
+------------------------------------------------------+
|                    Human User                        |
|        CEO / Founder / Manager / Project Owner       |
+---------------------------+--------------------------+
                            |
                            v
+------------------------------------------------------+
|                Workforce Runtime Server              |
|                                                      |
|  +-------------------+   +------------------------+  |
|  | Organization Graph|   | Task / Report Ledger   |  |
|  +-------------------+   +------------------------+  |
|                                                      |
|  +-------------------+   +------------------------+  |
|  | Budget Manager    |   | Permission Manager     |  |
|  +-------------------+   +------------------------+  |
|                                                      |
|  +-------------------+   +------------------------+  |
|  | MCP Server        |   | Worker Spawner         |  |
|  +-------------------+   +------------------------+  |
|                                                      |
|  +-------------------+   +------------------------+  |
|  | Text Dashboard    |   | Artifact Store         |  |
|  +-------------------+   +------------------------+  |
+---------------------------+--------------------------+
                            |
                            v
+------------------------------------------------------+
|                 Worker Agent Layer                   |
|                                                      |
|  +-------------------+   +------------------------+  |
|  | Codex Worker      |   | Claude Code Worker     |  |
|  +-------------------+   +------------------------+  |
|                                                      |
|  Future: Gemini CLI, Cursor, SWE-agent, human worker |
+------------------------------------------------------+
                            |
                            v
+------------------------------------------------------+
|                     Tools / World                    |
|                                                      |
|  GitHub, local repo, shell, MCP tools, CI, files,    |
|  docs, Slack, Jira, databases, internal systems      |
+------------------------------------------------------+
```

---

## 9. Server-Centric Runtime

The initial system should run as a local or hosted service.

Example:

```bash
workforce-runtime --config workforce_runtime_config.json --db .workforce_runtime/runtime.sqlite init --org examples/simple_engineering_org/org.yaml
workforce-runtime --config workforce_runtime_config.json --db .workforce_runtime/runtime.sqlite dashboard --serve
```

The server owns:

* organization state
* task state
* budget state
* permissions
* worker spawning
* report collection
* event logging
* artifact storage
* MCP tools
* human-readable summaries

The server can expose:

* MCP server interface
* CLI interface
* local HTTP API
* file-based text dashboard
* optional future web dashboard

The first version does not need a fancy dashboard. A readable text output is enough.

---

## 10. MCP as the Communication Layer

Workforce Runtime should expose an MCP server.

Agents can use MCP tools to interact with the organization runtime.

The most important MCP tools are organizational tools, not ordinary coding tools.

Example tools:

* `report`
* `request_budget`
* `request_permission`
* `delegate_task`
* `create_task`
* `complete_task`
* `escalate`
* `ask_manager`
* `submit_artifact`
* `request_worker`
* `hire_agent`
* `update_status`
* `get_org_context`
* `get_task_context`
* `get_budget_status`
* `get_direct_reports`
* `get_company_goal`

This is important because worker agents need a stable way to communicate with the organization.

Instead of asking a worker agent to “please report to your manager” in natural language, Workforce Runtime gives it a real tool:

```text
report(...)
```

The report becomes a structured event in the runtime ledger.

---

## 11. Example MCP Tool: report

The `report` tool is the central communication primitive.

A subordinate agent uses it to report progress to its manager.

Example schema:

```json
{
  "tool": "report",
  "arguments": {
    "from_agent_id": "agent_worker_001",
    "to_agent_id": "agent_manager_001",
    "task_id": "task_123",
    "summary": "I fixed the failing unit test by correcting the input validation logic.",
    "status": "completed",
    "work_done": [
      "Inspected the failing test output",
      "Located the validation bug in src/validator.py",
      "Applied a minimal patch",
      "Ran pytest tests/test_validator.py"
    ],
    "evidence": [
      {
        "type": "test_log",
        "path": "artifacts/task_123/pytest.log"
      },
      {
        "type": "git_diff",
        "path": "artifacts/task_123/diff.patch"
      }
    ],
    "risks": [
      "The fix only covers the failing test case. Broader validation behavior may need additional tests."
    ],
    "blockers": [],
    "confidence": 0.82,
    "cost": {
      "tokens_used": 18432,
      "runtime_seconds": 312,
      "tool_calls": 9
    },
    "next_action": "Ready for manager review.",
    "requires_decision": false
  }
}
```

The manager agent should not need to inspect the entire worker transcript. It should receive the structured report, inspect the artifact if necessary, and then either accept, reject, retry, escalate, or delegate further.

---

## 12. Example MCP Tool: delegate_task

A manager agent can delegate a task to a subordinate agent.

```json
{
  "tool": "delegate_task",
  "arguments": {
    "from_agent_id": "agent_manager_001",
    "to_agent_id": "agent_worker_002",
    "parent_task_id": "task_100",
    "objective": "Investigate why the authentication test suite is failing.",
    "context_refs": [
      {
        "type": "repo",
        "path": "/workspace/project"
      },
      {
        "type": "log",
        "path": "artifacts/ci/failure.log"
      }
    ],
    "constraints": [
      "Do not modify production configuration files.",
      "Do not change test expectations unless there is clear evidence the test is wrong."
    ],
    "acceptance_criteria": [
      "Root cause is identified.",
      "A proposed patch is produced or a clear explanation is given.",
      "Relevant tests are run."
    ],
    "budget": {
      "max_tokens": 50000,
      "max_runtime_seconds": 1200,
      "max_tool_calls": 50
    },
    "risk_level": "medium"
  }
}
```

---

## 13. Example MCP Tool: request_budget

Agents should not have unlimited compute.

They should request budget when needed.

```json
{
  "tool": "request_budget",
  "arguments": {
    "agent_id": "agent_worker_003",
    "task_id": "task_456",
    "requested_budget": {
      "additional_tokens": 30000,
      "additional_runtime_seconds": 900,
      "additional_tool_calls": 20
    },
    "reason": "The initial investigation found that the bug spans three services. Additional budget is needed to inspect the second service and run integration tests.",
    "expected_value": "Higher chance of producing a verified patch instead of only a diagnostic report."
  }
}
```

The manager can approve, deny, or modify the request.

---

## 14. Example MCP Tool: request_permission

Workers should not automatically receive all tools.

```json
{
  "tool": "request_permission",
  "arguments": {
    "agent_id": "agent_worker_004",
    "task_id": "task_789",
    "requested_permission": "write_files",
    "scope": {
      "repo": "example/project",
      "allowed_paths": [
        "src/auth/**",
        "tests/auth/**"
      ]
    },
    "reason": "The task requires editing the authentication module and adding tests.",
    "risk_level": "medium"
  }
}
```

This creates a permission request event.

The manager or human can approve it.

---

## 15. Agent as Employee

Each agent is represented as an employee-like entity.

Example:

```yaml
agents:
  - id: ceo
    name: CEO Agent
    role: CEO
    department: Executive
    manager: null
    model_worker: null
    responsibilities:
      - Own company-level objective
      - Decide priorities
      - Allocate budget
      - Review executive reports
    permissions:
      - approve_budget
      - create_department
      - hire_agent
      - terminate_agent
    budget:
      daily_tokens: 100000
      daily_runtime_seconds: 7200

  - id: vp_engineering
    name: VP Engineering Agent
    role: VP Engineering
    department: Engineering
    manager: ceo
    worker_type: claude_code
    responsibilities:
      - Break engineering goals into projects
      - Manage engineering managers
      - Summarize engineering execution
      - Escalate strategic technical decisions
    permissions:
      - delegate_task
      - approve_small_budget
      - request_hiring
    budget:
      daily_tokens: 200000
      daily_runtime_seconds: 14400

  - id: backend_worker_1
    name: Backend Worker Agent
    role: Software Engineer
    department: Engineering
    manager: eng_manager_backend
    worker_type: codex
    responsibilities:
      - Modify backend code
      - Run tests
      - Produce patches
      - Report progress
    permissions:
      - read_repo
      - write_branch
      - run_tests
    budget:
      daily_tokens: 50000
      daily_runtime_seconds: 3600
```

An agent profile should contain:

* identity
* role
* department
* manager
* direct reports
* responsibilities
* allowed tools
* budget
* performance history
* current tasks
* permissions
* memory references
* status

Possible statuses:

* active
* idle
* busy
* blocked
* suspended
* probation
* terminated

---

## 16. Organization Graph

The organization graph is a first-class object.

It defines reporting relationships.

Example:

```text
Human CEO
└── CEO Agent
    ├── VP Engineering Agent
    │   ├── Backend Manager Agent
    │   │   ├── Backend Worker 1
    │   │   └── Backend Worker 2
    │   ├── Frontend Manager Agent
    │   │   ├── Frontend Worker 1
    │   │   └── Frontend Worker 2
    │   └── QA Manager Agent
    │       ├── Test Worker 1
    │       └── Test Worker 2
    ├── VP Research Agent
    │   ├── Research Worker 1
    │   └── Research Worker 2
    └── Chief of Staff Agent
```

The organization graph is used for:

* task delegation
* report routing
* budget approval
* escalation
* accountability
* permission inheritance
* performance aggregation

---

## 17. Why Hierarchy Matters

A flat swarm of agents does not scale well.

If every agent talks to every other agent, communication cost grows rapidly.

Hierarchy provides:

* information compression
* responsibility boundaries
* escalation paths
* budget ownership
* role clarity
* task decomposition
* human readability

But hierarchy also creates risk:

* goal drift
* information loss
* bureaucratic overhead
* duplicated work
* over-compression
* false confidence
* slow escalation

Workforce Runtime should treat hierarchy as a governance structure, not as a rigid copy of human companies.

The structure can evolve over time.

---

## 18. Goal Lineage

Every task should preserve its parent goal.

This is critical for preventing objective drift.

Example:

```text
company_goal_001:
  "Ship a working MVP of the developer analytics dashboard."

task_010:
  parent: company_goal_001
  objective: "Design the backend API."

task_011:
  parent: task_010
  objective: "Inspect existing authentication middleware."

task_012:
  parent: task_011
  objective: "Check whether the dashboard API can reuse the current auth system."
```

Every report should include an alignment check:

```json
{
  "alignment_check": {
    "parent_goal_id": "task_010",
    "explanation": "This investigation determines whether the new dashboard API can reuse the existing authentication system, which is necessary before implementing the backend API."
  }
}
```

This helps the runtime answer:

* Why is this agent doing this?
* Which higher-level goal does this support?
* Is this work still relevant?
* Has the task drifted away from the original objective?

---

## 19. Task as Contract

Tasks should not be loose natural language instructions.

A task is a contract.

```json
{
  "task_id": "task_123",
  "title": "Fix failing authentication test",
  "objective": "Identify and fix the failing authentication unit test.",
  "assigned_to": "backend_worker_1",
  "assigned_by": "backend_manager",
  "parent_task_id": "task_100",
  "context_refs": [
    {
      "type": "repo",
      "path": "/workspace/project"
    },
    {
      "type": "ci_log",
      "path": "artifacts/ci/auth_failure.log"
    }
  ],
  "constraints": [
    "Do not modify public API behavior without approval.",
    "Do not delete tests.",
    "Do not change test expectations unless the test is clearly invalid."
  ],
  "acceptance_criteria": [
    "The failing test passes.",
    "The relevant test suite is run.",
    "A minimal patch is produced.",
    "A report is submitted to the manager."
  ],
  "budget": {
    "max_tokens": 50000,
    "max_runtime_seconds": 1200,
    "max_tool_calls": 50
  },
  "risk_level": "medium",
  "required_artifacts": [
    "git_diff",
    "test_log",
    "worker_report"
  ],
  "status": "assigned"
}
```

---

## 20. Report as Compression

A report is not a transcript.

A report is a compressed management object.

Good reports should include:

* conclusion
* work completed
* evidence
* risks
* blockers
* cost
* confidence
* next action
* decision request
* alignment with parent goal

Managers should be able to aggregate reports without reading full worker histories.

---

## 21. Budget as Payroll

Token budget is the payroll of the AI workforce.

Each agent should have:

* daily token budget
* per-task token budget
* runtime budget
* tool-call budget
* model budget
* human-attention budget

Example:

```yaml
budget_policy:
  default_worker_budget:
    max_tokens_per_task: 50000
    max_runtime_seconds_per_task: 1200
    max_tool_calls_per_task: 50

  manager_budget:
    max_tokens_per_day: 200000
    max_runtime_seconds_per_day: 14400

  approval_thresholds:
    budget_request_above_tokens: 100000
    requires_human_approval: true
```

Budget is used to prevent:

* runaway agents
* infinite loops
* unnecessary parallelism
* excessive expensive-model usage
* low-value exploration

Managers should be evaluated partly by how effectively they allocate budget.

---

## 22. Permissions as Capabilities

Agents should not have unlimited access.

A worker agent receives capabilities for a task.

Example capabilities:

* read files
* write files
* run tests
* run shell commands
* access GitHub
* open pull request
* comment on pull request
* access Slack
* access Jira
* request human approval
* spawn subtask
* delegate task
* approve budget
* access secrets

Example:

```yaml
capabilities:
  backend_worker_1:
    - read_repo
    - write_branch
    - run_tests
    - submit_artifact
    - report

  backend_manager:
    - delegate_task
    - approve_small_budget
    - review_report
    - escalate
    - request_permission

  ceo:
    - approve_large_budget
    - hire_agent
    - terminate_agent
    - change_org_chart
```

Permissions should be scoped by:

* task
* repo
* path
* tool
* time window
* budget
* risk level

---

## 23. Worker Adapter Layer

Worker agents are third-party execution engines.

Workforce Runtime should support multiple worker types through adapters.

Initial adapters:

* `CodexWorkerAdapter`
* `ClaudeCodeWorkerAdapter`

Future adapters:

* `GeminiCLIWorkerAdapter`
* `CursorWorkerAdapter`
* `SWEAgentWorkerAdapter`
* `OpenAIAgentsSDKAdapter`
* `ClaudeAgentSDKAdapter`
* `GenericCLIWorkerAdapter`
* `HumanWorkerAdapter`

The adapter interface should normalize all workers into the same lifecycle:

```python
class WorkerAdapter:
    def declare_capabilities(self) -> WorkerCapabilities:
        ...

    def start_task(self, task: TaskContract, runtime_context: RuntimeContext) -> WorkerRun:
        ...

    def stream_events(self, run_id: str) -> Iterator[WorkerEvent]:
        ...

    def collect_artifacts(self, run_id: str) -> list[Artifact]:
        ...

    def stop_task(self, run_id: str) -> None:
        ...

    def get_usage(self, run_id: str) -> Usage:
        ...
```

The runtime should not assume that every worker supports structured streaming.

Some workers may support full structured events.

Some workers may only produce stdout, logs, files, diffs, or exit codes.

The adapter layer should support degraded modes.

---

## 24. Worker Modes

### Native structured mode

The worker supports structured API calls, tool use, streaming events, and structured reports.

This is the best mode.

### Artifact mode

The worker may not report cleanly, but it produces artifacts:

* git diff
* files
* logs
* test output
* stdout
* stderr
* exit code

A separate report synthesizer can turn artifacts into structured reports.

### Black-box mode

The worker only accepts a prompt and returns text.

This worker should receive low-risk tasks only.

It should have limited permissions and strict budget.

---

## 25. Codex Worker

The Codex worker can be used as a third-party coding worker.

The runtime should spawn Codex in a controlled workspace.

Example lifecycle:

1. Create isolated worktree.
2. Prepare task prompt from `TaskContract`.
3. Provide MCP server URL or config.
4. Start Codex process.
5. Codex performs work.
6. Codex calls MCP tools such as `report`, `submit_artifact`, or `request_permission`.
7. Runtime collects diff, logs, test results, and reports.
8. Runtime evaluates task outcome.
9. Manager agent receives summary.

Example command shape:

```bash
codex exec --profile workforce-local "Complete the assigned Workforce Runtime task. Use the MCP report tool when finished."
```

The exact command should remain adapter-specific.

The runtime should not depend on Codex internals.

---

## 26. Claude Code Worker

Claude Code can also be used as a third-party worker.

Example lifecycle:

1. Create isolated workspace.
2. Generate task prompt.
3. Attach MCP server.
4. Start Claude Code in non-interactive or automation mode.
5. Claude Code edits files, runs commands, and uses MCP tools.
6. Runtime records events and artifacts.
7. Runtime generates manager-readable report.

The runtime should treat Claude Code as a worker process, not as the organization itself.

---

## 27. MCP Tool Design

The Workforce Runtime MCP server should expose tools grouped by function.

### Reporting tools

* `report`
* `update_status`
* `submit_artifact`
* `complete_task`

### Management tools

* `delegate_task`
* `create_subtask`
* `escalate`
* `ask_manager`
* `request_review`

### Budget tools

* `get_budget_status`
* `request_budget`
* `release_budget`

### Permission tools

* `get_permissions`
* `request_permission`
* `check_permission`

### Organization tools

* `get_org_chart`
* `get_manager`
* `get_direct_reports`
* `get_role`
* `get_company_goal`

### Hiring tools

* `request_worker`
* `hire_agent`
* `suspend_agent`
* `terminate_agent`

### Memory tools

* `write_memory`
* `read_memory`
* `search_memory`

The MVP should start with a smaller subset:

* `report`
* `submit_artifact`
* `update_status`
* `request_budget`
* `request_permission`
* `get_task_context`
* `get_org_context`

---

## 28. Human-Readable Text Dashboard

The initial dashboard does not need to be fancy.

A text dashboard is enough.

Example:

```text
Workforce Runtime
=================

Company Goal:
  Ship MVP for GitHub issue-to-PR automation.

Current Budget:
  Tokens used today: 142,330 / 500,000
  Runtime used today: 43m / 180m
  Active worker runs: 3

Organization:
  CEO Agent
    VP Engineering Agent
      Backend Manager Agent
        Codex Worker 1         busy      task_104
        Codex Worker 2         idle
      QA Manager Agent
        Claude Code Worker 1   blocked   task_107
    Chief of Staff Agent       active

Active Tasks:
  task_101  Design MVP architecture              completed
  task_102  Implement MCP report tool            in_progress
  task_103  Add Codex worker adapter             in_progress
  task_104  Fix adapter test failure             in_progress
  task_107  Validate Claude Code worker flow      blocked

Recent Reports:
  [10:42] Codex Worker 1 -> Backend Manager
         Fixed failing adapter test. pytest passed.
         Cost: 18,432 tokens, 312s, 9 tool calls.

  [10:45] Claude Code Worker 1 -> QA Manager
         Blocked. Missing MCP server config.
         Requested permission: read runtime config.

Decision Inbox:
  1. Approve additional 30,000 tokens for task_107?
  2. Allow Claude Code Worker 1 to read config/mcp.yaml?
```

The dashboard should show:

* company goal
* org chart
* active agents
* idle agents
* blocked agents
* active tasks
* completed tasks
* failed tasks
* budget usage
* recent reports
* decision inbox
* artifacts
* risk alerts

---

## 29. Agent Interaction Log

The runtime should record agent interactions.

Example:

```text
[10:31] CEO Agent created task_100:
       "Build initial MCP report tool."

[10:32] CEO Agent delegated task_100 to VP Engineering Agent.

[10:34] VP Engineering Agent split task_100 into:
       - task_101: Define report schema
       - task_102: Implement MCP report tool
       - task_103: Add report ledger tests

[10:35] VP Engineering Agent delegated task_102 to Backend Manager Agent.

[10:36] Backend Manager Agent assigned task_102 to Codex Worker 1.

[10:42] Codex Worker 1 submitted artifact:
       artifacts/task_102/diff.patch

[10:43] Codex Worker 1 called report:
       status=completed, confidence=0.82

[10:44] Backend Manager Agent reviewed report and requested test run.

[10:47] Codex Worker 1 submitted test log:
       pytest passed: 12/12
```

This log is not just for debugging. It is organizational memory.

---

## 30. Agent Performance

Each agent should accumulate performance data.

Example metrics:

* tasks assigned
* tasks completed
* tasks failed
* tasks escalated
* average cost per completed task
* average runtime
* report quality score
* artifact acceptance rate
* manager approval rate
* human approval rate
* rework rate
* budget overrun rate
* blocked time
* permission request frequency
* successful delegation rate

Example text output:

```text
Agent Performance
=================

Codex Worker 1
  Role: Software Engineer
  Manager: Backend Manager Agent
  Tasks completed: 8
  Tasks failed: 2
  Avg tokens / task: 21,400
  Avg runtime / task: 6m 10s
  Artifact acceptance rate: 70%
  Rework rate: 25%
  Current status: busy

Claude Code Worker 1
  Role: Senior Software Engineer
  Manager: QA Manager Agent
  Tasks completed: 4
  Tasks failed: 1
  Avg tokens / task: 34,700
  Avg runtime / task: 9m 45s
  Artifact acceptance rate: 80%
  Rework rate: 20%
  Current status: blocked
```

This allows the organization to make decisions:

* Which worker is reliable?
* Which worker is too expensive?
* Which worker should receive harder tasks?
* Which worker needs stricter permissions?
* Which worker should be suspended?
* Which task types should go to which worker?

---

## 31. Hiring New Agents

A manager agent should be able to request a new worker.

Example:

```json
{
  "tool": "request_worker",
  "arguments": {
    "requested_by": "vp_engineering",
    "department": "Engineering",
    "role": "Test Repair Worker",
    "reason": "There are 12 blocked tasks caused by failing test suites. Existing workers are overloaded.",
    "required_capabilities": [
      "read_repo",
      "run_tests",
      "edit_code",
      "submit_artifact",
      "report"
    ],
    "proposed_worker_type": "claude_code",
    "initial_budget": {
      "daily_tokens": 100000,
      "daily_runtime_seconds": 7200
    },
    "probation_tasks": [
      "Fix one failing unit test",
      "Generate a report with evidence"
    ]
  }
}
```

The CEO agent or human user can approve or reject the request.

Hiring should create:

* a new agent profile
* a reporting line
* a budget
* a permission set
* a probation status
* initial tasks

This mirrors human hiring but remains optimized for agents.

---

## 32. Firing or Suspending Agents

Agents should not live forever by default.

An agent can be suspended if:

* it repeatedly exceeds budget
* it produces low-quality artifacts
* it causes unsafe actions
* it fails too many tasks
* it does not report correctly
* it drifts from goals
* it duplicates other agents’ work

Example:

```yaml
agent_status_update:
  agent_id: codex_worker_3
  old_status: active
  new_status: suspended
  reason: "Exceeded budget on 3 consecutive tasks and failed to submit structured reports."
```

This is part of making the system governable.

---

## 33. Human-Agent Hybrid Organization

The long-term system should support real human employees.

Each human can have a corresponding personal agent.

Example:

```text
Human Engineering Manager
└── Manager Agent
    ├── Engineer Alice Agent
    ├── Engineer Bob Agent
    ├── Codex Worker 1
    └── Claude Code Worker 1
```

A personal agent can:

* summarize the human’s work
* draft reports
* prepare updates
* track open tasks
* respond to routine requests
* help with code review
* escalate blockers
* communicate with manager agents
* receive delegated tasks

This creates an AI shadow organization.

The human org remains responsible, but the agent org handles information compression, routine work, and structured execution.

---

## 34. Initial MVP

The MVP should be small and concrete.

### MVP goal

Build a local Workforce Runtime server that can run a small engineering organization of agents.

### MVP organization

```text
Human User
└── CEO Agent
    └── VP Engineering Agent
        └── Engineering Manager Agent
            ├── Codex Worker
            └── Claude Code Worker
```

### MVP capabilities

* define org chart in YAML
* start runtime server
* expose MCP server
* spawn Codex worker
* spawn Claude Code worker
* assign task contract
* receive MCP `report`
* receive MCP `submit_artifact`
* track budget
* track task status
* produce text dashboard
* produce agent interaction log
* produce manager summary

### MVP use case

Input:

```text
Fix the failing test in this sample repository and report back through Workforce Runtime.
```

Runtime behavior:

1. Human creates company-level goal.
2. CEO agent delegates to VP Engineering.
3. VP Engineering creates engineering task.
4. Engineering Manager assigns the task to Codex or Claude Code.
5. Worker edits code in a sandbox.
6. Worker runs tests.
7. Worker submits artifact.
8. Worker calls `report`.
9. Manager reviews report.
10. Runtime updates dashboard.
11. Human sees a readable summary.

---

## 35. Example MVP Output

```text
Workforce Runtime Summary
=========================

Goal:
  Fix the failing test in sample-repo.

Outcome:
  Completed.

Final Artifact:
  artifacts/task_004/diff.patch

Test Result:
  pytest tests/test_parser.py passed.

Agent Chain:
  Human User
    -> CEO Agent
    -> VP Engineering Agent
    -> Engineering Manager Agent
    -> Codex Worker

Cost:
  Total tokens: 37,901
  Runtime: 8m 22s
  Tool calls: 17

Reports:
  Codex Worker:
    Fixed parser edge case for empty input.
    Added regression test.
    Ran target test successfully.
    Confidence: 0.86

  Engineering Manager:
    Reviewed worker report and accepted artifact.
    Risk: Low.
    Recommendation: Ready for human review.

Decision Required:
  None.
```

---

## 36. Suggested Repository Structure

```text
workforce-runtime/
  README.md
  DESIGN.md
  LICENSE

  workforce_runtime/
    __init__.py

    core/
      organization.py
      agent_profile.py
      task.py
      report.py
      budget.py
      permissions.py
      artifact.py
      events.py

    server/
      main.py
      config.py
      runtime.py

    mcp/
      server.py
      tools/
        report.py
        submit_artifact.py
        update_status.py
        request_budget.py
        request_permission.py
        get_task_context.py
        get_org_context.py

    workers/
      base.py
      codex.py
      claude_code.py
      generic_cli.py
      human_mock.py

    scheduler/
      dispatcher.py
      task_queue.py
      budget_manager.py
      permission_manager.py

    dashboard/
      text_dashboard.py
      summaries.py

    storage/
      sqlite_store.py
      file_store.py

    evals/
      task_outcome.py
      report_quality.py
      budget_efficiency.py
      goal_alignment.py

  examples/
    simple_engineering_org/
      org.yaml
      tasks.yaml
      README.md

    sample_repo_task/
      org.yaml
      task.json
      README.md

  tests/
    test_task_contract.py
    test_report_contract.py
    test_budget.py
    test_permissions.py
    test_worker_adapter.py
```

---

## 37. Configuration Example

```yaml
company:
  name: Demo Workforce
  mission: Build and maintain software using AI workers.
  human_owner: Bob

runtime:
  storage: sqlite
  artifact_dir: ./artifacts
  mcp:
    host: 127.0.0.1
    port: 8765

agents:
  - id: ceo
    name: CEO Agent
    role: CEO
    department: Executive
    manager: null
    worker_type: claude_code
    budget:
      daily_tokens: 100000
      daily_runtime_seconds: 7200
    permissions:
      - delegate_task
      - approve_budget
      - hire_agent
      - escalate_to_human

  - id: vp_engineering
    name: VP Engineering Agent
    role: VP Engineering
    department: Engineering
    manager: ceo
    worker_type: claude_code
    budget:
      daily_tokens: 150000
      daily_runtime_seconds: 10800
    permissions:
      - delegate_task
      - request_budget
      - review_report

  - id: eng_manager
    name: Engineering Manager Agent
    role: Engineering Manager
    department: Engineering
    manager: vp_engineering
    worker_type: claude_code
    budget:
      daily_tokens: 120000
      daily_runtime_seconds: 7200
    permissions:
      - assign_worker
      - review_report
      - request_permission

  - id: codex_worker
    name: Codex Worker
    role: Software Engineer
    department: Engineering
    manager: eng_manager
    worker_type: codex
    budget:
      daily_tokens: 50000
      daily_runtime_seconds: 3600
    permissions:
      - read_repo
      - write_branch
      - run_tests
      - submit_artifact
      - report

  - id: claude_worker
    name: Claude Code Worker
    role: Senior Software Engineer
    department: Engineering
    manager: eng_manager
    worker_type: claude_code
    budget:
      daily_tokens: 80000
      daily_runtime_seconds: 5400
    permissions:
      - read_repo
      - write_branch
      - run_tests
      - submit_artifact
      - report

workers:
  codex:
    command: codex
    mode: cli
    profile: workforce-local

  claude_code:
    command: claude
    mode: cli
    profile: default
```

---

## 38. Design Philosophy

### 1. Workers are replaceable.

Codex, Claude Code, Gemini CLI, Cursor, open-source agents, and human employees should all be pluggable.

### 2. The organization is persistent.

Worker processes may come and go, but the organization graph, task ledger, reports, artifacts, memory, and performance history should persist.

### 3. Reports are more important than chat.

The runtime should optimize for structured reporting, not unbounded conversation.

### 4. Budget is a first-class constraint.

Every worker consumes resources. Every task should have a cost.

### 5. Permissions must be explicit.

Agents should receive only the capabilities needed for a task.

### 6. Humans should see decisions, not noise.

The human interface should surface decision points, risks, outcomes, and budget usage.

### 7. Artifacts matter more than claims.

A worker’s claim is weak. A patch, test log, report, metric, or approved artifact is stronger.

### 8. The system should improve through performance history.

The runtime should learn which workers are good at which tasks, which tasks are expensive, which managers delegate well, and which workflows produce reliable artifacts.

---

## 39. Evaluation

Workforce Runtime should be evaluated at the organization level.

Useful metrics:

### Task metrics

* task completion rate
* task failure rate
* task retry rate
* time to completion
* artifact acceptance rate
* human approval rate

### Cost metrics

* tokens per completed task
* runtime per completed task
* tool calls per completed task
* budget overrun rate
* cost by department
* cost by worker type

### Quality metrics

* test pass rate
* report quality
* artifact quality
* manager acceptance rate
* human rework rate
* false completion rate

### Organization metrics

* number of active agents
* number of blocked agents
* manager load
* average reports per manager
* escalation rate
* goal drift rate
* duplicate task rate
* decision latency

### Worker metrics

* completion rate
* cost efficiency
* reliability
* average confidence
* confidence calibration
* rework rate
* permission violation rate
* report compliance rate

---

## 40. Future Directions

### Dynamic organization design

The system can suggest org chart changes based on workload and performance.

Examples:

* create a new QA worker when test tasks pile up
* suspend an unreliable worker
* move a strong worker to a harder task class
* split a department when manager load is too high
* merge redundant teams

### Agent hiring and probation

New agents can be created with limited permissions and probation tasks.

They earn more budget and permissions through performance.

### Human mirror agents

Each human employee can have a corresponding personal agent that handles routine reporting, task tracking, summaries, and coordination.

### Multi-company or multi-project support

A user could operate multiple AI organizations.

Each organization has its own mission, budget, agents, and memory.

### Enterprise integrations

Future integrations:

* GitHub
* GitLab
* Jira
* Slack
* Google Drive
* Google Calendar
* Notion
* Linear
* Datadog
* PagerDuty
* Perforce
* CI systems
* internal tools

### Advanced dashboard

The initial dashboard can be text-only.

A later dashboard can show:

* org chart
* live task graph
* budget burn
* worker performance
* reports
* artifacts
* decision inbox
* permission requests
* hiring requests
* risk alerts

### Organization memory

The runtime can store:

* past tasks
* successful patterns
* failed attempts
* manager decisions
* human feedback
* worker performance
* org changes
* recurring blockers
* cost history

This memory can help future managers and workers perform better.

---

## 41. Long-Term Product Form

The long-term version of Workforce Runtime can become:

* an AI company operating system
* an AI workforce management layer
* a human-agent organization runtime
* a neutral orchestration layer above competing agent providers
* a governance layer for enterprise AI workers
* a personal AI company runtime for solo founders
* an execution layer for AI software factories

The strongest version is provider-neutral.

It should not depend on any single model company.

If Codex improves, Workforce Runtime benefits.

If Claude Code improves, Workforce Runtime benefits.

If an open-source agent becomes strong, Workforce Runtime benefits.

If companies build internal agents, Workforce Runtime can manage them.

The runtime’s value comes from governing the workforce, not from being the worker.

---

## 42. Initial Implementation Roadmap

### Phase 1: Core contracts

* define `AgentProfile`
* define `TaskContract`
* define `ReportContract`
* define `Budget`
* define `Capability`
* define `Artifact`
* define `Event`

### Phase 2: Local runtime server

* load org config
* create org graph
* create task ledger
* create event ledger
* create artifact directory
* create budget manager
* create permission manager

### Phase 3: MCP server

Implement initial MCP tools:

* `report`
* `submit_artifact`
* `update_status`
* `request_budget`
* `request_permission`
* `get_task_context`
* `get_org_context`

### Phase 4: Worker adapters

Implement:

* `GenericCLIWorkerAdapter`
* `CodexWorkerAdapter`
* `ClaudeCodeWorkerAdapter`

### Phase 5: Text dashboard

Implement:

* org chart view
* active task view
* budget view
* recent report view
* decision inbox
* worker performance summary

### Phase 6: Demo workflow

Build one full demo:

* sample repo
* failing test
* CEO task
* delegated manager task
* Codex or Claude Code worker run
* artifact submitted
* report collected
* dashboard updated

### Phase 7: Public release

Prepare:

* README
* design doc
* architecture diagram
* demo video or GIF
* example config
* quickstart
* comparison with existing agent frameworks
* roadmap

---

## 43. One-Sentence Summary

Workforce Runtime turns third-party AI agents into a governed workforce with org charts, roles, budgets, permissions, structured reports, artifacts, and human-readable execution summaries.

---

## 44. Short README Pitch

```text
Workforce Runtime is an organization runtime for AI workers.

It does not build its own coding agent. Instead, it manages existing agents like Codex and Claude Code as employees inside an AI organization.

Define an org chart. Assign roles. Give agents budgets and permissions. Delegate tasks. Receive structured reports through MCP. Track artifacts, costs, decisions, and performance.

The worker agents do the work.

Workforce Runtime manages the company.
```
