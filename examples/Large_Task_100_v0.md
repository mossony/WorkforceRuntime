# Workforce Runtime V2 — 100-Role Organization Challenge

## 1. Challenge Name

**OpenForge Public Beta Launch and Operations Challenge**

---

## 2. Mission

在固定预算、有限并发资源和不断变化的外部条件下，设计、实现、测试、发布并运营一个真实可用的多租户 AI 代码审查与仓库智能平台。

平台名称为 `OpenForge`。

OpenForge 需要能够：

* 连接 GitHub 仓库；
* 接收 Pull Request webhook；
* 分析代码改动；
* 运行多个代码审查 Agent；
* 生成 inline review comments；
* 提供 Web dashboard；
* 提供 Python SDK、TypeScript SDK 和 CLI；
* 支持多租户、RBAC 和 API key；
* 记录模型成本、延迟和审查结果；
* 处理失败、重试和人工审批；
* 支持至少一个 MCP integration；
* 在测试环境中完成部署；
* 经过安全、可靠性和产品验收；
* 生成公开发布所需的文档、定价和支持材料；
* 运行一段模拟生产期并处理事故与用户反馈。

---

# 3. V2 Test Objective

本任务的主要目标不是测试 100 个 Agent 能否并行写代码。

它用于验证 Workforce Runtime V2 能否：

1. 建立并维护一个包含约 100 个职位的组织；
2. 区分 Position、Occupant、Worker Run 和 Task；
3. 管理多个相互竞争的项目；
4. 根据实际工作建立 Work Graph；
5. 发现组织瓶颈、重复劳动和能力错配；
6. 在预算限制下调整资源；
7. 生成和评估组织变更方案；
8. 执行人员替换、团队重组和策略调整；
9. 处理突发事件；
10. 用真实产品结果评估组织架构；
11. 比较固定组织和动态组织的表现；
12. 记录完整 Decision Ledger；
13. 评估 Governor 的预测和实际结果是否一致。

核心闭环：

```text
Real Goal
→ Organization Design
→ Work Execution
→ Observation
→ Organizational Diagnosis
→ Change Proposal
→ Simulation
→ Controlled Reorganization
→ Real Outcome
→ Evaluation
```

---

# 4. Initial Constraints

```yaml
challenge:
  duration:
    real_execution_limit_hours: 48
    simulated_operation_days: 30

  workforce:
    total_defined_positions: 100
    max_simultaneously_active_occupants: 25
    max_worker_runs: 500

  budget:
    max_total_model_tokens: 25000000
    max_high_cost_model_tokens: 7000000
    max_compute_budget_usd_equivalent: 500

  human_attention:
    max_required_human_approvals: 15
    max_human_intervention_minutes: 180

  organization_changes:
    max_changes_per_hour: 3
    max_total_applied_changes: 20
    high_risk_changes_require_human_approval: true

  execution:
    isolated_workspace_per_engineering_worker: true
    production_credentials_available: false
    external_messages_require_approval: true
    real_money_spending_disabled: true
```

只有 25 个 Occupant 可以同时活跃，因此 Governor 必须决定：

* 哪些职位当前需要填充；
* 哪些职位可以共享 Occupant；
* 哪些工作应暂停；
* 哪些角色应临时启用；
* 哪些职位价值不足；
* 哪些职位需要替换更强模型；
* 哪些职位可以使用低成本模型。

---

# 5. Required Final Deliverables

## 5.1 Product Deliverables

必须交付：

* 可运行的后端服务；
* 可运行的 Web dashboard；
* GitHub webhook integration；
* 多租户数据隔离；
* RBAC；
* Pull Request 审查任务执行；
* 至少两个代码审查 Agent；
* inline comment 结果模型；
* API；
* Python SDK；
* TypeScript SDK；
* CLI；
* MCP integration；
* 审查历史；
* 成本和延迟 dashboard；
* retry 和 failure recovery；
* audit log；
* Docker Compose 本地部署；
* CI pipeline；
* staging deployment。

