# Workforce Runtime V2 Design Document

## 1. Overview

Workforce Runtime is an organizational control plane for long-running human-AI workforces.

It maintains a persistent organization composed of positions, human members, AI workers, projects, budgets, authority relationships, decisions, reports, artifacts, and execution backends.

Its primary purpose is to help an organization solve real, long-running, strongly coupled problems by continuously:

1. observing how work is actually performed;
2. evaluating organizational health;
3. identifying structural problems;
4. proposing organizational changes;
5. simulating alternative structures;
6. applying approved changes;
7. measuring the real effects of those changes.

Workforce Runtime does not need to implement its own general-purpose agent loop.

Existing execution systems such as Codex, Claude Code, Hive, AgentsMesh, other agent frameworks, and human workers can serve as execution backends.

The core product is the organizational layer above those executors.

---

## 2. Product Thesis

Current agent systems mainly optimize task execution.

They focus on:

- prompt execution;
- tool use;
- task delegation;
- workflow graphs;
- retries;
- memory;
- agent communication;
- isolated workspaces;
- model routing;
- observability.

These capabilities are necessary, but they do not answer the higher-level question:

> How should a long-running human-AI organization be structured, governed, evaluated, and changed so that it produces better real-world outcomes?

Workforce Runtime addresses this question.

Its optimization target is not the behavior of one agent session.

Its optimization target is the organization as a whole.

---

## 3. Product Objective

Given:

- one or more long-running goals;
- a set of humans and AI workers;
- multiple execution backends;
- limited budgets;
- incomplete information;
- changing external conditions;
- operational and security constraints;

Workforce Runtime should continuously improve the organization’s ability to achieve real outcomes.

Examples of outcomes include:

- shipping a reliable software product;
- maintaining an open-source project;
- reducing incident resolution time;
- completing a repository modernization program;
- improving code review quality;
- operating a developer-tool business;
- managing a research portfolio;
- coordinating human engineers and coding agents.

---

## 4. Scope

### 4.1 In Scope

Workforce Runtime V2 owns:

- persistent organization state;
- positions and departments;
- position-to-occupant assignments;
- human and AI workforce identity;
- authority relationships;
- project portfolio state;
- goals and success criteria;
- budget allocation;
- organizational decisions;
- structured reports;
- work-flow observation;
- organizational metrics;
- organizational findings;
- change proposals;
- organizational simulation;
- controlled organizational changes;
- post-change evaluation;
- execution-backend abstraction;
- human approval and intervention;
- organizational audit history.

### 4.2 Out of Scope

Workforce Runtime V2 does not attempt to provide:

- a new foundation-model API;
- a new general-purpose agent loop;
- a complete coding agent;
- browser automation infrastructure;
- terminal streaming infrastructure;
- a full distributed container scheduler;
- model training or fine-tuning;
- a universal enterprise integration platform;
- fully autonomous control of high-risk production systems.

Those capabilities may be provided by execution backends or external tools.

---

## 5. Core Design Principle

The system must distinguish between organizational identity and execution implementation.

A long-lived organizational position must not be tied to one specific model, process, or agent session.

The following entities must remain separate:

- Position
- Occupant
- Worker Definition
- Worker Run
- Assignment
- Task
- Project
- Decision

Example:

```yaml
position:
  id: vp_engineering
  title: VP Engineering
  department_id: engineering
  responsibilities:
    - technical strategy
    - engineering budget allocation
    - cross-team architectural decisions
  authority:
    - approve_project_budget
    - create_engineering_positions
    - pause_engineering_project

occupancy:
  position_id: vp_engineering
  occupant_type: ai_worker
  occupant_id: claude_worker_12
  effective_from: 2026-06-20
```

The position remains stable even if the occupant changes from Claude to Codex or from an AI worker to a human.

---

## 6. System Architecture

