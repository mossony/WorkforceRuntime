# Workforce Runtime Implementation Plan

## Global Rule

Build this project incrementally.

Every phase must produce runnable code, tests, and a small demo where applicable.

Do not implement a custom LLM agent loop.

All real worker execution must go through external worker adapters, initially:

* Generic CLI worker
* Codex worker
* Claude Code worker

The core product is the organization runtime: org chart, task contracts, reports, budget, permissions, MCP tools, worker spawning, artifacts, logs, and text dashboard.

---

# Phase 0 — Repository Skeleton and Core Philosophy

## Goal

Create the basic Python project structure and define the core data model philosophy.

## Deliverables

Create repository structure:

```text
workforce-runtime/
  README.md
  DESIGN.md
  pyproject.toml
  workforce_runtime/
    __init__.py
    core/
    server/
    mcp/
    workers/
    scheduler/
    dashboard/
    storage/
    evals/
  examples/
  tests/
```

## Requirements

Use Python 3.11+.

Use Pydantic for structured contracts.

Use SQLite for initial persistence.

Use plain text dashboard first.

Avoid web dashboard in this phase.

Avoid real Codex / Claude integration in this phase.

## Acceptance Criteria

* `python -m workforce_runtime` runs and prints a placeholder message.
* `pytest` runs successfully.
* Project has a valid `pyproject.toml`.
* README explains the product in one paragraph.

---

# Phase 1 — Core Contracts

## Goal

Define the core objects of Workforce Runtime.

## Files

Implement:

```text
workforce_runtime/core/agent_profile.py
workforce_runtime/core/task.py
workforce_runtime/core/report.py
workforce_runtime/core/budget.py
workforce_runtime/core/permissions.py
workforce_runtime/core/artifact.py
workforce_runtime/core/events.py
workforce_runtime/core/organization.py
```

## Core Objects

### AgentProfile

Fields:

* `id`
* `name`
* `role`
* `department`
* `manager_id`
* `worker_type`
* `responsibilities`
* `permissions`
* `budget`
* `status`
* `current_task_ids`
* `performance_summary`

Allowed statuses:

* `idle`
* `busy`
* `blocked`
* `suspended`
* `terminated`

### TaskContract

Fields:

* `task_id`
* `title`
* `objective`
* `assigned_to`
* `assigned_by`
* `parent_task_id`
* `root_goal_id`
* `context_refs`
* `constraints`
* `acceptance_criteria`
* `budget`
* `risk_level`
* `required_artifacts`
* `status`

Allowed statuses:

* `created`
* `assigned`
* `in_progress`
* `blocked`
* `completed`
* `failed`
* `cancelled`

### ReportContract

Fields:

* `report_id`
* `from_agent_id`
* `to_agent_id`
* `task_id`
* `summary`
* `status`
* `work_done`
* `evidence`
* `risks`
* `blockers`
* `confidence`
* `cost`
* `next_action`
* `requires_decision`
* `alignment_check`

### Budget

Fields:

* `max_tokens`
* `max_runtime_seconds`
* `max_tool_calls`
* `tokens_used`
* `runtime_seconds_used`
* `tool_calls_used`

### Capability

Represent permissions as strings first.

Examples:

* `read_repo`
* `write_branch`
* `run_tests`
* `submit_artifact`
* `report`
* `delegate_task`
* `request_budget`
* `request_permission`
* `approve_budget`
* `hire_agent`

### Artifact

Fields:

* `artifact_id`
* `task_id`
* `agent_id`
* `type`
* `path`
* `description`
* `created_at`

### Event

Fields:

* `event_id`
* `timestamp`
* `event_type`
* `actor_id`
* `task_id`
* `payload`

## Tests

Add unit tests for:

* creating valid agent profiles
* creating valid task contracts
* creating valid reports
* rejecting invalid task statuses
* budget usage updates
* organization manager/direct-report relationships

## Acceptance Criteria

* All contracts serialize to JSON.
* All contracts deserialize from JSON.
* Tests pass.
* A sample org can be loaded in memory.

---

# Phase 2 — Organization Graph

## Goal

Implement the organization graph.

## Files

```text
workforce_runtime/core/organization.py
workforce_runtime/storage/file_loader.py
examples/simple_engineering_org/org.yaml
```

## Requirements

Load an org from YAML.

Support:

