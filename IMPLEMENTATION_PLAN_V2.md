# Workforce Runtime V2 Implementation Plan

## 1. Purpose

This implementation plan assumes the following capabilities already exist or are substantially implemented:

- organization and agent profiles;
- task contracts;
- report contracts;
- artifact storage;
- event storage;
- budget tracking;
- permission tracking;
- MCP communication;
- worker spawning;
- Codex and/or Claude Code adapters;
- basic runtime service;
- basic dashboard or CLI;
- task execution lifecycle.

V2 focuses on transforming the existing runtime into an organizational control plane capable of observing, evaluating, simulating, and changing a real human-AI organization.

---

## 2. Implementation Principles

### 2.1 Preserve Existing Runtime Capabilities

Do not rewrite worker execution, MCP, task storage, or existing adapters unless required for compatibility.

Add migration layers around current models.

### 2.2 Use Structured Contracts

All Governor outputs must be schema-validated.

The Governor must never mutate the database directly.

### 2.3 Separate Facts, Analysis, and Decisions

Maintain separate stores for:

- observed facts and events;
- calculated metrics;
- analytical findings;
- proposed changes;
- approved decisions;
- applied changes;
- measured outcomes.

### 2.4 Start Deterministic

Initial analytics and simulation should use deterministic algorithms and rules.

LLMs should interpret evidence and generate candidate proposals.

LLMs should not be the source of numerical metrics.

### 2.5 Build One Complete Vertical Loop

The first major milestone should demonstrate:

```text
observe
→ find problem
→ propose change
→ simulate
→ approve
→ apply
→ evaluate
```

Avoid implementing many disconnected organizational concepts before this loop works.

---

## 3. Target Package Structure

```text
workforce_runtime/
├── core/
│   ├── models/
│   ├── services/
│   └── events/
│
├── organization/
│   ├── models/
│   │   ├── organization.py
│   │   ├── department.py
│   │   ├── position.py
│   │   ├── occupant.py
│   │   ├── occupancy.py
│   │   ├── authority.py
│   │   └── snapshot.py
│   ├── services/
│   │   ├── organization_service.py
│   │   ├── occupancy_service.py
│   │   ├── authority_service.py
│   │   └── snapshot_service.py
│   └── validators/
│       └── invariants.py
│
├── portfolio/
│   ├── models/
│   │   ├── goal.py
│   │   ├── project.py
│   │   └── allocation.py
│   └── services/
│
├── decisions/
│   ├── models/
│   │   ├── decision.py
│   │   ├── option.py
│   │   ├── assumption.py
│   │   └── outcome.py
│   └── services/
│
├── observation/
│   ├── models/
│   │   ├── normalized_event.py
│   │   └── work_edge.py
│   ├── connectors/
│   │   └── github/
│   ├── normalizers/
│   └── services/
│       └── work_graph_builder.py
│
├── analytics/
│   ├── metrics/
│   │   ├── work_metrics.py
│   │   ├── cost_metrics.py
│   │   ├── structural_metrics.py
│   │   ├── goal_metrics.py
│   │   └── decision_metrics.py
│   ├── findings/
│   │   ├── finding.py
│   │   ├── bottleneck_detector.py
│   │   ├── utilization_detector.py
│   │   ├── drift_detector.py
│   │   └── distortion_detector.py
│   └── services/
│       └── analytics_service.py
│
├── governance/
│   ├── models/
│   │   ├── change_proposal.py
│   │   ├── change_set.py
│   │   ├── approval.py
│   │   └── governance_policy.py
│   ├── governor/
│   │   ├── protocol.py
│   │   ├── rule_based.py
│   │   └── llm_governor.py
│   ├── validators/
│   │   ├── schema_validator.py
│   │   ├── invariant_validator.py
│   │   └── policy_validator.py
│   └── services/
│       ├── proposal_service.py
│       ├── approval_service.py
│       └── change_service.py
│
├── simulation/
│   ├── models/
│   │   ├── scenario.py
│   │   ├── simulation_result.py
│   │   └── experiment.py
│   ├── engines/
│   │   ├── historical_replay.py
│   │   └── discrete_event.py
│   └── services/
│       └── simulation_service.py
│
├── evaluation/
│   ├── models/
│   │   └── outcome_evaluation.py
│   └── services/
│       └── experiment_evaluator.py
│
├── backends/
│   ├── protocol.py
│   ├── codex/
│   ├── claude/
│   ├── human/
│   └── generic/
│
├── api/
│   ├── routes/
│   └── schemas/
│
├── cli/
└── tests/
```