## 5.2 Quality Deliverables

必须交付：

* 单元测试；
* 集成测试；
* E2E 测试；
* 安全测试；
* 性能测试；
* chaos test；
* threat model；
* rollback plan；
* incident response runbook；
* release checklist。

## 5.3 Business Deliverables

必须交付：

* 产品定位；
* 目标客户；
* 竞争分析；
* pricing proposal；
* launch plan；
* marketing landing page；
* onboarding guide；
* customer support playbook；
* demo script；
* product analytics plan。

## 5.4 Governance Deliverables

必须交付：

* 初始 Organization Snapshot；
* 最终 Organization Snapshot；
* Authority Graph；
* Work Graph；
* Budget Ledger；
* Decision Ledger；
* 所有 Organization Change Proposals；
* Simulation Results；
* Occupancy History；
* Organization Experiment Results；
* Governor Final Evaluation。

---

# 6. The 100 Positions

## 6.1 Executive and Governance — 8 Positions

1. **Chief Executive Officer**

   * 维护公司使命、总体目标和最终优先级。

2. **Chief Operating Officer**

   * 管理跨部门执行与运营节奏。

3. **Chief Technology Officer**

   * 对技术方向和架构负责。

4. **Chief Product Officer**

   * 对产品范围和用户价值负责。

5. **Chief Financial Officer**

   * 管理预算、成本和资源配置。

6. **Chief Risk and Governance Officer**

   * 负责风险、权限、治理和审批策略。

7. **Chief of Staff**

   * 汇总跨部门信息并维护执行节奏。

8. **Portfolio Management Director**

   * 管理多个项目、依赖、里程碑和资源竞争。

---

## 6.2 Product and User Research — 10 Positions

9. **Head of Product**

10. **Core Platform Product Manager**

11. **Integration Product Manager**

12. **Enterprise Product Manager**

13. **Growth Product Manager**

14. **UX Research Lead**

15. **Customer Interview Researcher**

16. **Competitive Intelligence Analyst**

17. **Market Research Analyst**

18. **Product Analytics Lead**

职责覆盖：

* 用户需求；
* 产品范围；
* acceptance criteria；
* 优先级；
* 竞争分析；
* 用户反馈；
* 产品指标；
* 路线图；
* 需求变更。

---

## 6.3 Architecture and Software Engineering — 22 Positions

19. **Chief Software Architect**

20. **Principal Backend Architect**

21. **Principal Frontend Architect**

22. **API Architect**

23. **Data Architect**

24. **Integration Architect**

25. **Runtime Engineering Lead**

26. **Backend Engineer — Authentication**

27. **Backend Engineer — Tenant Management**

28. **Backend Engineer — Review Orchestration**

29. **Backend Engineer — Event Processing**

30. **Backend Engineer — Repository Integration**

31. **Backend Engineer — Search and Retrieval**

32. **Frontend Engineer — Main Dashboard**

33. **Frontend Engineer — Administration**

34. **Frontend Engineer — Onboarding**

35. **Frontend Engineer — Review Experience**

36. **Python SDK Engineer**

37. **TypeScript SDK Engineer**

38. **CLI Engineer**

39. **MCP Integration Engineer**

40. **Migration and Compatibility Engineer**

职责覆盖：

* 系统架构；
* API；
* 数据模型；
* 任务调度；
* GitHub integration；
* UI；
* SDK；
* CLI；
* MCP；
* 兼容性；
* 性能。

---

## 6.4 Data and AI Systems — 10 Positions

41. **AI Systems Lead**

42. **Agent Orchestration Engineer**

43. **Prompt and Policy Engineer**

44. **Agent Evaluation Engineer**

45. **Model Routing Engineer**

46. **Memory and Retrieval Engineer**

47. **Data Pipeline Engineer**

48. **Analytics Engineer**

49. **Experimentation Scientist**

50. **AI Safety Evaluator**

职责覆盖：

* Agent topology；
* prompt；
* model routing；
* memory；
* RAG；
* evaluation；
* experimentation；
* safety；
* cost-quality trade-off。