```text
External Systems
├── GitHub / GitLab / Perforce
├── Jira / Linear
├── Slack / Email / Calendar
├── CI / CD / Monitoring
├── Financial Systems
├── Product Analytics
└── Human Inputs
          │
          ▼
Event Ingestion and Normalization
          │
          ▼
Organization State Store
├── Organization Graph
├── Position Registry
├── Occupancy History
├── Project Portfolio
├── Goal Graph
├── Task and Assignment Ledger
├── Decision Ledger
├── Budget Ledger
├── Authority Graph
├── Report Store
├── Artifact Store
└── Event Store
          │
          ├───────────────────────────────┐
          ▼                               ▼
Organizational Analytics           Organization Governor
├── Work Graph Builder             ├── Observe
├── Bottleneck Detection           ├── Diagnose
├── Goal Drift Analysis            ├── Propose
├── Information Distortion         ├── Request Simulation
├── Cost and Throughput             ├── Recommend
├── Decision Quality               └── Evaluate Outcome
└── Organization Health
          │                               │
          └───────────────┬───────────────┘
                          ▼
                  Simulation Engine
                  ├── Historical Replay
                  ├── Discrete Event Model
                  ├── Policy Simulation
                  ├── Capacity Simulation
                  └── Scenario Comparison
                          │
                          ▼
                  Change Management
                  ├── Validate
                  ├── Approve
                  ├── Apply
                  ├── Roll Back
                  └── Evaluate
                          │
                          ▼
                  Execution Backends
                  ├── Codex
                  ├── Claude Code
                  ├── Hive
                  ├── AgentsMesh
                  ├── Generic MCP Agent
                  └── Human Worker
```

---

## 7. Core Domain Model

## 7.1 Organization

Represents a persistent human-AI organization.

```yaml
organization:
  id: workforce_runtime_project
  name: Workforce Runtime Project
  mission: Build and operate an organizational control plane for AI workforces.
  status: active
  root_goal_ids:
    - public_alpha
    - real_world_validation
```

---

## 7.2 Department

Represents a durable organizational grouping.

```yaml
department:
  id: engineering
  name: Engineering
  parent_department_id: null
  leader_position_id: vp_engineering
  mandate:
    - build the runtime
    - maintain system reliability
```

Departments may be functional, project-based, regional, temporary, or virtual.

---

## 7.3 Position

Represents a durable organizational role.

```yaml
position:
  id: runtime_engineer
  title: Runtime Engineer
  department_id: engineering
  reports_to_position_id: engineering_manager
  responsibilities:
    - runtime implementation
    - backend integration
  required_capabilities:
    - coding
    - repository_access
    - test_execution
  budget_account_id: runtime_engineering_budget
  status: active
```

A position can exist without an occupant.

A position may also have:

- primary occupant;
- acting occupant;
- supervisor;
- mirror agent;
- temporary delegate;
- backup occupant.

---

## 7.4 Occupant

Represents an entity capable of filling a position.

Occupant types:

- human;
- AI worker;
- team;
- service;
- external contractor.

```yaml
occupant:
  id: codex_worker_7
  type: ai_worker
  worker_definition_id: codex_default
  capabilities:
    - software_engineering
    - testing
    - repository_modification
  status: available
```

---

## 7.5 Occupancy

Represents the relationship between a position and its occupant.

```yaml
occupancy:
  id: occupancy_441
  position_id: runtime_engineer
  occupant_id: codex_worker_7
  occupancy_type: primary
  effective_from: 2026-06-20
  effective_to: null
  status: active
```

Occupancy history must be immutable and auditable.

---

## 7.6 Project

Represents a long-running initiative competing for organizational resources.

```yaml
project:
  id: github_shadow_mode
  name: GitHub Shadow Mode
  owner_position_id: product_lead
  root_goal_id: real_world_validation
  status: active
  budget_account_id: shadow_mode_budget
  success_metrics:
    - recommendation_precision
    - bottleneck_detection_accuracy
    - user_acceptance_rate
```

---

## 7.7 Goal

Goals must form a traceable graph.