---

## 4. Workstream A: Upgrade the Organization Model

## Objective

Introduce strict separation between positions, occupants, worker definitions, and worker runs.

## Required Models

### Position

Fields:

- `id`
- `organization_id`
- `department_id`
- `title`
- `description`
- `reports_to_position_id`
- `responsibilities`
- `required_capabilities`
- `authority_policy_ids`
- `budget_account_id`
- `status`
- `created_at`
- `updated_at`

### Occupant

Fields:

- `id`
- `occupant_type`
- `display_name`
- `worker_definition_id`
- `human_identity_id`
- `capabilities`
- `status`
- `metadata`

### Occupancy

Fields:

- `id`
- `position_id`
- `occupant_id`
- `occupancy_type`
- `effective_from`
- `effective_to`
- `status`
- `handoff_artifact_id`

### WorkerRun

Ensure existing worker executions reference:

- `occupant_id`
- `position_id`
- `assignment_id`
- `task_id`
- `project_id`

## Migration Requirements

Existing `AgentProfile` data should be migrated as follows:

- stable role information becomes `Position`;
- model or worker configuration becomes `Occupant` or `WorkerDefinition`;
- active role assignment becomes `Occupancy`;
- individual execution becomes `WorkerRun`.

## Required Invariants

- one active primary occupancy per position;
- one occupant may fill multiple positions only when policy permits;
- reporting relationships cannot reference archived positions;
- authority inheritance must not create invalid cycles;
- position deletion must be implemented as archival;
- occupancy history must remain immutable.

## Acceptance Criteria

- an existing agent can be migrated into a Position + Occupant + Occupancy;
- the occupant can be replaced without losing position history;
- tasks continue to reference the position after replacement;
- occupancy changes are visible in the audit log;
- all invariants have unit tests.

---

## 5. Workstream B: Organization Snapshot

## Objective

Create immutable snapshots of organization state for analysis, simulation, and rollback.

## Snapshot Contents

A snapshot should include:

- organization;
- departments;
- positions;
- occupancies;
- authority relationships;
- active goals;
- active projects;
- budget allocations;
- governance policies;
- active assignments;
- current metrics summary;
- snapshot timestamp;
- source event cursor.

## API

```python
snapshot = snapshot_service.create_snapshot(
    organization_id=organization_id,
    reason="pre_change_simulation",
)
```

## Requirements

- snapshots must be immutable;
- snapshots must be reproducible from event history where possible;
- change proposals must reference a baseline snapshot;
- simulations must reference a baseline snapshot;
- applied changes must produce a post-change snapshot;
- rollback must reference a known snapshot or inverse change set.

## Acceptance Criteria

- create and load snapshots;
- compare two snapshots;
- generate a structural diff;
- reconstruct active authority and occupancy graphs;
- serialize snapshots for simulation.

---

## 6. Workstream C: Decision Ledger

## Objective

Implement decisions as durable, evaluable organizational objects.

## Required Models

- `Decision`
- `DecisionOption`
- `DecisionEvidence`
- `DecisionDissent`
- `DecisionAssumption`
- `ExpectedOutcome`
- `ObservedOutcome`
- `DecisionEvaluation`

## Decision Lifecycle

```text
draft
→ collecting_evidence
→ awaiting_decision
→ decided
→ active
→ evaluating
→ validated / invalidated / superseded
```

## Required Capabilities

- create decision;
- attach evidence;
- register participants;
- register dissent;
- select option;
- record rationale;
- define assumptions;
- define expected outcomes;
- define revisit conditions;
- attach actual outcomes;
- evaluate forecast accuracy;
- mark decision as superseded.

## Integration

Change proposals requiring approval should create or reference a Decision.

Project-level strategic changes should always create a Decision.