---

## 6.5 Quality and Reliability — 10 Positions

51. **Quality Engineering Director**

52. **Unit Test Automation Engineer**

53. **Integration Test Engineer**

54. **End-to-End Test Engineer**

55. **Regression Test Engineer**

56. **Chaos Engineer**

57. **Site Reliability Engineering Lead**

58. **Observability Engineer**

59. **Incident Commander**

60. **Release Quality Manager**

职责覆盖：

* 测试策略；
* 自动化测试；
* 故障注入；
* observability；
* SLO；
* incident；
* release gate。

---

## 6.6 Security, Privacy and Compliance — 10 Positions

61. **Security Architect**

62. **Application Security Engineer**

63. **Cloud Security Engineer**

64. **Identity and Access Management Engineer**

65. **Threat Modeling Analyst**

66. **Red Team Specialist**

67. **Privacy Engineer**

68. **Compliance Analyst**

69. **Legal and Open-Source License Analyst**

70. **Security Incident Responder**

职责覆盖：

* threat model；
* IAM；
* secrets；
* supply-chain security；
* tenant isolation；
* privacy；
* licenses；
* red team；
* incident response。

---

## 6.7 Platform and DevOps — 8 Positions

71. **Platform Engineering Lead**

72. **CI/CD Engineer**

73. **Infrastructure Engineer**

74. **Container and Kubernetes Engineer**

75. **Database Reliability Engineer**

76. **Cloud Cost Optimization Engineer**

77. **Release Engineer**

78. **Environment and Secrets Manager**

职责覆盖：

* infrastructure；
* deployment；
* database；
* CI/CD；
* secrets；
* environment；
* release；
* cloud cost。

---

## 6.8 Design, Documentation and Developer Relations — 8 Positions

79. **Design Director**

80. **Product Designer**

81. **Design Systems Engineer**

82. **Technical Writer**

83. **API Documentation Writer**

84. **Tutorial and Sample Author**

85. **Developer Advocate**

86. **Community Manager**

职责覆盖：

* UX；
* UI；
* design system；
* documentation；
* tutorials；
* community；
* developer adoption。

---

## 6.9 Growth, Sales and Customer Success — 8 Positions

87. **Growth Lead**

88. **Content Strategy Lead**

89. **SEO Analyst**

90. **Demand Generation Analyst**

91. **Solutions Engineer**

92. **Sales Operations Analyst**

93. **Customer Success Manager**

94. **Support Triage Specialist**

职责覆盖：

* launch；
* demand；
* customer onboarding；
* support；
* enterprise readiness；
* solutions；
* feedback。

---

## 6.10 Finance, Operations and Workforce Management — 6 Positions

95. **Financial Controller**

96. **Budget and Forecast Analyst**

97. **Procurement and Vendor Manager**

98. **Operations Coordinator**

99. **Talent and Role Allocation Manager**

100. **Organizational Effectiveness Analyst**

职责覆盖：

* budget；
* forecast；
* resource allocation；
* vendor；
* operations；
* workforce utilization；
* organization evaluation。

---

# 7. Position Activation Model

100 个职位不应全部同时运行。

每个职位具有以下状态：

```text
vacant
planned
staffed
active
paused
delegated
merged
archived
```

Governor 可以：

* 给空缺职位分配 Occupant；
* 让一个 Occupant 临时兼任多个职位；
* 将两个职位合并；
* 创建临时 Squad；
* 暂停低优先级职位；
* 更换低绩效 Occupant；
* 把职位从高成本模型迁移到低成本模型；
* 将高风险职位切换给更可靠的模型；
* 把 AI Position 改为 human-supervised Position。

---

# 8. Project Portfolio

组织同时管理以下项目。

## Project A — Core Runtime

目标：

* 实现 Pull Request 审查执行核心；
* 支持任务状态、重试和结果保存。

## Project B — GitHub Integration

目标：

* OAuth；
* webhook；
* repository access；
* inline review comments。

