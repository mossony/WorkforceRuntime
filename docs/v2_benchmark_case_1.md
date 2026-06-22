# Workforce Runtime V2 — SymPy SWE-bench End-to-End Test Plan

## 1. Test Identity

```yaml
test_name: sympy_20590_governor_vertical_test
dataset: SWE-bench Lite
instance_id: sympy__sympy-20590
repository: sympy/sympy
execution_platform: Docker
host_platform: macOS Apple Silicon
```

---

## 2. Test Purpose

本测试用于验证 Workforce Runtime V2 的完整治理闭环：

```text
Load real task
→ Create organization
→ Assign work
→ Observe execution
→ Build metrics and Work Graph
→ Detect organizational problem
→ Generate change proposals
→ Simulate or compare changes
→ Approve and apply a change
→ Produce a patch
→ Evaluate patch with SWE-bench
→ Evaluate organizational outcome
```

本测试需要同时回答两个问题：

### Software outcome

组织最终是否产生了能够通过 SWE-bench 官方测试的 patch？

### Organizational outcome

Governor 是否通过一次有证据、可追踪、可回滚的组织调整，改善了任务执行过程？

---

## 3. Scope

该测试验证：

- Position 与 Occupant 分离；
- Worker assignment；
- task/report/event lifecycle；
- Work Graph；
- finding detection；
- Governor proposal；
- organization snapshot；
- change validation；
- change approval；
- change application；
- handoff；
- patch collection；
- SWE-bench evaluation；
- expected-versus-actual outcome evaluation。

该测试暂时不用于验证：

- 大规模组织优化；
- 多项目预算配置；
- 长期学习；
- 统计显著性；
- 复杂资本分配；
- 多周真实组织演化。

---

# 4. Host Requirements

## 4.1 Hardware

建议最低配置：

```text
Memory: 16 GB
Free disk: 40 GB for this single-instance test
CPU: Apple Silicon M-series
```

完整 SWE-bench 批量评测通常需要更多磁盘，但本测试只执行一个实例。

## 4.2 Required Software

安装：

- Git
- Docker Desktop
- Python 3.11
- Python virtual environment
- Workforce Runtime V2
- Codex CLI、Claude Code 或你支持的 Worker backend

确认：

```bash
git --version
docker --version
docker info
python3.11 --version
```

Docker Desktop 必须处于运行状态。

---

# 5. Directory Layout

创建独立实验目录：

```bash
mkdir -p ~/workforce-tests/sympy-20590
cd ~/workforce-tests/sympy-20590
```

最终目录建议如下：

```text
sympy-20590/
├── SWE-bench/
├── workspace/
│   └── sympy/
├── dataset/
│   └── instance.json
├── predictions/
│   ├── single-agent.jsonl
│   ├── fixed-org.jsonl
│   └── governor-org.jsonl
├── artifacts/
│   ├── single-agent/
│   ├── fixed-org/
│   └── governor-org/
├── workforce-runs/
└── results/
```

创建目录：

```bash
mkdir -p \
  workspace \
  dataset \
  predictions \
  artifacts/single-agent \
  artifacts/fixed-org \
  artifacts/governor-org \
  workforce-runs \
  results
```

---

# 6. Install SWE-bench Harness

## 6.1 Clone

```bash
git clone https://github.com/SWE-bench/SWE-bench.git
cd SWE-bench
```

## 6.2 Create Environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
pip install datasets
```

## 6.3 Verify CLI

```bash
python -m swebench.harness.run_evaluation --help
```

Return to the experiment root:

```bash
cd ..
```

---

# 7. Download the Concrete Instance

Create `extract_instance.py`:

```python
import json
from pathlib import Path

from datasets import load_dataset

INSTANCE_ID = "sympy__sympy-20590"

dataset = load_dataset(
    "princeton-nlp/SWE-bench_Lite",
    split="test",
)

matches = [
    row for row in dataset
    if row["instance_id"] == INSTANCE_ID
]