## Acceptance Criteria

- decisions can be queried by project, position, goal, and time;
- expected and actual outcomes can be compared;
- assumptions can be marked true, false, or unknown;
- decision reversal rate can be calculated;
- decision evidence is traceable.

---

## 7. Workstream D: Normalized Observation Events

## Objective

Create a backend-neutral event format for organizational observation.

## Event Types

Initial event types should include:

- task_created;
- task_assigned;
- task_started;
- task_blocked;
- task_completed;
- task_failed;
- review_requested;
- review_completed;
- approval_requested;
- approval_granted;
- approval_rejected;
- artifact_created;
- report_submitted;
- message_sent;
- escalation_created;
- escalation_resolved;
- decision_created;
- decision_made;
- budget_requested;
- budget_allocated;
- worker_run_started;
- worker_run_completed;
- human_intervention;
- position_reassigned;
- organization_change_applied.

## Event Contract

```yaml
event:
  id: event_1004
  organization_id: org_1
  project_id: project_7
  actor_position_id: runtime_engineer
  actor_occupant_id: codex_worker_7
  target_position_id: security_reviewer
  event_type: review_requested
  object_type: artifact
  object_id: artifact_82
  occurred_at: 2026-06-20T14:10:00Z
  source: github
  source_event_id: github_review_992
  metadata: {}
```

## Requirements

- events must be idempotent;
- external source IDs must be stored;
- timestamps must distinguish observed time and occurred time;
- raw source payload may be retained separately;
- actor and target resolution may initially be incomplete;
- unresolved identities should be backfillable.

## Acceptance Criteria

- existing runtime events can be normalized;
- one external connector can emit normalized events;
- duplicate ingestion does not duplicate events;
- events can be replayed in timestamp order.

---

## 8. Workstream E: GitHub Shadow Connector

## Objective

Use one GitHub repository as the first real-world observation source.

## Data to Ingest

- issues;
- issue comments;
- pull requests;
- review requests;
- review comments;
- approvals;
- merge events;
- commits;
- assignees;
- labels;
- CI results;
- releases;
- timestamps.

## Identity Mapping

Provide mappings from GitHub users to:

- occupants;
- positions;
- teams;
- unknown external actors.

## Shadow Restrictions

The connector must initially be read-only.

It must not:

- comment;
- assign;
- merge;
- close;
- modify labels;
- create branches.

## Acceptance Criteria

- repository history can be imported for a configurable time window;
- polling or webhook ingestion works;
- imported activity produces normalized events;
- GitHub identities can be mapped to positions;
- the connector can resume from a stored cursor.

---

## 9. Workstream F: Work Graph Builder

## Objective

Build an empirical graph of how work actually flows.

## Node Types

- position;
- occupant;
- project;
- artifact;
- decision;
- external actor.

## Edge Types

Initial edge types:

- assigned_to;
- requested_review_from;
- reviewed_by;
- approved_by;
- blocked_by;
- depends_on;
- escalated_to;
- informed;
- produced;
- consumed;
- reassigned_to;
- participated_in_decision.

## Aggregations

For each edge calculate:

- count;
- first observed;
- last observed;
- median latency;
- mean latency;
- failure rate;
- rework rate;
- project distribution;
- task category distribution.

## Acceptance Criteria

- graph can be rebuilt from events;
- graph supports configurable observation windows;
- graph can be filtered by project and event type;
- graph metrics are persisted;
- authority graph and work graph can be compared.

---

## 10. Workstream G: Organizational Metrics

## Objective

Implement a minimal reliable metrics engine.

## Initial Metric Set

### Throughput

- completed tasks per period;
- accepted artifacts per period;
- merged PRs per period.

### Latency

- task cycle time;
- review wait time;
- approval wait time;
- blocked duration.

### Quality

- rework rate;
- rejection rate;
- failed CI after completion;
- reopened task rate.

### Cost

- model usage;
- worker runtime;
- human interventions;
- cost per accepted artifact.

### Structure

- span of control;
- work centrality;
- approval centrality;
- dependency centrality;
- authority/work mismatch.

### Governance

- decision latency;
- evidence count;
- dissent count;
- reversal rate;
- assumption accuracy.