* find agent by id
* get manager
* get direct reports
* get full reporting chain
* get department agents
* print org chart as text

## Example Org

```yaml
company:
  name: Demo Workforce
  mission: Build software using AI workers.

agents:
  - id: ceo
    name: CEO Agent
    role: CEO
    department: Executive
    manager_id: null
    worker_type: generic_cli
    responsibilities:
      - Own company-level goals
      - Allocate budget
      - Review executive reports
    permissions:
      - delegate_task
      - approve_budget
      - hire_agent
    budget:
      max_tokens: 100000
      max_runtime_seconds: 7200
      max_tool_calls: 100

  - id: vp_engineering
    name: VP Engineering Agent
    role: VP Engineering
    department: Engineering
    manager_id: ceo
    worker_type: generic_cli
    responsibilities:
      - Break engineering goals into projects
      - Manage engineering execution
    permissions:
      - delegate_task
      - request_budget
      - review_report
    budget:
      max_tokens: 150000
      max_runtime_seconds: 10800
      max_tool_calls: 200
```

## Acceptance Criteria

Running:

```bash
python -m workforce_runtime org print examples/simple_engineering_org/org.yaml
```

prints a readable org chart.

---

# Phase 3 — Storage Layer

## Goal

Persist agents, tasks, reports, events, artifacts, and budget usage.

## Files

```text
workforce_runtime/storage/sqlite_store.py
workforce_runtime/storage/file_store.py
```

## Requirements

SQLite store should support:

* save agent
* get agent
* save task
* get task
* list tasks
* save report
* list reports by task
* save event
* list events
* save artifact
* list artifacts by task

File store should support:

* saving artifact files under `artifacts/<task_id>/`
* saving worker stdout/stderr
* saving git diffs
* saving test logs

## Acceptance Criteria

* Storage tests pass.
* A task can be created, updated, reported on, and loaded again.
* Events are append-only.

---

# Phase 4 — Runtime Server Core

## Goal

Implement the local runtime service without MCP yet.

## Files

```text
workforce_runtime/server/runtime.py
workforce_runtime/server/main.py
workforce_runtime/scheduler/dispatcher.py
workforce_runtime/scheduler/budget_manager.py
workforce_runtime/scheduler/permission_manager.py
```

## Requirements

Runtime should:

* load org config
* initialize storage
* create tasks
* assign tasks
* update task status
* check budgets
* check permissions
* record events
* register reports
* register artifacts

## CLI Commands

Implement basic CLI:

```bash
workforce-runtime init --org examples/simple_engineering_org/org.yaml

workforce-runtime task create \
  --title "Fix failing test" \
  --objective "Fix the failing parser test" \
  --assign-to codex_worker

workforce-runtime task list

workforce-runtime task show task_001
```

## Acceptance Criteria

* A user can create a task from CLI.
* Runtime saves task to SQLite.
* Runtime records events.
* Runtime can show task state.

---

# Phase 5 — MCP Server

## Goal

Expose Workforce Runtime as an MCP server.

## Files

```text
workforce_runtime/mcp/server.py
workforce_runtime/mcp/tools/report.py
workforce_runtime/mcp/tools/submit_artifact.py
workforce_runtime/mcp/tools/update_status.py
workforce_runtime/mcp/tools/request_budget.py
workforce_runtime/mcp/tools/request_permission.py
workforce_runtime/mcp/tools/get_task_context.py
workforce_runtime/mcp/tools/get_org_context.py
```

## Initial MCP Tools

Implement:

* `report`
* `submit_artifact`
* `update_status`
* `request_budget`
* `request_permission`
* `get_task_context`
* `get_org_context`

## report Tool

Input:

```json
{
  "from_agent_id": "codex_worker",
  "to_agent_id": "eng_manager",
  "task_id": "task_001",
  "summary": "Fixed the failing parser test.",
  "status": "completed",
  "work_done": [
    "Inspected failing test",
    "Modified parser edge case",
    "Ran pytest"
  ],
  "evidence": [
    {
      "type": "test_log",
      "path": "artifacts/task_001/pytest.log"
    }
  ],
  "risks": [],
  "blockers": [],
  "confidence": 0.86,
  "cost": {
    "tokens_used": 12000,
    "runtime_seconds": 240,
    "tool_calls": 8
  },
  "next_action": "Ready for manager review.",
  "requires_decision": false
}
```

Output:

```json
{
  "ok": true,
  "report_id": "report_001"
}
```

## submit_artifact Tool

Input:

```json
{
  "agent_id": "codex_worker",
  "task_id": "task_001",
  "artifact_type": "git_diff",
  "path": "artifacts/task_001/diff.patch",
  "description": "Patch generated by Codex worker."
}
```

## Acceptance Criteria

* MCP server starts.
* A mock MCP client can call `report`.
* Reports are saved to SQLite.
* Report events appear in event log.

---

# Phase 6 — Generic CLI Worker Adapter

## Goal

Implement the first worker adapter using a generic command-line process.

This allows testing the runtime without Codex or Claude Code.

## Files

```text
workforce_runtime/workers/base.py
workforce_runtime/workers/generic_cli.py
examples/mock_worker/mock_worker.py
```

## WorkerAdapter Interface

```python
class WorkerAdapter:
    def declare_capabilities(self):
        ...

    def start_task(self, task, runtime_context):
        ...

    def collect_artifacts(self, run_id):
        ...

    def stop_task(self, run_id):
        ...

    def get_usage(self, run_id):
        ...
```

## Generic CLI Behavior

The generic CLI worker should:

* receive task contract via JSON file
* receive MCP server info through environment variables
* run a configured command
* capture stdout/stderr
* collect artifacts
* update runtime on completion

## Acceptance Criteria

* Runtime can spawn mock worker.
* Mock worker can call MCP `report`.
* Runtime records report.
* Text dashboard shows task completed.

---

# Phase 7 — Codex Worker Adapter

## Goal

Add Codex as a real worker.

## Files

```text
workforce_runtime/workers/codex.py
examples/codex_worker_task/
```

## Requirements

The Codex adapter should:

* create isolated workspace or git worktree
* generate task prompt from `TaskContract`
* include instructions to use Workforce MCP tools
* spawn Codex CLI
* capture stdout/stderr
* collect git diff
* collect logs
* collect artifacts
* update budget usage when possible

## Codex Prompt Template

```text
You are an AI worker inside Workforce Runtime.

Your agent id is: {agent_id}
Your manager is: {manager_id}
Your assigned task is:

{task_contract}

You must work only within the given workspace.

You must respect all constraints.

When you make progress, use the Workforce Runtime MCP tools.

When you finish, call the report tool with:
- summary
- status
- work_done
- evidence
- risks
- blockers
- confidence
- cost estimate
- next_action
- whether a decision is required

Do not claim completion without submitting artifacts.
```

## Acceptance Criteria

* Codex can be spawned on a sample repo.
* Codex receives a structured task.
* Codex produces a patch or report.
* Runtime captures stdout/stderr.
* Runtime captures git diff.
* Runtime displays result in dashboard.

---

# Phase 8 — Claude Code Worker Adapter

## Goal

Add Claude Code as a real worker.

## Files

```text
workforce_runtime/workers/claude_code.py
examples/claude_worker_task/
```

## Requirements

The Claude Code adapter should follow the same lifecycle as Codex:

* create isolated workspace
* generate task prompt
* attach MCP server configuration if available
* spawn Claude Code process
* capture stdout/stderr
* collect diff/logs/artifacts
* receive MCP reports
* update runtime state

## Acceptance Criteria

* Claude Code can be spawned on a sample repo.
* Claude Code can complete or report on a task.
* Runtime stores artifacts and reports.
* Dashboard shows Claude Code worker status.

---

# Phase 9 — Text Dashboard

## Goal

Create a human-readable text dashboard.

## Files

```text
workforce_runtime/dashboard/text_dashboard.py
workforce_runtime/dashboard/summaries.py
```

## Dashboard Sections

Show:

* company goal
* org chart
* active agents
* idle agents
* blocked agents
* active tasks
* completed tasks
* failed tasks
* recent reports
* recent artifacts
* budget usage
* decision inbox
* worker performance

## Example Command

```bash
workforce-runtime dashboard
```

## Acceptance Criteria

Dashboard prints:

```text
Workforce Runtime
=================

Company:
  Demo Workforce

Budget:
  Tokens used: 42,100 / 500,000
  Runtime: 18m / 180m

Organization:
  CEO Agent
    VP Engineering Agent
      Engineering Manager Agent
        Codex Worker      idle
        Claude Worker     busy task_003

Active Tasks:
  task_003  Fix parser test  in_progress  Claude Worker

Recent Reports:
  [12:31] Codex Worker -> Engineering Manager
         Completed task_002. Patch submitted. Tests passed.

Decision Inbox:
  1. Claude Worker requested write permission for tests/parser/**
```