if len(matches) != 1:
    raise RuntimeError(
        f"Expected exactly one instance for {INSTANCE_ID}, "
        f"found {len(matches)}"
    )

instance = matches[0]

output_path = Path("dataset/instance.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(
    json.dumps(instance, indent=2),
    encoding="utf-8",
)

print(f"Saved {INSTANCE_ID} to {output_path}")
print(f"Repository: {instance['repo']}")
print(f"Base commit: {instance['base_commit']}")
print("\nProblem statement:\n")
print(instance["problem_statement"])
```

Run:

```bash
source SWE-bench/.venv/bin/activate
python extract_instance.py
```

Inspect the task:

```bash
python - <<'PY'
import json

instance = json.load(open("dataset/instance.json"))

for key in [
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
]:
    print(f"\n===== {key} =====")
    print(instance.get(key))
PY
```

## Confidential Evaluator Fields

The following fields must not be provided to Worker Agents:

- `patch`
- `test_patch`
- gold implementation details
- hidden test expectations beyond the issue statement

Agents may receive:

- `instance_id`
- repository at `base_commit`
- `problem_statement`
- normal repository files
- commands needed to run public tests

---

# 8. Validate the Official Gold Environment

Before testing Workforce Runtime, verify that the benchmark itself works.

From the `SWE-bench` directory:

```bash
cd SWE-bench
source .venv/bin/activate

python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path gold \
  --instance_ids sympy__sympy-20590 \
  --max_workers 1 \
  --cache_level instance \
  --namespace '' \
  --run_id sympy-20590-gold-validation
```

Because the host is an ARM Mac, `--namespace ''` instructs the harness to build locally compatible images.

## Expected Result

The gold patch must resolve the instance.

Inspect:

```bash
find evaluation_results -type f -maxdepth 4 -print
```

Locate and inspect the result:

```bash
find evaluation_results \
  -name "results.json" \
  -o -name "instance_results.jsonl"
```

The result should show:

```text
instance_id: sympy__sympy-20590
resolved: true
```

## Stop Condition

Do not proceed if the gold patch fails.

First inspect:

```text
logs/build_images/
logs/run_evaluation/
evaluation_results/
```

Common causes:

- Docker Desktop is not running;
- insufficient Docker disk allocation;
- failed ARM image build;
- transient dependency download failure;
- incorrect Python environment;
- stale Docker cache.

---

# 9. Materialize the Agent Workspace

Read the repository and base commit from the dataset:

```bash
REPO=$(python - <<'PY'
import json
print(json.load(open("dataset/instance.json"))["repo"])
PY
)

BASE_COMMIT=$(python - <<'PY'
import json
print(json.load(open("dataset/instance.json"))["base_commit"])
PY
)

echo "$REPO"
echo "$BASE_COMMIT"
```

Clone the repository:

```bash
git clone "https://github.com/${REPO}.git" workspace/sympy
cd workspace/sympy
git checkout "$BASE_COMMIT"
git switch -c workforce/sympy-20590
git status
cd ../..
```

Create a pristine backup reference:

```bash
cd workspace/sympy
git tag workforce-test-base
cd ../..
```

Before each experimental run, reset the workspace:

```bash
cd workspace/sympy
git reset --hard workforce-test-base
git clean -fdx
git switch -C workforce/sympy-20590 workforce-test-base
cd ../..
```

---

# 10. Task Input Contract

Create `task_input.json` without gold fields:

```bash
python - <<'PY'
import json
from pathlib import Path

instance = json.load(open("dataset/instance.json"))

task = {
    "task_id": "sympy__sympy-20590",
    "project_id": "sympy_20590_experiment",
    "repository": instance["repo"],
    "base_commit": instance["base_commit"],
    "problem_statement": instance["problem_statement"],
    "workspace_path": str(
        Path("workspace/sympy").resolve()
    ),
    "success_contract": {
        "must_produce_git_diff": True,
        "must_not_modify_tests": True,
        "must_explain_root_cause": True,
        "must_report_validation_commands": True,
        "final_score_source": "swebench_harness",
    },
    "constraints": {
        "no_gold_patch_access": True,
        "no_test_patch_access": True,
        "max_worker_runs": 8,
        "max_organization_changes": 2,
    },
}