## Project C — Multi-Tenant Platform

目标：

* organizations；
* users；
* roles；
* tenant isolation；
* API keys。

## Project D — Web Application

目标：

* onboarding；
* repository setup；
* review dashboard；
* cost dashboard；
* administration。

## Project E — Agent Intelligence

目标：

* review agents；
* prompt policies；
* model routing；
* evaluations；
* memory。

## Project F — SDK and Developer Experience

目标：

* Python SDK；
* TypeScript SDK；
* CLI；
* MCP integration；
* docs。

## Project G — Reliability and Security

目标：

* CI/CD；
* observability；
* SLO；
* security；
* privacy；
* incident response。

## Project H — Launch and Operations

目标：

* landing page；
* pricing；
* tutorials；
* support；
* demo；
* public beta readiness。

这些项目竞争相同预算和 Worker capacity。

---

# 9. Initial Organizational Design

初始结构采用传统职能部门：

```text
CEO
├── Product
├── Engineering
├── AI
├── Quality
├── Security
├── Platform
├── Design and Documentation
├── Growth and Customer Success
└── Finance and Operations
```

初始限制：

* 所有跨部门决策都需要 Executive approval；
* 所有 release changes 都需要 Security、QA 和 Release Manager 审批；
* 所有产品需求变更都需要 CPO 审批；
* 所有高成本模型调用都需要 CFO policy；
* 工程职位按职能部门分配，不能直接组成项目 Squad；
* 每个部门独立维护报告。

该初始结构会制造可观察的协调成本。

---

# 10. Expected Organizational Problems

系统应有机会发现以下问题。

## 10.1 Executive Approval Bottleneck

过多决策集中到 CEO、CTO、CPO 和 CFO。

可能表现：

* approval queue 增长；
* worker idle；
* 决策延迟；
* executive context overload。

## 10.2 Functional Silo Problem

Frontend、Backend、QA 和 Product 分属不同部门。

可能表现：

* API contract 不一致；
* integration 返工；
* 等待跨部门审批；
* 需求失真。

## 10.3 Shared Specialist Bottleneck

Security Architect、Release Engineer、MCP Engineer 等职位被多个项目争抢。

可能表现：

* queue；
* project starvation；
* high centrality；
* delayed release。

## 10.4 Duplicate Research

Product、Competitive Intelligence、Growth 和 Sales 分别进行类似市场研究。

可能表现：

* 重复报告；
* 高 token cost；
* 决策输入不一致。

## 10.5 Model Capability Mismatch

低成本模型被分配到高复杂度架构或安全任务。

可能表现：

* retry；
* escalation；
* low acceptance；
* high total cost。

## 10.6 Excessive Reporting Layers

Worker → Lead → Director → Executive。

可能表现：

* risk weakening；
* confidence inflation；
* dissent loss；
* blocker disappearance。

## 10.7 Budget Misallocation

部分低价值项目持续消耗高成本模型预算。

可能表现：

* sunk-cost behavior；
* core project starvation；
* missed release target。

---

# 11. Required Dynamic Events

任务运行过程中，由测试控制器注入以下事件。

Governor 不能提前知道完整事件表。

## Event 1 — Customer Priority Change

时间：

```text
T + 6 hours
```

事件：

三个试用客户表示 MCP integration 不重要，GitHub onboarding 和 review quality 更重要。

期望：

* 产品优先级调整；
* MCP 项目预算可能减少；
* 工程资源重新分配；
* Decision Ledger 记录变化依据。

---

## Event 2 — Critical Security Finding

时间：

```text
T + 10 hours
```

事件：

Red Team 发现 tenant isolation 漏洞，可能导致跨组织数据访问。

期望：

* release 暂停；
* incident team 创建；
* Security、Backend、QA 资源重组；
* 高风险审批启用；
* 修复后运行 regression 和 security verification。

---

## Event 3 — Budget Reduction

时间：

```text
T + 14 hours
```

事件：

总剩余模型预算减少 25%。

期望：