---

# Phase 10 — Manager Review Flow

## Goal

Implement one level of management review.

## Behavior

A worker submits a report.

The manager agent receives a review task.

The manager decides:

* accept
* reject
* request retry
* escalate
* request human review

The first version can implement manager review as a simple rule-based or LLM-worker task.

## Requirements

Manager review should inspect:

* worker report
* submitted artifacts
* task acceptance criteria
* budget usage
* risks
* blockers

## Acceptance Criteria

* Worker completes task.
* Manager review task is automatically created.
* Manager accepts or rejects worker output.
* Final task status updates accordingly.

---

# Phase 11 — Budget Enforcement

## Goal

Make budget meaningful.

## Requirements

Budget manager should enforce:

* max runtime
* max tool calls if tracked
* max tokens if available
* max retry count

For worker processes:

* kill process after runtime limit
* mark task as failed or timed out
* record budget event

## Acceptance Criteria

* A worker exceeding runtime budget is stopped.
* Task status becomes failed or timed_out.
* Event log records budget violation.
* Dashboard shows budget overrun.

---

# Phase 12 — Permission Enforcement

## Goal

Make permissions meaningful.

## Initial Scope

For MVP, enforce permissions at runtime level:

* whether agent can call `delegate_task`
* whether agent can call `request_budget`
* whether agent can call `submit_artifact`
* whether agent can call `report`
* whether agent can request permission

Full filesystem sandboxing can come later.

## Acceptance Criteria

* Agent without `delegate_task` cannot delegate.
* Agent without `submit_artifact` cannot submit artifact.
* Permission violation creates event.
* Dashboard shows permission violation.

---

# Phase 13 — End-to-End Demo

## Goal

Build one polished demo.

## Demo Scenario

A sample repo has a failing test.

The human user creates a company goal:

```text
Fix the failing parser test and produce a manager-reviewed report.
```

Expected flow:

1. Human creates task.
2. CEO agent delegates to VP Engineering.
3. VP delegates to Engineering Manager.
4. Manager assigns Codex or Claude Code worker.
5. Worker attempts fix.
6. Worker submits artifact and report.
7. Manager reviews.
8. Dashboard shows final status.

## Acceptance Criteria

One command runs the demo:

```bash
workforce-runtime demo sample-repo-fix
```

At the end, the user sees:

* org chart
* task chain
* worker report
* manager review
* diff path
* test log path
* total cost
* final status

---

# Phase 14 — Public Alpha Readiness

## Goal

Prepare project for open-source alpha.

## Required Docs

* README.md
* DESIGN.md
* QUICKSTART.md
* MCP_TOOLS.md
* WORKER_ADAPTERS.md
* EXAMPLES.md
* ROADMAP.md

## README Must Explain

* what Workforce Runtime is
* what it is not
* how it differs from ordinary agent frameworks
* how to run the demo
* how to define an org chart
* how to add a worker adapter
* how MCP reporting works

## Acceptance Criteria

A new user can:

1. clone repo
2. install package
3. run mock worker demo
4. inspect dashboard
5. understand how Codex / Claude Code fit into the architecture

---

# Implementation Priority

The strict order should be:

1. Core contracts
2. Org graph
3. Storage
4. Runtime server
5. MCP server
6. Generic CLI worker
7. Text dashboard
8. Codex adapter
9. Claude Code adapter
10. Manager review
11. Budget enforcement
12. Permission enforcement
13. End-to-end demo
14. Public alpha docs

Do not implement advanced UI before the core runtime works.

Do not implement dynamic hiring before basic task delegation works.

Do not implement hundreds of agents before 5 agents work reliably.

Do not implement custom agent reasoning loops.

Do not optimize for scale until the protocol works.

---

# First Coding Agent Task

Implement Phase 1 only.

Create the Pydantic models for:

* AgentProfile
* TaskContract
* ReportContract
* Budget
* Artifact
* Event
* Organization

Also create unit tests for serialization, deserialization, status validation, budget updates, and org reporting relationships.

Do not implement MCP.

Do not implement worker adapters.

Do not implement dashboard.

Focus only on correct, typed, tested core contracts.