Path("task_input.json").write_text(
    json.dumps(task, indent=2),
    encoding="utf-8",
)
PY
```

---

# 11. Experimental Organization

## 11.1 Initial Authority Graph

```text
Engineering Manager
├── Investigator
├── Implementer
└── Reviewer
```

## 11.2 Positions

### Engineering Manager

Responsibilities:

- read the issue;
- decompose the work;
- assign investigation;
- choose implementation direction;
- coordinate reports;
- approve final submission.

### Investigator

Responsibilities:

- reproduce or characterize the failure;
- locate relevant code;
- identify root-cause hypotheses;
- produce evidence-backed report;
- avoid modifying production code unless authorized.

### Implementer

Responsibilities:

- consume the investigation report;
- implement the smallest correct fix;
- run available tests;
- produce a patch and implementation report.

### Reviewer

Responsibilities:

- inspect the diff;
- challenge the root-cause claim;
- verify regression risk;
- request corrections;
- approve or reject the patch.

## 11.3 Occupants

Example:

```yaml
occupancies:
  engineering_manager:
    occupant: claude_code_manager

  investigator:
    occupant: codex_worker_1

  implementer:
    occupant: codex_worker_2

  reviewer:
    occupant: claude_code_reviewer
```

Use the actual Worker IDs configured in your Runtime.

---

# 12. Organizational Constraint Injection

A single SWE-bench issue is small, so this experiment deliberately introduces one organizational inefficiency.

## Injected Problem

Require all intermediate transitions to be approved by the Engineering Manager:

```text
Investigation complete
→ Manager approval
→ Implementation starts
→ Manager approval
→ Review starts
→ Manager approval
→ Final patch
```

Also configure the Manager with:

```yaml
manager_constraints:
  max_concurrent_reviews: 1
  artificial_decision_delay_seconds: 30
```

The delay is intentional and must be marked as experimental injection.

Its purpose is to create observable queueing behavior without altering the software problem.

## Expected Organizational Finding

The Governor should detect that:

- the Manager approves low-risk phase transitions;
- approval outcomes are almost always positive;
- workers wait despite no substantive conflict;
- approval latency represents a significant part of total cycle time.

Expected finding type:

```yaml
type: unnecessary_approval_bottleneck
```

---

# 13. Allowed Governor Actions

For this test, constrain the Governor to a small action space.

Allowed:

```yaml
allowed_changes:
  - update_approval_policy
  - change_task_routing
  - assign_secondary_reviewer
  - replace_occupant
```

Disallowed:

```yaml
disallowed_changes:
  - increase_total_budget
  - modify_success_criteria
  - access_gold_patch
  - access_test_patch
  - delete_required_position
  - skip_final_review
  - create_more_than_one_position
```

Maximum applied changes:

```yaml
max_changes: 1
```

This prevents the Governor from solving a small problem through uncontrolled organizational expansion.

---

# 14. Experiment Runs

Run three independent modes from the same base commit.

## Run A: Single Agent

Organization:

```text
Single Coding Agent
```

The Agent receives:

- problem statement;
- repository;
- base commit;
- workspace;
- normal coding tools.

It performs investigation, implementation and self-review.

Governor disabled.

Save artifacts under:

```text
artifacts/single-agent/
```

---

## Run B: Fixed Organization

Use:

```text
Engineering Manager
├── Investigator
├── Implementer
└── Reviewer
```

Keep all injected Manager approvals.

Governor runs in observation-only mode:

```yaml
governor:
  may_generate_findings: true
  may_generate_proposals: true
  may_apply_changes: false