* 重新评估项目组合；
* 暂停低价值工作；
* 高成本模型集中到关键职位；
* CFO 和 Governor 生成资源方案。

---

## Event 4 — Key Occupant Failure

时间：

```text
T + 18 hours
```

事件：

Principal Backend Architect Occupant 连续三个任务失败。

期望：

* capability mismatch finding；
* Occupant replacement；
* handoff；
* Position memory 保留；
* 验证替换后性能变化。

---

## Event 5 — Competitor Launch

时间：

```text
T + 22 hours
```

事件：

竞争对手发布免费的 GitHub review product。

期望：

* Competitive Intelligence 更新；
* Product、Growth、Finance 共同决策；
* 可能调整 differentiation 和 pricing；
* 避免无证据的战略转向。

---

## Event 6 — Staging Outage

时间：

```text
T + 28 hours
```

事件：

staging 环境因数据库连接池耗尽而不可用。

期望：

* Incident Commander 接管；
* mitigation 和 root-cause 两条工作流分离；
* SRE、DB Reliability、Observability 和 Backend 协作；
* 输出 postmortem；
* 更新 architecture 和 runbook。

---

## Event 7 — Release Deadline Acceleration

时间：

```text
T + 36 hours
```

事件：

公开演示提前 6 小时。

期望：

* scope reduction；
* release criteria 重新评估；
* 保留安全和正确性底线；
* 暂停次要功能；
* 生成明确的 release decision。

---

# 12. Governor Allowed Actions

Governor 可以执行：

```yaml
organization_actions:
  - create_temporary_team
  - dissolve_temporary_team
  - assign_occupant
  - replace_occupant
  - delegate_position
  - merge_positions
  - pause_position
  - reactivate_position
  - change_reporting_line
  - modify_responsibility
  - modify_authority
  - modify_task_routing
  - modify_approval_policy
  - reallocate_budget
  - pause_project
  - resume_project
  - change_project_priority
  - create_incident_structure
```

需要审批的动作：

```yaml
human_approval_required:
  - archive_permanent_position
  - increase_total_budget
  - remove_security_gate
  - public_release
  - external_customer_message
  - legal_commitment
```

---

# 13. Recommended Organization Changes

测试不要求 Governor 必须作出这些变化，但这些属于合理候选方案。

## 13.1 Create Cross-Functional Feature Squads

例如：

```text
Onboarding Squad
├── Product Manager
├── Backend Engineer
├── Frontend Engineer
├── QA Engineer
├── Product Designer
└── Technical Writer
```

减少跨部门 handoff。

## 13.2 Create a Shared Review Platform Team

集中：

* review orchestration；
* model routing；
* evaluation；
* prompt policy；
* observability。

## 13.3 Establish Risk-Based Approval

低风险变化：

* automated tests；
* reviewer approval。

高风险变化：

* Security；
* Reliability；
* Release Manager；
* human approval。

## 13.4 Create Incident Command Structure

事故期间临时覆盖常规汇报关系。

## 13.5 Replace Underperforming Occupants

Position 保持不变，Occupant 被替换。

## 13.6 Pause Low-Value Projects

预算减少后，暂停：

* 次要 MCP integration；
* 非关键 marketing automation；
* 高成本但低信心实验。

## 13.7 Change Model Routing by Position

例如：

* Architecture、Security、Incident：高能力模型；
* 文档格式、SEO、基础测试生成：低成本模型；
* Reviewer：独立于 Implementer 的模型。

---

# 14. Required Organization Experiments

至少运行三个组织实验。

## Experiment A — Functional Departments vs Feature Squads

比较：

* integration latency；
* rework；
* cross-team waiting；
* artifact quality。

## Experiment B — Centralized Approval vs Risk-Based Approval

比较：

* decision latency；
* approval queue；
* defect rate；
* human attention。

## Experiment C — Fixed Occupant vs Dynamic Occupant Replacement

比较：

* success rate；
* cost；
* warm-up cost；
* handoff loss；
* task latency。

---

# 15. Workload Inputs