```yaml
goal:
  id: public_alpha
  parent_goal_id: null
  objective: Release a usable public alpha.
  owner_position_id: ceo
  success_criteria:
    - one real repository integrated
    - one dynamic organization change evaluated
    - complete audit trail available
  status: active
```

Each project, task, decision, and report should trace back to one or more goals.

---

## 7.8 Decision

Decision is a first-class organizational object.

```yaml
decision:
  id: decision_104
  question: Should the release be delayed?
  owner_position_id: product_lead
  status: decided

  options:
    - id: ship_now
      description: Ship the current scope.
    - id: delay
      description: Delay by two weeks.
    - id: reduce_scope
      description: Remove the unreviewed integration.

  evidence_ids:
    - security_report_8
    - customer_feedback_21
    - reliability_metrics_17

  dissent:
    - position_id: sales_lead
      argument: Delay may lose the pilot customer.

  selected_option_id: reduce_scope

  rationale:
    - Preserve the customer deadline.
    - Remove unreviewed functionality.
    - Maintain rollback safety.

  assumptions:
    - Pilot customer accepts reduced scope.
    - Security review completes next week.

  expected_outcomes:
    incident_probability: 0.05
    customer_retention_probability: 0.8

  revisit_conditions:
    - customer_rejects_scope
    - critical_security_issue_found
```

After execution, the decision should be evaluated against actual outcomes.

---

## 7.9 Organizational Finding

A finding represents a detected organizational issue or opportunity.

```yaml
finding:
  id: finding_902
  type: approval_bottleneck
  severity: high
  affected_positions:
    - engineering_manager
  affected_projects:
    - runtime_v2
  evidence:
    - 42_percent_tasks_waiting_for_manager
    - median_wait_19_hours
  confidence: 0.91
  status: open
```

Finding types may include:

- bottleneck;
- duplicated work;
- overloaded position;
- underused position;
- goal drift;
- information distortion;
- missing capability;
- excessive escalation;
- weak decision quality;
- budget inefficiency;
- authority mismatch;
- responsibility gap;
- coordination failure.

---

## 7.10 Organization Change Proposal

```yaml
change_proposal:
  id: proposal_77
  type: modify_approval_policy
  proposer_id: governor_default
  affected_positions:
    - engineering_manager

  current_state:
    low_risk_tasks_require_manager_approval: true

  proposed_state:
    low_risk_tasks_require_manager_approval: false
    verifier_required: true

  rationale:
    - Manager approval is the main queue bottleneck.
    - Low-risk tasks have a 96 percent verifier pass rate.

  expected_effects:
    median_cycle_time_change: -0.24
    manager_load_change: -0.31
    quality_change: -0.01

  risk_level: medium
  simulation_required: true
  human_approval_required: true
  status: proposed
```

---

## 7.11 Organization Change Set

A change set groups multiple atomic changes.

Examples:

- create position;
- archive position;
- reassign occupant;
- change reporting line;
- change department;
- modify responsibility;
- modify authority;
- modify budget;
- modify approval policy;
- pause project;
- resume project;
- create temporary team;
- split team;
- merge teams.

A change set must be validated before execution.

---

## 7.12 Experiment

```yaml
experiment:
  id: experiment_flat_vs_hierarchical
  hypothesis: A flatter review structure reduces cycle time without increasing defects.
  baseline_snapshot_id: snapshot_120
  treatment_change_set_id: changeset_44
  evaluation_window_days: 14

  metrics:
    - task_cycle_time
    - defect_rate
    - rework_rate
    - human_intervention_count

  rollback_conditions:
    defect_rate_increase: 0.10

  status: running
```

---

## 8. Dual Graph Model

Workforce Runtime maintains two different organizational graphs.

## 8.1 Authority Graph

The authority graph describes formal relationships:

- reporting lines;
- approval authority;
- budget authority;
- delegation rights;
- permission inheritance;
- escalation routes.

It answers:

- who reports to whom;
- who can approve a budget;
- who can create a position;
- who can stop a project;
- who can grant a capability.

The authority graph must remain acyclic unless the organization explicitly supports matrix relationships.

---

## 8.2 Work Graph

The work graph describes actual interactions observed during execution.

Edges may represent:

- task assignment;
- review;
- approval;
- blocking;
- information request;
- artifact production;
- artifact consumption;
- escalation;
- decision participation;
- communication;
- dependency;
- rework.

Each edge should contain:

```yaml
work_edge:
  source_id: runtime_engineer
  target_id: security_reviewer
  type: review_request
  count: 18
  median_latency_seconds: 7200
  failure_rate: 0.11
  observation_window: 30_days
```

The work graph answers:

- where work actually flows;
- which positions are bottlenecks;
- which teams are bypassed;
- which people are hidden dependencies;
- which relationships create rework;
- where organizational reality differs from the formal structure.

---

## 9. Organization Governor

## 9.1 Responsibility

The Organization Governor is responsible for organizational reasoning.

It should:

1. inspect organization snapshots;
2. inspect metrics and findings;
3. request additional evidence;
4. identify root causes;
5. generate alternative organizational changes;
6. request simulations;
7. compare simulation results;
8. produce recommendations;
9. submit change proposals;
10. evaluate actual post-change outcomes.

The Governor does not directly mutate organization state.

It proposes structured changes.

The Runtime validates and applies them.

---

## 9.2 Governor Interface

```python
class OrganizationGovernor(Protocol):
    async def inspect(
        self,
        snapshot: OrganizationSnapshot,
        metrics: OrganizationMetrics,
        findings: list[OrganizationalFinding],
    ) -> GovernorAssessment:
        ...

    async def propose_changes(
        self,
        assessment: GovernorAssessment,
        constraints: GovernanceConstraints,
    ) -> list[OrganizationChangeProposal]:
        ...

    async def evaluate_scenarios(
        self,
        proposals: list[OrganizationChangeProposal],
        simulations: list[SimulationResult],
    ) -> GovernorRecommendation:
        ...

    async def evaluate_outcome(
        self,
        experiment: OrganizationExperiment,
        observed_metrics: OrganizationMetrics,
    ) -> OutcomeEvaluation:
        ...
```

Possible Governor implementations:

- LLM-based Governor;
- rule-based Governor;
- optimization-based Governor;
- human Governor;
- committee of specialized Governors;
- hybrid implementation.

---

## 9.3 Governor Safety Boundary

The Governor must never receive unrestricted mutation access.

The change path must be:

```text
Governor Proposal
→ Schema Validation
→ Invariant Validation
→ Policy Validation
→ Simulation
→ Approval
→ Apply
→ Audit
→ Measure
→ Rollback if required
```

---

## 10. Organizational Analytics

## 10.1 Required Metrics

### Work Metrics

- task completion rate;
- median cycle time;
- queue time;
- blocked duration;
- rework rate;
- failure rate;
- review latency;
- approval latency;
- escalation rate.

### Cost Metrics

- token cost;
- model cost;
- human attention cost;
- infrastructure cost;
- cost per accepted outcome;
- cost per project;
- cost per position;
- cost per decision.

### Structural Metrics

- span of control;
- graph depth;
- coordination edge density;
- cross-team dependency rate;
- centrality;
- bus factor;
- authority concentration;
- work-authority mismatch.

### Goal Metrics

- goal completion;
- goal drift;
- task-to-goal traceability;
- abandoned work;
- conflicting objectives;
- unowned goals.

### Decision Metrics

- decision latency;
- reversal rate;
- assumption accuracy;
- forecast calibration;
- dissent frequency;
- evidence completeness;
- expected-versus-actual outcome error.

### Human Interaction Metrics

- approval count;
- human intervention time;
- escalation burden;
- human override rate;
- human rejection rate;
- recommendation acceptance rate.

---

## 10.2 Information Distortion