```

Save artifacts under:

```text
artifacts/fixed-org/
```

---

## Run C: Governor-Managed Organization

Use the same initial structure and injected constraints.

Governor may:

1. observe initial execution;
2. issue findings;
3. generate at least two proposals;
4. compare expected effects;
5. submit one proposal;
6. wait for approval;
7. apply one organization change;
8. continue execution.

Recommended change:

```yaml
type: update_approval_policy

before:
  all_phase_transitions_require_manager_approval: true

after:
  investigation_to_implementation:
    approval: automatic_when_report_contract_valid

  implementation_to_review:
    approval: automatic_when_patch_and_report_exist

  final_submission:
    approval: manager_required
```

Save artifacts under:

```text
artifacts/governor-org/
```

---

# 15. Required Task Lifecycle

Each organizational run should produce the following events:

```text
task_created
task_assigned
investigation_started
report_submitted
approval_requested
approval_granted
implementation_started
artifact_created
report_submitted
review_requested
review_completed
decision_created
decision_made
task_completed
```

Governor-managed run should additionally produce:

```text
metric_calculated
finding_created
change_proposal_created
simulation_completed
change_approval_requested
change_approved
organization_snapshot_created
organization_change_applied
experiment_evaluation_created
```

---

# 16. Worker Deliverables

## 16.1 Investigator Report

Required fields:

```yaml
root_cause_hypotheses: []
relevant_files: []
relevant_symbols: []
observed_behavior: ""
expected_behavior: ""
evidence: []
recommended_next_step: ""
confidence: 0.0
risks: []
```

## 16.2 Implementer Report

Required fields:

```yaml
selected_hypothesis: ""
files_modified: []
implementation_summary: ""
validation_commands: []
validation_results: []
known_risks: []
remaining_uncertainty: []
```

## 16.3 Reviewer Report

Required fields:

```yaml
decision: approve_or_reject
root_cause_alignment: ""
correctness_findings: []
regression_risks: []
test_coverage_findings: []
required_changes: []
confidence: 0.0
```

## 16.4 Final Manager Report

Required fields:

```yaml
problem_summary: ""
root_cause: ""
selected_fix: ""
evidence_chain: []
review_result: ""
known_risks: []
final_recommendation: ""
```

---

# 17. Patch Extraction

At the end of each run:

```bash
cd workspace/sympy
git diff workforce-test-base > ../../artifacts/<RUN_NAME>/model.patch
cd ../..
```

Replace `<RUN_NAME>` with:

```text
single-agent
fixed-org
governor-org
```

Reject the run if:

```bash
test ! -s "artifacts/<RUN_NAME>/model.patch"
```

Check that tests were not modified:

```bash
cd workspace/sympy

git diff --name-only workforce-test-base |
  grep -E '(^|/)(test|tests)(/|_)' &&
  echo "WARNING: test files were modified"

cd ../..
```

Any test modification requires manual inspection and should normally invalidate the run.

---

# 18. Build SWE-bench Prediction Files

Create `make_prediction.py`:

```python
import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--model-name", required=True)
    args = parser.parse_args()

    patch_path = (
        Path("artifacts")
        / args.run_name
        / "model.patch"
    )

    if not patch_path.exists():
        raise FileNotFoundError(patch_path)

    patch = patch_path.read_text(encoding="utf-8")

    if not patch.strip():
        raise RuntimeError("Patch is empty")

    prediction = {
        "instance_id": "sympy__sympy-20590",
        "model_name_or_path": args.model_name,
        "model_patch": patch,
    }

    output_path = (
        Path("predictions")
        / f"{args.run_name}.jsonl"
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(prediction) + "\n",
        encoding="utf-8",
    )

    print(output_path)


if __name__ == "__main__":
    main()
```

Generate files:

```bash
python make_prediction.py \
  --run-name single-agent \
  --model-name single-agent-baseline

python make_prediction.py \
  --run-name fixed-org \
  --model-name fixed-organization

python make_prediction.py \
  --run-name governor-org \
  --model-name governor-managed-organization