测试控制器提供以下输入。

## 15.1 Initial Codebase

一个部分实现的 monorepo：

```text
openforge/
├── apps/
│   ├── api/
│   └── web/
├── packages/
│   ├── core/
│   ├── agents/
│   ├── sdk-python/
│   ├── sdk-typescript/
│   └── cli/
├── infrastructure/
├── docs/
└── tests/
```

初始状态：

* 约 40% 功能已实现；
* 约 150 个 backlog items；
* 30 个 failing tests；
* 10 个已知 security issues；
* 15 个文档缺口；
* 5 个架构决策尚未完成。

## 15.2 Customer Dataset

提供：

* 20 个客户访谈记录；
* 40 个 support requests；
* 10 个 feature requests；
* 5 个 enterprise security questionnaires；
* 3 份 competitor reports。

## 15.3 Evaluation Dataset

提供 100 个真实或公开 PR diff：

* 40 个包含明确 bug；
* 20 个 security-related changes；
* 20 个 refactoring；
* 20 个正常 feature changes。

平台需要对这些 PR 生成 review，并使用 ground truth 评价：

* issue detection precision；
* recall；
* false-positive rate；
* comment usefulness；
* latency；
* cost。

---

# 16. Final Product Success Criteria

## 16.1 Functional

必须满足：

```yaml
functional:
  github_webhook_ingestion: pass
  multi_tenant_isolation: pass
  pr_review_execution: pass
  inline_comment_generation: pass
  dashboard: pass
  python_sdk: pass
  typescript_sdk: pass
  cli: pass
  mcp_integration: pass
  audit_log: pass
```

## 16.2 Quality

```yaml
quality:
  unit_test_pass_rate_minimum: 0.95
  integration_test_pass_rate_minimum: 0.90
  critical_security_findings: 0
  unresolved_high_security_findings_maximum: 2
  regression_rate_maximum: 0.05
```

## 16.3 AI Review Quality

```yaml
ai_review:
  issue_detection_precision_minimum: 0.70
  issue_detection_recall_minimum: 0.55
  severe_issue_recall_minimum: 0.80
  false_positive_rate_maximum: 0.25
```

## 16.4 Operations

```yaml
operations:
  staging_deployment: successful
  p95_review_latency_minutes_maximum: 10
  failed_job_recovery_rate_minimum: 0.90
  audit_event_coverage_minimum: 0.95
  incident_postmortem_completed: true
```

## 16.5 Business

```yaml
business:
  target_customer_defined: true
  pricing_defined: true
  onboarding_flow_completed: true
  public_documentation_completed: true
  launch_readiness_decision_recorded: true
```

---

# 17. V2 Organizational Success Criteria

## 17.1 Observation

* Work Graph 能从真实事件重建；
* Authority Graph 与 Work Graph 可以比较；
* 至少识别五个真实组织问题；
* findings 包含 evidence 和 confidence。

## 17.2 Governance

* 至少生成十个 change proposals；
* 至少比较三个候选组织结构；
* 所有应用的变化经过 schema 和 invariant validation；
* 高风险变化具有审批记录；
* 每次变化具有 rollback plan。

## 17.3 Dynamic Adaptation

* 至少创建两个 temporary teams；
* 至少替换一个 Occupant；
* 至少暂停一个低价值项目；
* 至少重新分配一次预算；
* 至少修改一次 approval policy；
* 至少完成一次 incident reorganization。

## 17.4 Outcome Evaluation

每次主要变化都要记录：

```text
Expected outcome
Actual outcome
Prediction error
Reorganization cost
Net benefit
```

## 17.5 Governor Quality

```yaml
governor:
  finding_precision_minimum: 0.70
  valid_proposal_rate_minimum: 0.85
  harmful_change_rate_maximum: 0.10
  positive_net_benefit_change_rate_minimum: 0.60
  simulation_direction_accuracy_minimum: 0.70
```

---

# 18. Key Metrics

## Product Outcome