Reports must preserve provenance.

Each claim in a report should optionally reference:

- evidence;
- task;
- artifact;
- event;
- lower-level report;
- decision.

The system should compare reports across management layers.

Potential distortion signals:

- removed blockers;
- weakened risk language;
- increased confidence without evidence;
- missing dissent;
- changed numerical claims;
- altered recommended action;
- lost acceptance criteria;
- unsupported summary claims.

Example output:

```yaml
distortion_analysis:
  source_report_id: worker_report_8
  derived_report_id: manager_report_4
  fact_retention_score: 0.78
  risk_retention_score: 0.51
  dissent_retention_score: 0.20
  confidence_inflation: 0.17
```

---

## 11. Simulation Engine

## 11.1 Purpose

The Simulation Engine estimates how an organization change may affect real work before the change is deployed.

Simulation does not need to perfectly predict the future.

It should provide comparable scenario estimates and expose uncertainty.

---

## 11.2 Simulation Modes

### Historical Replay

Replay past work events under modified policies or structures.

Examples:

- remove one approval step;
- change reviewer assignment;
- increase worker capacity;
- change routing policy;
- replace one occupant;
- modify escalation threshold.

### Discrete Event Simulation

Model:

- task arrival;
- queueing;
- worker capacity;
- review;
- approval;
- failure;
- retry;
- escalation;
- dependency;
- budget exhaustion.

### Agent-Based Simulation

Use simulated occupants to reason about alternative organizational designs.

This mode is more expensive and less deterministic.

It should not be the initial implementation.

### Policy Simulation

Evaluate proposed organization policies against historical or synthetic events.

---

## 11.3 Simulation Result

```yaml
simulation_result:
  scenario_id: scenario_12
  baseline_snapshot_id: snapshot_120
  proposal_id: proposal_77

  predicted_metrics:
    median_cycle_time_change: -0.24
    rework_rate_change: 0.03
    cost_change: -0.08
    manager_load_change: -0.31

  confidence_intervals:
    median_cycle_time_change:
      low: -0.31
      high: -0.15

  assumptions:
    - historical_task_distribution_remains_stable
    - worker_capacity_unchanged

  warnings:
    - limited_samples_for_security_tasks
```

---

## 12. Shadow Mode

Shadow Mode allows Workforce Runtime to observe a real organization without changing it.

### 12.1 Shadow Mode Behavior

The system may:

- ingest events;
- build the work graph;
- calculate metrics;
- detect findings;
- generate recommendations;
- simulate changes;
- compare its recommendations with human decisions.

The system may not:

- send external messages;
- modify repositories;
- change assignments;
- approve budgets;
- alter permissions;
- deploy changes.

---

## 12.2 Autonomy Levels

```text
Level 0: Observe
Level 1: Recommend
Level 2: Draft actions
Level 3: Execute with approval
Level 4: Execute low-risk changes
Level 5: Limited autonomous governance
```

Autonomy is granted per action type, position, project, and risk level.

---

## 13. Change Management

## 13.1 Change Lifecycle

```text
Draft
→ Proposed
→ Validated
→ Simulated
→ Awaiting Approval
→ Approved
→ Applying
→ Active
→ Evaluating
→ Retained or Rolled Back
```

---

## 13.2 Required Invariants

Examples:

- reporting graph must not contain an invalid cycle;
- each active position must belong to an organization;
- each primary occupancy must be unique;
- required responsibilities must remain owned;
- authority grants must not exceed policy;
- budget allocations must balance;
- active tasks must have valid owners;
- archived positions cannot receive new work;
- terminating an occupancy must trigger handoff;
- high-risk changes require approval;
- rollback information must exist before applying reversible changes.

---

## 13.3 Handoff

When an occupant changes, the Runtime should create a handoff artifact containing:

- current responsibilities;
- active tasks;
- unresolved blockers;
- recent decisions;
- relevant memory;
- pending commitments;
- permissions;
- budget state;
- important contacts;
- known risks.