```

---

# 19. Evaluate Each Run

From the `SWE-bench` environment:

```bash
source SWE-bench/.venv/bin/activate
```

## 19.1 Single Agent

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path predictions/single-agent.jsonl \
  --instance_ids sympy__sympy-20590 \
  --max_workers 1 \
  --cache_level instance \
  --namespace '' \
  --run_id sympy-20590-single-agent
```

## 19.2 Fixed Organization

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path predictions/fixed-org.jsonl \
  --instance_ids sympy__sympy-20590 \
  --max_workers 1 \
  --cache_level instance \
  --namespace '' \
  --run_id sympy-20590-fixed-org
```

## 19.3 Governor-Managed Organization

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path predictions/governor-org.jsonl \
  --instance_ids sympy__sympy-20590 \
  --max_workers 1 \
  --cache_level instance \
  --namespace '' \
  --run_id sympy-20590-governor-org
```

---

# 20. Primary Observations

## 20.1 Final Correctness

For each run:

```yaml
resolved: true_or_false
tests_passed: true_or_false
new_regressions: integer
```

This is the hard outcome.

A faster organization producing an incorrect patch has failed.

---

## 20.2 End-to-End Time

Record:

```yaml
total_wall_time_seconds:
investigation_time_seconds:
implementation_time_seconds:
review_time_seconds:
manager_wait_time_seconds:
```

Expected observation:

```text
Governor-managed organization should reduce manager wait time.
```

---

## 20.3 Human Attention

Record:

```yaml
human_approval_count:
human_intervention_count:
human_attention_seconds:
```

Expected observation:

```text
Governor-managed organization should not increase human intervention.
```

---

## 20.4 Organizational Overhead

Record:

```yaml
manager_decisions:
manager_approval_requests:
reports_generated:
messages_sent:
handoff_count:
organization_change_cost_seconds:
organization_change_token_cost:
```

The Governor change has value only if saved coordination cost exceeds reorganization cost.

---

## 20.5 Governor Diagnosis Quality

Manually classify every finding:

```text
true_positive
partially_correct
false_positive
```

Expected finding:

```text
Manager approval is an avoidable execution bottleneck.
```

Check:

- Was the finding supported by observed events?
- Did it reference real queue and wait metrics?
- Did it distinguish root cause from symptom?
- Was confidence reasonable?

---

## 20.6 Proposal Quality

Each proposal should contain:

- baseline snapshot;
- evidence;
- expected effect;
- risk;
- assumptions;
- rollback condition;
- evaluation metric.

Reject proposals that:

- reference nonexistent IDs;
- lack evidence;
- modify success criteria;
- request unlimited workers;
- bypass final verification;
- cannot be rolled back.

---

## 20.7 Simulation Accuracy

For the selected change, record:

```yaml
predicted_manager_wait_change:
actual_manager_wait_change:

predicted_total_time_change:
actual_total_time_change:

predicted_cost_change:
actual_cost_change:
```

Calculate:

```text
prediction_error =
abs(predicted_change - actual_change)
/
max(abs(actual_change), epsilon)
```

For this single-task smoke test, direction accuracy matters more than precise magnitude:

```text
Did the simulator correctly predict whether the change would help or hurt?
```

---

## 20.8 Information Preservation

Compare:

```text
Investigator report
→ Implementer report
→ Reviewer report
→ Manager final report
```

Check whether the final report preserves:

- root-cause evidence;
- uncertainty;
- regression risk;
- reviewer objections;
- relevant files;
- validation results.

Record:

```yaml
fact_retention:
risk_retention:
dissent_retention:
confidence_inflation:
provenance_completeness:
```

---

# 21. Required V2 Artifacts

The Governor-managed run must contain:

```text
initial_organization_snapshot.json
task_input.json
normalized_events.jsonl
authority_graph_before.json
work_graph_before.json
metrics_before.json
findings.json
governor_assessment.json
change_proposals.json
simulation_results.json
decision.json
approved_change_set.json
organization_change_events.jsonl
post_change_snapshot.json
authority_graph_after.json
work_graph_after.json
metrics_after.json
outcome_evaluation.json
model.patch
final_report.md
```