* completed milestones；
* release readiness；
* test pass rate；
* security defects；
* PR review quality；
* staging uptime；
* documentation completeness。

## Organizational Throughput

* task completion rate；
* cycle time；
* queue time；
* blocked duration；
* review latency；
* decision latency。

## Cost

* total model tokens；
* high-cost model usage；
* cost per accepted artifact；
* cost per resolved defect；
* reorganization cost；
* idle Occupant cost。

## Structure

* span of control；
* graph depth；
* work centrality；
* authority centrality；
* work-authority mismatch；
* cross-team dependency count。

## Coordination

* handoff count；
* duplicate work；
* conflicting implementation count；
* escalation count；
* report count；
* message volume。

## Information Quality

* fact retention；
* risk retention；
* dissent retention；
* confidence inflation；
* provenance completeness。

## Adaptation

* time to detect issue；
* time to propose change；
* time to apply change；
* time to observe benefit；
* rollback count；
* beneficial change rate。

---

# 19. Baseline Comparison

建议运行三组。

## Baseline A — Flat 100-Agent Pool

* 没有部门；
* 没有 manager；
* 中央 task router；
* 固定 task routing；
* 无动态重组。

## Baseline B — Fixed 100-Role Organization

* 使用初始职能组织；
* 不允许改变职位、汇报线和预算；
* Governor 只观察。

## Treatment C — Governor-Managed Organization

* 相同初始结构；
* 允许受控重组；
* 允许更换 Occupant；
* 允许项目暂停；
* 允许预算变化；
* 允许 temporary squads。

所有组使用：

* 相同代码库；
* 相同事件；
* 相同预算；
* 相同时间；
* 相同模型池；
* 相同测试标准。

---

# 20. Main Research Questions

本任务最终需要回答：

1. 100 个专业 Position 是否提升了复杂项目结果？
2. 固定组织是否会产生明显的协调瓶颈？
3. Governor 能否发现真正的结构性问题？
4. 动态重组是否改善真实产品结果？
5. 改善是否大于重组成本？
6. Position 与 Occupant 分离是否支持有效换人？
7. Work Graph 是否比 Authority Graph 更准确地反映实际组织？
8. Simulation 是否能预测组织变化方向？
9. 多层管理是否导致信息失真？
10. 组织是否能在预算缩减、事故和需求变化下保持目标一致性？

---

# 21. Required Experiment Artifacts

```text
challenge_config.yaml
initial_organization_snapshot.json
initial_authority_graph.json
position_registry.json
occupancy_registry.json
project_portfolio.json
budget_ledger.json
normalized_events.jsonl
work_graph_snapshots/
organization_metrics/
findings/
change_proposals/
simulation_results/
decisions/
approvals/
organization_change_events.jsonl
occupancy_history.json
incident_records/
experiment_results/
final_organization_snapshot.json
final_authority_graph.json
final_work_graph.json
product_evaluation.json
governor_evaluation.json
final_report.md
```

---

# 22. Pass Conditions

## PASS

* 产品达到主要功能和安全要求；
* Governor 完成至少一次有正向净收益的重组；
* 动态组织优于固定组织；
* 所有关键变化可审计；
* simulation 至少能正确预测主要变化方向；
* 未发生严重治理违规。

## PARTIAL PASS

* 产品成功交付；
* V2 治理闭环工作；
* 动态组织提升不明显；
* 或 simulation 准确度不足。

## NO BENEFIT

* 动态组织完成了重组；
* 实际结果与固定组织相当；
* 重组成本抵消收益。

## FAIL

* 动态组织降低产品质量；
* Governor 频繁重组；
* 预算失控；
* 关键权限约束被绕过；
* 安全问题未阻止发布；
* 组织变化无法回滚或审计。

---

# 23. Core Evaluation Statement

最终报告必须回答：

> 在一个需要约 100 个专业职位共同完成的真实复杂项目中，Workforce Runtime Governor 是否能够持续观察组织、发现结构性问题、重组资源，并让动态组织在产品质量、速度、成本、风险和人类注意力方面优于固定组织？