## Metric Contract

```yaml
metric:
  name: median_review_latency
  organization_id: org_1
  project_id: project_7
  position_id: security_reviewer
  window_start: ...
  window_end: ...
  value: 7200
  unit: seconds
  sample_size: 18
  confidence: 0.88
```

## Acceptance Criteria

- metrics are reproducible from events;
- metric definitions are versioned;
- sample size is always stored;
- missing data is explicit;
- metrics can be calculated for baseline and post-change windows.

---

## 11. Workstream H: Finding Detectors

## Objective

Generate evidence-backed organizational findings.

## Initial Detectors

### Approval Bottleneck Detector

Signals:

- high queue time;
- high share of waiting tasks;
- concentrated approval ownership;
- low rejection rate despite long wait.

### Overloaded Position Detector

Signals:

- high active task count;
- high incoming edge count;
- growing queue;
- high escalation count.

### Underused Position Detector

Signals:

- low work volume;
- low unique output;
- high cost relative to accepted outcomes.

### Hidden Dependency Detector

Signals:

- high work-graph centrality;
- low formal authority;
- many tasks blocked on the position.

### Authority/Work Mismatch Detector

Signals:

- work routinely bypasses formal managers;
- actual approvers differ from configured approvers;
- responsibilities and observed actions diverge.

### Duplicate Work Detector

Signals:

- similar tasks;
- overlapping artifacts;
- repeated research;
- independent workers producing equivalent output.

## Finding Requirements

Each finding must contain:

- type;
- severity;
- confidence;
- affected entities;
- supporting metrics;
- supporting events;
- detection version;
- suggested investigation;
- status.

## Acceptance Criteria

- each detector has synthetic tests;
- findings reference evidence;
- findings can be dismissed with a reason;
- dismissed findings are retained;
- repeated findings are deduplicated.

---

## 12. Workstream I: Governance Change Model

## Objective

Define structured, machine-validatable organization changes.

## Initial Atomic Changes

- `create_position`
- `archive_position`
- `update_position`
- `assign_occupant`
- `remove_occupant`
- `change_reporting_line`
- `move_position`
- `update_responsibilities`
- `grant_authority`
- `revoke_authority`
- `allocate_budget`
- `pause_project`
- `resume_project`
- `update_approval_policy`
- `create_temporary_team`

## Change Proposal Requirements

Each proposal must contain:

- baseline snapshot ID;
- finding IDs;
- proposed atomic changes;
- rationale;
- expected effects;
- risks;
- assumptions;
- simulation requirements;
- approval requirements;
- evaluation window;
- rollback conditions.

## Validation Layers

### Schema Validation

All required fields and types are present.

### Invariant Validation

The resulting organization remains structurally valid.

### Policy Validation

The proposer and approver have sufficient authority.

### Operational Validation

Active tasks and responsibilities have valid handoff plans.

## Acceptance Criteria

- invalid cycles are rejected;
- orphaned responsibilities are rejected;
- unauthorized changes are rejected;
- proposed post-change state can be previewed;
- proposal validation returns structured errors.

---

## 13. Workstream J: Rule-Based Governor

## Objective

Implement a deterministic Governor before adding LLM reasoning.

## Initial Rules

Examples:

```text
If:
  one approver handles more than 40 percent of approval requests
  and median approval latency exceeds threshold
  and rejection rate is below 5 percent

Then propose:
  risk-based automatic approval for low-risk tasks
  or an additional approval position
```

```text
If:
  a position has high work centrality
  and no backup occupant
  and more than 30 percent of project tasks depend on it

Then propose:
  create backup position or secondary occupancy
```

## Output

Rules must emit standard `OrganizationChangeProposal` objects.

## Acceptance Criteria

- at least three rules produce valid proposals;
- all proposals are evidence-backed;
- no rule directly applies changes;
- rules are configurable;
- rules can be enabled or disabled.

---

## 14. Workstream K: LLM Governor

## Objective

Add an LLM-based reasoning layer that consumes structured data and produces structured proposals.

## Input Context

The LLM Governor should receive:

- organization summary;
- relevant snapshot subset;
- findings;
- metrics;
- current policies;
- relevant decisions;
- project goals;
- resource constraints.

Do not send the full event history by default.

## Output Schema

The LLM must return:

- assessment;
- root-cause hypotheses;
- missing evidence requests;
- candidate proposals;
- expected effects;
- risks;
- assumptions;
- suggested evaluation metrics.

## Validation

Every LLM proposal passes through the same validators as rule-based proposals.

## Requirements

- invalid output must be rejected;
- hallucinated IDs must be rejected;
- unsupported claims must be marked;
- evidence references must resolve;
- numerical predictions must be clearly labeled as estimates;
- the Governor must be replaceable through a protocol.

## Acceptance Criteria

- LLM Governor produces at least two alternative proposals;
- proposals reference real findings and metrics;
- proposals survive schema and ID validation;
- failures are observable and retryable;
- tests use a mock Governor response.

---

## 15. Workstream L: Historical Replay Simulator

## Objective

Implement the first useful counterfactual simulation engine.

## Initial Supported Scenarios

- remove an approval step;
- add a second reviewer;
- change task routing;
- change position capacity;
- replace one occupant’s historical performance profile;
- reduce or increase concurrency;
- change escalation timeout.

## Approach

Replay historical task events using:

- observed task arrival times;
- observed service times;
- observed failure probabilities;
- observed retry probabilities;
- simulated routing rules;
- simulated capacity;
- simulated approval policies.

## Constraints

The initial simulator does not need to simulate semantic reasoning quality.

It should focus on:

- queueing;
- latency;
- capacity;
- routing;
- failure;
- retry;
- cost.

## Acceptance Criteria

- baseline replay approximately reproduces observed latency;
- scenario replay produces comparable metrics;
- assumptions are explicit;
- low-sample scenarios produce warnings;
- results include uncertainty or sensitivity ranges.

---

## 16. Workstream M: Change Approval and Application

## Objective

Allow validated changes to move through an approval workflow and safely modify organization state.

## Approval Policy Examples

- low-risk assignment change: manager approval;
- reporting-line change: department leader approval;
- budget increase above threshold: finance and executive approval;
- position archival: responsible executive approval;
- high-risk policy change: human approval required.

## Application Requirements

- apply changes transactionally;
- create pre-change snapshot;
- create post-change snapshot;
- write audit events;
- generate handoff tasks where required;
- preserve rollback information;
- prevent partial application.

## Acceptance Criteria

- approved changes can be applied;
- rejected changes remain auditable;
- failed change application rolls back;
- applied changes are visible in organization state;
- change application produces events.

---

## 17. Workstream N: Organizational Experiment Runner

## Objective

Track whether a real organization change produces the expected benefit.

## Experiment Lifecycle

```text
planned
→ baseline_collection
→ active
→ evaluating
→ retained / rolled_back / inconclusive
```

## Required Fields

- hypothesis;
- baseline snapshot;
- applied change set;
- baseline time window;
- treatment time window;
- target metrics;
- guardrail metrics;
- rollback thresholds;
- expected effect;
- observed effect;
- conclusion.

## Evaluation

Compare:

- baseline metrics;
- post-change metrics;
- confounding events;
- sample size;
- expected effect;
- observed effect.

Initial evaluation may use simple statistical summaries.

## Acceptance Criteria

- an applied change can create an experiment;
- metrics are collected automatically;
- rollback conditions are evaluated;
- experiment conclusion is stored;
- findings and decisions can reference experiment results.

---

## 18. Workstream O: Information Distortion Analysis

## Objective

Measure how information changes across reports.

## Prerequisites

Reports should support structured claims and evidence references.

## Initial Analysis

Compare source and derived reports for:

- retained blockers;
- retained risks;
- retained numerical facts;
- retained dissent;
- confidence changes;
- recommendation changes.

Use a hybrid approach:

- deterministic comparison for structured fields;
- LLM semantic comparison for free-text claims.

## Acceptance Criteria

- one manager report can be linked to lower-level reports;
- distortion metrics can be calculated;
- missing provenance is flagged;
- unsupported confidence increases are flagged;
- results are visible in report details.

---