---

## 14. Execution Backend Protocol

Execution backends should expose organizationally meaningful operations.

```python
class ExecutionBackend(Protocol):
    async def provision_occupant(
        self,
        request: ProvisionOccupantRequest,
    ) -> ProvisionedOccupant:
        ...

    async def assign_task(
        self,
        assignment: AssignmentContract,
    ) -> WorkerRun:
        ...

    async def get_run_status(
        self,
        run_id: str,
    ) -> WorkerRunStatus:
        ...

    async def suspend_occupant(
        self,
        occupant_id: str,
    ) -> None:
        ...

    async def collect_report(
        self,
        run_id: str,
    ) -> ReportContract:
        ...

    async def collect_usage(
        self,
        run_id: str,
    ) -> UsageRecord:
        ...
```

The Runtime should support execution backends with different capabilities.

A backend may support:

- long-lived agents;
- ephemeral workers;
- human tasks;
- structured reports;
- only terminal output;
- native budgeting;
- native permissions;
- native memory;
- none of the above.

Capabilities must be declared explicitly.

---

## 15. Real-World Validation

The system must be evaluated on real outcomes.

The primary experimental comparison should be:

```text
A. Single strong agent
B. Fixed multi-agent organization
C. Governor-managed dynamic organization
```

Candidate evaluation domains:

- open-source project maintenance;
- CI failure triage;
- issue-to-PR workflow;
- repository modernization;
- long-running feature development;
- software incident investigation;
- research and prototype portfolio.

Required measurements:

- outcome quality;
- completion rate;
- cost;
- latency;
- rework;
- human attention;
- goal drift;
- number of organizational changes;
- benefit of each change;
- simulation accuracy.

---

## 16. Initial Product Scenario

The recommended first scenario is:

> Workforce Runtime observes and helps operate a real GitHub project over multiple weeks.

The system should ingest:

- issues;
- PRs;
- review comments;
- commits;
- CI results;
- assignees;
- project labels;
- timestamps;
- releases.

It should produce:

- an authority graph;
- an observed work graph;
- bottleneck findings;
- position utilization metrics;
- decision records;
- organizational recommendations;
- simulated alternatives;
- controlled interventions;
- post-intervention evaluation.

Possible interventions:

- introduce a CI triage position;
- remove unnecessary approval steps;
- create a shared reviewer role;
- assign a coding agent to a specific task category;
- split one team into two project squads;
- change review routing;
- change escalation policy.

---

## 17. Product Differentiation

Workforce Runtime does not differentiate itself by merely providing:

- multiple agents;
- roles;
- task delegation;
- budgets;
- permissions;
- memory;
- dashboards;
- agent communication.

Its differentiation comes from combining:

1. position and occupant separation;
2. authority graph and observed work graph;
3. decision ledger with outcome evaluation;
4. shadow organization mode;
5. organizational findings;
6. counterfactual simulation;
7. controlled organizational change;
8. real-world post-change evaluation;
9. support for heterogeneous AI and human execution backends.

---

## 18. Success Criteria

V2 is successful when the system can demonstrate the following loop:

```text
Observe a real organization
→ detect a measurable structural problem
→ generate multiple change proposals
→ simulate the proposals
→ apply one approved proposal
→ measure the real outcome
→ determine whether the change helped
```

A complete demo must include at least one organizational change that produces a measurable effect on real work.

---

## 19. Long-Term Direction

Potential future directions include:

- organization structure search;
- learned organizational policies;
- cross-company organizational benchmarks;
- marketplace of organization templates;
- human-AI responsibility design;
- agent hiring and termination;
- reputation and internal labor markets;
- matrix organizations;
- multi-organization collaboration;
- organization digital twins for enterprise teams;
- governance compliance;
- organization policy verification;
- causal inference for organizational changes;
- automated organization experimentation.

The long-term objective is to create a general control plane for organizations in which humans and AI workers jointly perform real work.