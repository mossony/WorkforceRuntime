# MCP Tools

Workforce Runtime exposes worker-facing tools over line-delimited JSON-RPC on stdio:

```bash
workforce-runtime --db .workforce_runtime/runtime.sqlite mcp serve
```

The server supports `initialize`, `tools/list`, and `tools/call`. Tool responses include both text content and `structuredContent`.

## Tools

### `report`

Submits the final structured report for a task. The report is delivered to the sender's direct manager. `to_agent_id` is optional for compatibility; when present it must match the direct manager.

Required fields:

- `from_agent_id`
- `task_id`
- `summary`
- `status`
- `confidence`

Common optional fields:

- `work_done`
- `evidence`
- `risks`
- `blockers`
- `cost`
- `next_action`
- `requires_decision`
- `alignment_check`

Accepted reports trigger manager review in the runtime.

### `report_to_human`

Submits a CEO-level update that should be shown clearly to the human operator. This is not a manager report and does not trigger manager review. By default only the root CEO/top-level agent with `report_to_human` permission can call it.

Required fields:

- `from_agent_id`
- `message`

Common optional fields:

- `task_id`
- `title`
- `status`
- `confidence`
- `next_action`
- `requires_decision`

The web dashboard highlights these as Human Reports near the task execution view.

### `assign`

Creates a task for an agent under the caller's reporting line, or assigns an existing task when `task_id` is provided.

Expected arguments:

- `from_agent_id`
- `to_agent_id`
- `message`
- optional `title`
- optional `task_id` or `id`
- optional `acceptance_criteria`
- optional `required_artifacts`

The caller must have `delegate_task` and must manage the assignee directly or indirectly.

### `discuss`

Sends a message to another agent and records it as a runtime event. This is for peer or cross-functional communication that is not a formal task or report.

Expected arguments:

- `from_agent_id`
- `to_agent_id`
- `message`
- optional `task_id`
- optional `thread_id` or `id`

### `check_progress`

Lets a manager inspect and record the current progress of a subordinate. The tool returns the target agent, active tasks, recent reports, and recent events. The caller must manage the target agent directly or indirectly.

Expected arguments:

- `from_agent_id`
- `target_agent_id`
- optional `task_id`
- optional `message`

### `hire`

Creates a new worker or manager when the caller has `hire_agent` and company headcount/token budget allow the hire.

Expected arguments:

- `from_agent_id`
- `id` or `new_agent_id`
- `name`
- `role`
- `department`
- `manager_id`
- `worker_type`
- optional `responsibilities`
- optional `permissions`
- optional `budget`
- optional `system_prompt`

If `system_prompt` is omitted, Workforce Runtime generates one from the company mission, role, reporting line, responsibilities, and permissions.

### `update_system_prompt`

Updates the system prompt of an agent under the caller's reporting line.

Expected arguments:

- `from_agent_id`
- `target_agent_id` or `id`
- `system_prompt` or `message`

### `submit_artifact`

Registers a file produced by a worker, such as a git diff, test log, final message, or generated document.

Expected arguments:

- `agent_id`
- `task_id`
- `artifact_type`
- `path`
- `description`

### `update_status`

Updates task state while work is running or blocked.

Expected arguments:

- `agent_id`
- `task_id`
- `status`

### `request_budget`

Records a request for additional task budget. Managers can inspect this through events and dashboard output.

Expected arguments:

- `agent_id`
- `task_id`
- `reason`
- `requested_budget`

### `request_permission`

Records a request for an additional capability or permission.

Expected arguments:

- `agent_id`
- `task_id`
- `permission`
- `reason`

### `get_task_context`

Returns the current task contract and related runtime context, including reports, artifacts, task documents, and actor model capabilities.

Expected arguments:

- `task_id`

### `get_task_dossier`

Returns the broader task dossier: task contract, requirements, division of work from child tasks, task documents, reports, artifacts, and recent events. Agents should use this before asking for a large prompt dump.

Expected arguments:

- `agent_id` or `actor_id`
- `task_id`
- optional `include_events`
- optional `event_limit`

### `upsert_task_doc`

Creates or updates a task-attached document such as requirements, division of work, context notes, decisions, risks, or tool requests.

Expected arguments:

- `agent_id` or `actor_id`
- `task_id`
- `title`
- `content`
- optional `doc_id`
- optional `doc_type`

### `request_tool`

Records a request for a new runtime tool when an agent repeatedly finds a missing capability and the workaround is manual or error-prone. The request is auditable and can be approved by the configured approval level.

Expected arguments:

- `from_agent_id`
- `tool_name`
- `problem`
- `proposed_capability`
- optional `task_id`
- optional `frequency`
- optional `current_workaround`
- optional `requested_approval_level`: `human_ceo`, `vp`, or `manager`

### `decide_tool_request`

Approves or rejects a tool request. Human can approve any level; CEO can approve `human_ceo`; VP/CEO can approve `vp`; a direct manager can approve `manager`.

Expected arguments:

- `from_agent_id`
- `request_id`
- `decision`: `approved` or `rejected`
- optional `approval_level`
- optional `notes`

### `get_org_context`

Returns organization context useful for managers and workers.

Expected arguments:

- `agent_id`

## Minimal JSON-RPC Call

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"update_status","arguments":{"agent_id":"codex_worker","task_id":"task_001","status":"in_progress"}}}
```

Workers should call `submit_artifact` for durable evidence and `report` before claiming completion. Managers should use `assign` for task delegation, `check_progress` for subordinate status checks, and `discuss` for non-task messages.