## 19. Workstream P: Shadow Dashboard

## Objective

Expose the organizational control loop.

## Required Views

### Organization

- departments;
- positions;
- occupants;
- vacancies;
- reporting lines.

### Authority vs Work

- formal authority graph;
- observed work graph;
- mismatch indicators.

### Findings

- severity;
- confidence;
- evidence;
- affected positions;
- status.

### Proposals

- current state;
- proposed state;
- rationale;
- risks;
- simulation result;
- approval state.

### Decisions

- question;
- options;
- evidence;
- dissent;
- selected option;
- expected outcomes;
- actual outcomes.

### Experiments

- baseline;
- treatment;
- target metrics;
- observed result;
- retain or rollback conclusion.

## Acceptance Criteria

A user can follow one full chain:

```text
event
→ metric
→ finding
→ proposal
→ simulation
→ decision
→ applied change
→ experiment result
```

---

## 20. First End-to-End Milestone

## Scenario

Connect Workforce Runtime to one real GitHub repository in read-only shadow mode.

## Required Flow

1. Import 30 to 90 days of repository activity.
2. Map repository participants to positions.
3. Build the work graph.
4. Calculate review and task latency.
5. Detect one measurable bottleneck.
6. Generate at least two change proposals.
7. Simulate both proposals.
8. Create a structured decision.
9. Select one proposal.
10. Apply the proposal to a sandbox organization configuration.
11. Run a controlled workload using Codex or Claude workers.
12. Compare results with the baseline.
13. store the experiment conclusion.

## Example Intervention

Create a dedicated CI Triage position and assign a Codex worker to it.

Compare:

- time from CI failure to diagnosis;
- human interventions;
- repeated failed runs;
- cost;
- accepted fixes.

---

## 21. Testing Strategy

## Unit Tests

Required for:

- model validation;
- graph invariants;
- occupancy rules;
- change validation;
- metric calculations;
- finding detectors;
- simulator components;
- decision evaluation.

## Integration Tests

Required for:

- event ingestion to work graph;
- work graph to findings;
- findings to proposals;
- proposal validation;
- simulation;
- approval;
- change application;
- experiment evaluation.

## Golden Tests

Maintain fixed event datasets with expected:

- work graph;
- metrics;
- findings;
- simulation outputs.

## Property Tests

Useful properties:

- event replay is deterministic;
- snapshots are immutable;
- applying an inverse change restores prior state;
- invalid reporting cycles are always rejected;
- duplicate events do not change metrics;
- cost allocations remain balanced.

---

## 22. Suggested Delivery Order

The recommended order is:

1. Position, Occupant, and Occupancy separation
2. Organization snapshots
3. Decision ledger
4. Normalized observation events
5. GitHub shadow connector
6. Work graph builder
7. Core organizational metrics
8. Initial finding detectors
9. Structured change proposal model
10. Proposal validators
11. Rule-based Governor
12. Historical replay simulator
13. Approval and change application
14. Experiment runner
15. LLM Governor
16. Information distortion analysis
17. Shadow dashboard
18. Real end-to-end experiment

This order prioritizes one complete governance loop over broad feature coverage.

---

## 23. Explicit Non-Goals for the First V2 Release

Do not implement yet:

- arbitrary recursive organization generation;
- fully autonomous hiring and firing;
- automated financial transactions;
- autonomous production deployment;
- physical-world control;
- reinforcement learning for organization design;
- large-scale distributed simulation;
- marketplace of organization templates;
- cross-company benchmarking;
- full Slack and email ingestion;
- complete human performance management;
- automatic replacement of real employees;
- autonomous high-risk governance.

---

## 24. Definition of Done

V2 is complete when Workforce Runtime can demonstrate:

1. a persistent organization with positions independent from occupants;
2. a real-world event source;
3. an observed work graph;
4. evidence-backed organizational findings;
5. structured organizational change proposals;
6. counterfactual simulation;
7. approval and controlled change application;
8. post-change outcome evaluation;
9. a full audit trail;
10. a measurable comparison between a fixed organization and a Governor-managed organization.

The final demonstration should answer:

> Did the organizational change improve the real outcome, and what evidence supports that conclusion?