---

# 22. Success Criteria

## 22.1 Environment Success

- Gold patch resolves the instance.
- Dataset metadata is loaded.
- Repository is checked out at the correct base commit.
- Generated patch can be evaluated by the official harness.

## 22.2 Software Success

At least the Governor-managed run must produce:

```yaml
resolved: true
```

## 22.3 Governance Success

The Governor-managed run must:

- produce at least one evidence-backed finding;
- produce at least two valid proposals;
- select one reversible proposal;
- create pre-change and post-change snapshots;
- apply no more than one change;
- preserve final review;
- generate complete audit events;
- evaluate expected versus actual effects.

## 22.4 Organizational Success

Compared with the fixed organization:

```yaml
resolved_must_not_decrease: true
manager_wait_time_improvement_minimum: 0.30
human_interventions_must_not_increase: true
net_organizational_benefit_must_be_positive: true
```

Because the injected Manager delay is 30 seconds per intermediate approval, a correct approval-policy change should produce a measurable wait-time reduction.

## 22.5 Strong Success

A strong result meets all of the following:

- all three runs produce correct patches;
- Governor-managed run is faster than fixed organization;
- Governor removes unnecessary approvals;
- final review remains intact;
- simulation predicts the correct direction;
- reorganization cost is lower than saved wait cost;
- no unsupported finding is treated as fact;
- information provenance remains complete.

---

# 23. Failure Conditions

The test fails if any of the following occurs:

- Agent receives the gold patch or hidden test patch;
- repository is not at the correct base commit;
- tests are modified to force success;
- Governor changes evaluation criteria;
- Governor increases budget without authorization;
- Governor skips final review;
- organization mutation bypasses validation;
- patch cannot be represented as SWE-bench JSONL;
- Governor-managed patch fails while fixed organization succeeds;
- reorganization overhead exceeds its benefit;
- the finding is generated without supporting events;
- post-change outcome is never evaluated.

---

# 24. Reset Procedure

Before every run:

```bash
cd workspace/sympy

git reset --hard workforce-test-base
git clean -fdx
git switch -C workforce/sympy-20590 workforce-test-base

cd ../..
```

Clear only run-specific Workforce state.

Do not share:

- prior run messages;
- prior patch;
- prior model conversation;
- prior Governor findings;

unless the experiment explicitly tests organizational memory.

---

# 25. Recommended Run Order

Run in this order:

```text
1. Gold environment validation
2. Single-agent baseline
3. Fixed organization
4. Governor-managed organization
5. Official evaluation for all three patches
6. Organizational comparison
7. Manual finding and proposal review
8. Final conclusion
```

This order minimizes accidental contamination.

---

# 26. Final Evaluation Table

Complete the following table:

| Metric | Single Agent | Fixed Organization | Governor Organization |
|---|---:|---:|---:|
| SWE-bench resolved | | | |
| Total wall time | | | |
| Investigation time | | | |
| Implementation time | | | |
| Review time | | | |
| Manager wait time | | | |
| Token cost | | | |
| Human interventions | | | |
| Reports generated | | | |
| Messages sent | | | |
| Organization changes | 0 | 0 | |
| Reorganization cost | 0 | 0 | |
| Finding precision | N/A | N/A | |
| Simulation direction correct | N/A | N/A | |
| Provenance completeness | | | |

---

# 27. Final Decision

At the end, produce one of four conclusions:

```text
PASS:
Governor improved organizational execution while preserving correctness.

PARTIAL PASS:
V2 loop worked, but improvement was too small or simulation was inaccurate.

NO BENEFIT:
Organization change worked technically but produced no positive net effect.

FAIL:
Governor degraded correctness, violated governance constraints, or failed to complete the loop.
```

---

# 28. Core Question

The final report must answer:

> On the same real SWE-bench issue, did the Governor identify an actual organizational inefficiency, safely alter the organization, preserve software correctness, and produce a positive net improvement over the fixed organization?