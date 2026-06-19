from __future__ import annotations

Capability = str

READ_REPO: Capability = "read_repo"
WRITE_BRANCH: Capability = "write_branch"
RUN_TESTS: Capability = "run_tests"
SUBMIT_ARTIFACT: Capability = "submit_artifact"
REPORT: Capability = "report"
DELEGATE_TASK: Capability = "delegate_task"
REQUEST_BUDGET: Capability = "request_budget"
REQUEST_PERMISSION: Capability = "request_permission"
APPROVE_BUDGET: Capability = "approve_budget"
HIRE_AGENT: Capability = "hire_agent"

DEFAULT_CAPABILITIES: set[Capability] = {
    READ_REPO,
    WRITE_BRANCH,
    RUN_TESTS,
    SUBMIT_ARTIFACT,
    REPORT,
    DELEGATE_TASK,
    REQUEST_BUDGET,
    REQUEST_PERMISSION,
    APPROVE_BUDGET,
    HIRE_AGENT,
}
