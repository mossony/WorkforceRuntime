from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from workforce_runtime.dashboard import render_agent_trajectories, render_event_replay, render_text_dashboard
from workforce_runtime.dashboard.v2_dashboard import render_v2_shadow_dashboard
from workforce_runtime.dashboard.web_dashboard import add_web_dashboard_args, serve_web_dashboard
from workforce_runtime.config import load_runtime_config
from workforce_runtime.evals import (
    build_swe_bench_comparison_cases,
    load_benchmark_case,
    load_swe_bench_instance,
    load_swe_bench_instances_from_hf,
    run_benchmark_case,
    run_swe_bench_instance,
)
from workforce_runtime.org_designer import OrgDesigner, OrgDesignRequest, organization_to_yaml
from workforce_runtime.server.demo import (
    run_large_org_scale_demo,
    run_sample_repo_fix_demo,
    run_simple_status_demo,
    run_web_research_demo,
)
from workforce_runtime.server.large_task_100 import run_large_task_100_real_llm
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage import load_org_from_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workforce-runtime")
    parser.add_argument(
        "--config",
        dest="runtime_config_path",
        type=Path,
        default=None,
        help="Path to the unified Workforce Runtime JSON config.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Runtime database identifier. Use a .sqlite path for SQLite, or omit for configured MySQL.",
    )
    subparsers = parser.add_subparsers(dest="command")

    org_parser = subparsers.add_parser("org", help="Organization graph commands")
    org_subparsers = org_parser.add_subparsers(dest="org_command")
    org_print = org_subparsers.add_parser("print", help="Print an org chart")
    org_print.add_argument("path", type=Path, help="Path to org YAML")
    org_design = org_subparsers.add_parser("design", help="Design an org chart from a short goal")
    org_design.add_argument("--goal", required=True, help="Short description of the organization goal")
    org_design.add_argument("--company-name", default=None)
    org_design.add_argument("--headcount-limit", type=int, default=None)
    org_design.add_argument("--token-budget", type=int, default=None)
    org_design.add_argument("--management-model", default=None)
    org_design.add_argument("--worker-model", default=None)
    org_design.add_argument("--use-llm", action="store_true", help="Use OpenRouter for org design when configured")
    org_design.add_argument("--format", choices=["yaml", "json"], default="yaml")
    org_design.add_argument("--out", type=Path, default=None, help="Optional output file")

    init_parser = subparsers.add_parser("init", help="Initialize runtime storage")
    init_parser.add_argument("--org", required=True, type=Path, help="Path to org YAML")

    demo_parser = subparsers.add_parser("demo", help="Run a packaged demo")
    demo_parser.add_argument("name", choices=["sample-repo-fix", "simple-status", "web-research", "large-org-scale", "large-task-100"])
    demo_parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace directory for demo files.",
    )
    demo_parser.add_argument("--agent-count", type=int, default=None, help="Agent count for large-org-scale.")
    demo_parser.add_argument("--active-agent-limit", type=int, default=None, help="Active slot limit for large-org-scale.")
    demo_parser.add_argument("--plan-path", type=Path, default=None, help="Plan file for large-task-100.")
    demo_parser.add_argument(
        "--allow-position-fallback",
        action="store_true",
        help="Allow large-task-100 to fall back to plan-derived positions if LLM org design fails.",
    )

    dashboard_parser = subparsers.add_parser("dashboard", help="Print the text dashboard")
    dashboard_parser.add_argument("--replay", action="store_true", help="Print the event replay instead")
    dashboard_parser.add_argument("--trajectories", action="store_true", help="Print per-agent trajectories")
    dashboard_parser.add_argument("--watch", action="store_true", help="Refresh dashboard repeatedly")
    dashboard_parser.add_argument("--serve", action="store_true", help="Serve the web dashboard")
    dashboard_parser.add_argument("--interval", type=float, default=None, help="Watch refresh interval in seconds")
    dashboard_parser.add_argument("--iterations", type=int, default=None, help="Number of watch refreshes")
    add_web_dashboard_args(dashboard_parser)

    mcp_parser = subparsers.add_parser("mcp", help="MCP server commands")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command")
    mcp_subparsers.add_parser("serve", help="Run the Workforce Runtime MCP stdio server")
    mcp_dashboard = mcp_subparsers.add_parser("dashboard", help="Serve the Workforce Runtime web dashboard")
    add_web_dashboard_args(mcp_dashboard)

    task_parser = subparsers.add_parser("task", help="Task commands")
    task_subparsers = task_parser.add_subparsers(dest="task_command")

    task_create = task_subparsers.add_parser("create", help="Create a task")
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--objective", required=True)
    task_create.add_argument("--assign-to", default=None)

    task_subparsers.add_parser("list", help="List tasks")

    task_show = task_subparsers.add_parser("show", help="Show a task")
    task_show.add_argument("task_id")
    task_trace = task_subparsers.add_parser("export-trace", help="Export a complete task trace snapshot")
    task_trace.add_argument("task_id")
    task_trace.add_argument("--workspace", type=Path, default=None, help="Directory for the exported trace JSON")
    task_trace.add_argument("--trace-id", default=None, help="Optional stable trace export id")
    task_trace.add_argument("--no-descendants", action="store_true", help="Only include this task, not child tasks")
    task_trace.add_argument("--no-file-contents", action="store_true", help="Include file metadata without inline contents")
    task_trace.add_argument("--max-file-bytes", type=int, default=500000, help="Maximum inline bytes per file")

    review_parser = subparsers.add_parser("review", help="Manager review commands")
    review_subparsers = review_parser.add_subparsers(dest="review_command")
    review_report = review_subparsers.add_parser("report", help="Review a worker report")
    review_report.add_argument("report_id")
    review_report.add_argument("--reviewer", required=True)
    review_report.add_argument(
        "--decision",
        choices=["accept", "reject", "request_retry", "escalate", "request_human_review"],
        default=None,
    )
    review_report.add_argument("--notes", default="")

    benchmark_parser = subparsers.add_parser("benchmark", help="Benchmark organization runs")
    benchmark_subparsers = benchmark_parser.add_subparsers(dest="benchmark_command")
    benchmark_run = benchmark_subparsers.add_parser("run", help="Run one benchmark test case")
    benchmark_run.add_argument("--case", required=True, type=Path, help="Path to benchmark case JSON")
    benchmark_run.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace directory for benchmark artifacts",
    )
    benchmark_run.add_argument("--use-llm", action="store_true", help="Use OpenRouter calls for design and agents")
    benchmark_run.add_argument("--judge", choices=["none", "heuristic", "llm"], default=None)
    benchmark_run.add_argument("--no-reset", action="store_true", help="Do not delete existing benchmark DB/workspace")
    swe_plan = benchmark_subparsers.add_parser("swe-bench-plan", help="Create single/distributed benchmark cases for a SWE-bench instance JSON")
    swe_plan.add_argument("--instance", required=True, type=Path, help="Path to a SWE-bench instance JSON")
    swe_plan.add_argument("--out-dir", required=True, type=Path, help="Directory for generated benchmark case JSON files")
    swe_plan.add_argument("--model", default=None, help="Terminal model for both comparison arms")
    swe_run = benchmark_subparsers.add_parser("swe-bench-run", help="Run real SWE-bench instances locally with an OpenRouter model")
    swe_run.add_argument("--instance-id", action="append", default=[], help="SWE-bench instance id from SWE-bench Lite test split")
    swe_run.add_argument("--instance", action="append", type=Path, default=[], help="Path to a local SWE-bench instance JSON")
    swe_run.add_argument("--workspace", required=True, type=Path, help="Workspace directory for cloned repos and artifacts")
    swe_run.add_argument("--model", default=None, help="OpenRouter model used to generate candidate patches")
    swe_run.add_argument("--max-tokens", type=int, default=None, help="Maximum output tokens for each candidate patch request")
    swe_run.add_argument("--test-timeout", type=int, default=None, help="Per-instance pytest timeout in seconds")
    swe_run.add_argument("--setup-timeout", type=int, default=None, help="Per-command setup timeout in seconds")

    v2_parser = subparsers.add_parser("v2", help="V2 organizational control-plane commands")
    v2_subparsers = v2_parser.add_subparsers(dest="v2_command")
    v2_demo = v2_subparsers.add_parser("demo", help="Run the V2 shadow-governance end-to-end demo")
    v2_demo.add_argument("--github-events", type=Path, default=None, help="Optional GitHub event fixture JSON")
    v2_demo.add_argument("--out", type=Path, default=None, help="Optional output JSON path")
    v2_analyze = v2_subparsers.add_parser("analyze-v1-run", help="Analyze a completed V1 runtime run with V2 governance")
    v2_analyze.add_argument("--task-id", default=None, help="Optional root V1 task id to analyze")
    v2_analyze.add_argument("--v2-db", type=Path, default=None, help="Optional separate SQLite DB for V2 analysis objects")
    v2_analyze.add_argument("--export-dir", type=Path, default=None, help="Directory for V2 analysis artifacts")
    v2_analyze.add_argument("--json", action="store_true", help="Print the full analysis JSON")
    v2_sympy = v2_subparsers.add_parser("sympy-20590", help="Prepare or run the V2 SymPy SWE-bench 20590 case")
    v2_sympy.add_argument("--experiment-dir", type=Path, default=Path.home() / "workforce-tests" / "sympy-20590")
    v2_sympy.add_argument("--prepare-only", action="store_true", help="Only download instance and materialize repo workspace")
    v2_sympy.add_argument("--runtime-db", type=Path, default=None, help="Optional V1 runtime DB path")
    v2_sympy.add_argument("--worker-timeout", type=int, default=None, help="Optional Codex worker timeout in seconds")
    v2_sympy.add_argument("--codex-model", default="openai/gpt-oss-120b:free", help="Override Codex model for the implementer")
    v2_sympy.add_argument("--codex-sandbox-mode", default=None, help="Override Codex sandbox mode, e.g. workspace-write or danger-full-access")
    v2_sympy.add_argument("--codex-profile", default=None, help="Override Codex profile")
    v2_sympy.add_argument("--no-reset-workspace", action="store_true", help="Do not reset repo to workforce-test-base before running")
    v2_sympy.add_argument("--out", type=Path, default=None, help="Optional result JSON path")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime_config = load_runtime_config(args.runtime_config_path)
    runtime_defaults = runtime_config.get("runtime", {})
    org_defaults = runtime_config.get("org_designer", {})
    demo_defaults = runtime_config.get("demos", {})
    benchmark_defaults = runtime_config.get("benchmarks", {})
    swe_defaults = benchmark_defaults.get("swe_bench", {})
    args.db = args.db or Path(str(runtime_defaults.get("db_path") or "workforce_runtime"))

    if args.command == "org" and args.org_command == "print":
        organization = load_org_from_yaml(args.path)
        print(organization.to_org_chart_text())
        return

    if args.command == "org" and args.org_command == "design":
        request = OrgDesignRequest(
            goal=args.goal,
            company_name=args.company_name or str(org_defaults.get("company_name") or "Designed Workforce"),
            headcount_limit=args.headcount_limit if args.headcount_limit is not None else int(org_defaults.get("headcount_limit") or 6),
            token_budget=args.token_budget if args.token_budget is not None else int(org_defaults.get("token_budget") or 600000),
            management_model=args.management_model or str(org_defaults.get("management_model") or "openai/gpt-oss-120b:free"),
            worker_model=args.worker_model or str(org_defaults.get("worker_model") or "poolside/laguna-xs.2:free"),
            include_hr=bool(org_defaults.get("include_hr", True)),
            max_management_depth=int(org_defaults.get("max_management_depth") or 3),
        )
        organization = OrgDesigner().design(request, use_llm=args.use_llm or bool(org_defaults.get("use_llm", False)), allow_fallback=True)
        if args.format == "json":
            output = json.dumps(organization.model_dump(mode="json"), indent=2)
        else:
            output = organization_to_yaml(organization)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output)
            print(f"Wrote designed organization with {len(organization.agents)} agents to {args.out}")
        else:
            print(output)
        return

    if args.command == "init":
        with WorkforceRuntime(args.db) as runtime:
            organization = runtime.initialize_org(args.org)
        print(f"Initialized {organization.company.name} with {len(organization.agents)} agents.")
        return

    if args.command == "dashboard":
        if args.serve:
            dashboard_section = runtime_config.get("dashboard", {})
            serve_web_dashboard(
                args.db,
                host=args.host or str(dashboard_section.get("host") or "127.0.0.1"),
                port=args.port or int(dashboard_section.get("port") or 8765),
                config_path=args.dashboard_config_path or args.runtime_config_path,
            )
            return
        with WorkforceRuntime(args.db) as runtime:
            if args.replay:
                print(render_event_replay(runtime.store))
            elif args.trajectories:
                print(render_agent_trajectories(runtime.store))
            elif args.watch:
                interval = args.interval if args.interval is not None else 1.0
                iterations = args.iterations if args.iterations is not None else 1
                for iteration in range(max(iterations, 1)):
                    if iteration:
                        print("\n" + "=" * 80 + "\n")
                    print(render_text_dashboard(runtime.store))
                    if iteration < iterations - 1:
                        time.sleep(max(interval, 0))
            else:
                print(render_text_dashboard(runtime.store))
        return

    if args.command == "demo" and args.name == "sample-repo-fix":
        from workforce_runtime.server.demo import run_sample_repo_fix_demo

        workspace = args.workspace or Path(str(demo_defaults.get("sample_repo_fix_workspace") or ".workforce_runtime/demo/sample-repo-fix"))
        print(run_sample_repo_fix_demo(args.db, workspace))
        return

    if args.command == "demo" and args.name == "simple-status":
        workspace = args.workspace or Path(str(demo_defaults.get("sample_status_workspace") or ".workforce_runtime/demo/simple-status"))
        print(run_simple_status_demo(args.db, workspace))
        return

    if args.command == "demo" and args.name == "web-research":
        workspace = args.workspace or Path(str(demo_defaults.get("web_research_workspace") or ".workforce_runtime/demo/web-research"))
        print(run_web_research_demo(args.db, workspace))
        return

    if args.command == "demo" and args.name == "large-org-scale":
        large_defaults = demo_defaults.get("large_org_scale", {})
        workspace = args.workspace or Path(str(large_defaults.get("workspace") or ".workforce_runtime/demo/large-org-scale"))
        print(
            run_large_org_scale_demo(
                args.db,
                workspace,
                agent_count=args.agent_count if args.agent_count is not None else int(large_defaults.get("agent_count") or 3000),
                active_agent_limit=(
                    args.active_agent_limit
                    if args.active_agent_limit is not None
                    else int(large_defaults.get("active_agent_limit") or 20)
                ),
                management_model=str(large_defaults.get("management_model") or "openai/gpt-oss-120b:free"),
                worker_model=str(large_defaults.get("worker_model") or "poolside/laguna-m.1:free"),
            )
        )
        return

    if args.command == "demo" and args.name == "large-task-100":
        large_defaults = demo_defaults.get("large_task_100", {})
        workspace = args.workspace or Path(str(large_defaults.get("workspace") or ".workforce_runtime/demo/large-task-100"))
        result = run_large_task_100_real_llm(
            args.db,
            workspace,
            plan_path=args.plan_path or Path(str(large_defaults.get("plan_path") or "examples/Large_Task_100_v0.md")),
            max_agents=args.agent_count if args.agent_count is not None else int(large_defaults.get("agent_count") or 100),
            active_agent_limit=(
                args.active_agent_limit
                if args.active_agent_limit is not None
                else int(large_defaults.get("active_agent_limit") or 25)
            ),
            management_models=[str(item) for item in large_defaults.get("management_models") or []] or None,
            worker_models=[str(item) for item in large_defaults.get("worker_models") or []] or None,
            llm_json_config=large_defaults.get("llm_json"),
            allow_position_fallback=bool(args.allow_position_fallback),
        )
        print(result.model_dump_json(indent=2))
        return

    if args.command == "mcp" and args.mcp_command == "serve":
        from workforce_runtime.mcp.server import serve_stdio

        serve_stdio(args.db)
        return

    if args.command == "mcp" and args.mcp_command == "dashboard":
        dashboard_section = runtime_config.get("dashboard", {})
        serve_web_dashboard(
            args.db,
            host=args.host or str(dashboard_section.get("host") or "127.0.0.1"),
            port=args.port or int(dashboard_section.get("port") or 8765),
            config_path=args.dashboard_config_path or args.runtime_config_path,
        )
        return

    if args.command == "task" and args.task_command == "create":
        with WorkforceRuntime(args.db) as runtime:
            task = runtime.create_task(
                title=args.title,
                objective=args.objective,
                assign_to=args.assign_to,
            )
        print(json.dumps(task.model_dump(mode="json"), indent=2))
        return

    if args.command == "task" and args.task_command == "list":
        with WorkforceRuntime(args.db) as runtime:
            tasks = runtime.list_tasks()
        if not tasks:
            print("No tasks.")
            return
        for task in tasks:
            assignee = task.assigned_to or "unassigned"
            print(f"{task.task_id}\t{task.status}\t{assignee}\t{task.title}")
        return

    if args.command == "task" and args.task_command == "show":
        with WorkforceRuntime(args.db) as runtime:
            task = runtime.require_task(args.task_id)
        print(json.dumps(task.model_dump(mode="json"), indent=2))
        return

    if args.command == "task" and args.task_command == "export-trace":
        with WorkforceRuntime(args.db) as runtime:
            trace = runtime.export_task_trace(
                args.task_id,
                workspace=args.workspace,
                trace_id=args.trace_id,
                include_descendants=not args.no_descendants,
                include_file_contents=not args.no_file_contents,
                max_file_bytes=args.max_file_bytes,
            )
        print(json.dumps(trace.model_dump(mode="json"), indent=2))
        return

    if args.command == "review" and args.review_command == "report":
        with WorkforceRuntime(args.db) as runtime:
            task = runtime.review_report(
                args.report_id,
                reviewer_id=args.reviewer,
                decision=args.decision,
                notes=args.notes,
            )
        print(json.dumps(task.model_dump(mode="json"), indent=2))
        return

    if args.command == "benchmark" and args.benchmark_command == "run":
        case = load_benchmark_case(args.case)
        result = run_benchmark_case(
            args.db,
            workspace=args.workspace or Path(str(benchmark_defaults.get("workspace") or ".workforce_runtime/benchmark/workspace")),
            case=case,
            use_llm=args.use_llm or bool(benchmark_defaults.get("use_llm", False)),
            judge=args.judge or str(benchmark_defaults.get("judge") or "heuristic"),
            reset=not args.no_reset,
            llm_json_config=benchmark_defaults.get("llm_json"),
        )
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return

    if args.command == "benchmark" and args.benchmark_command == "swe-bench-plan":
        instance = load_swe_bench_instance(args.instance)
        cases = build_swe_bench_comparison_cases(instance, model=args.model or str(swe_defaults.get("model") or "poolside/laguna-m.1:free"))
        args.out_dir.mkdir(parents=True, exist_ok=True)
        written: dict[str, str] = {}
        for name, case in cases.items():
            path = args.out_dir / f"{case.id}.json"
            path.write_text(json.dumps(case.model_dump(mode="json"), indent=2))
            written[name] = str(path)
        print(json.dumps({"ok": True, "instance_id": instance.instance_id, "cases": written}, indent=2))
        return

    if args.command == "benchmark" and args.benchmark_command == "swe-bench-run":
        instances = [load_swe_bench_instance(path) for path in args.instance]
        if args.instance_id:
            instances.extend(load_swe_bench_instances_from_hf(args.instance_id))
        if not instances:
            raise SystemExit("provide at least one --instance-id or --instance")
        args.workspace.mkdir(parents=True, exist_ok=True)
        results = [
            run_swe_bench_instance(
                instance,
                workspace=args.workspace,
                model=args.model or str(swe_defaults.get("model") or "poolside/laguna-m.1:free"),
                max_tokens=args.max_tokens if args.max_tokens is not None else int(swe_defaults.get("max_tokens") or 6000),
                test_timeout_seconds=args.test_timeout if args.test_timeout is not None else int(swe_defaults.get("test_timeout_seconds") or 600),
                setup_timeout_seconds=args.setup_timeout if args.setup_timeout is not None else int(swe_defaults.get("setup_timeout_seconds") or 900),
            )
            for instance in instances
        ]
        print(
            json.dumps(
                {
                    "ok": True,
                    "model": args.model or str(swe_defaults.get("model") or "poolside/laguna-m.1:free"),
                    "resolved_count": sum(1 for result in results if result.resolved),
                    "total": len(results),
                    "results": [result.model_dump(mode="json") for result in results],
                },
                indent=2,
            )
        )
        return

    if args.command == "v2" and args.v2_command == "demo":
        from workforce_runtime.v2.pipeline import run_v2_shadow_demo

        result = run_v2_shadow_demo(db_path=args.db, github_events_path=args.github_events)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result.model_dump(mode="json"), indent=2))
        print(render_v2_shadow_dashboard(result))
        return

    if args.command == "v2" and args.v2_command == "analyze-v1-run":
        from workforce_runtime.v2.v1_bridge import analyze_v1_runtime

        result = analyze_v1_runtime(
            v1_db_path=args.db,
            task_id=args.task_id,
            v2_db_path=args.v2_db,
            export_dir=args.export_dir,
        )
        if args.json:
            print(result.model_dump_json(indent=2))
        else:
            print("V2 analysis of V1 run")
            print(f"- organization: {result.organization_id}")
            print(f"- task: {result.task_id or 'all'}")
            print(f"- analyzed tasks: {len(result.analyzed_task_ids)}")
            print(f"- normalized events: {len(result.normalized_events)}")
            print(f"- work edges: {len(result.work_graph.edges)}")
            print(f"- findings: {len(result.findings)}")
            print(f"- proposals: {len(result.proposals)}")
            print("Recommendations:")
            for item in result.recommendations or ["No recommendation generated."]:
                print(f"- {item}")
            if args.export_dir is not None:
                print(f"Artifacts: {args.export_dir}")
        return

    if args.command == "v2" and args.v2_command == "sympy-20590":
        from workforce_runtime.v2.sympy_benchmark import prepare_sympy_20590_case, run_sympy_20590_fixed_org_with_v2_review

        if args.prepare_only:
            preparation = prepare_sympy_20590_case(experiment_dir=args.experiment_dir)
            print(preparation.model_dump_json(indent=2))
            return
        result = run_sympy_20590_fixed_org_with_v2_review(
            experiment_dir=args.experiment_dir,
            runtime_db_path=args.runtime_db,
            worker_timeout_seconds=args.worker_timeout,
            codex_model=args.codex_model,
            codex_sandbox_mode=args.codex_sandbox_mode,
            codex_profile=args.codex_profile,
            reset_workspace=not args.no_reset_workspace,
        )
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(result.model_dump_json(indent=2))
        print("V2 SymPy 20590 fixed-org run")
        print(f"- experiment_dir: {result.preparation.experiment_dir}")
        print(f"- task_id: {result.task_id}")
        print(f"- worker_returncode: {result.worker_returncode}")
        print(f"- patch: {result.patch_path} nonempty={result.patch_nonempty}")
        print(f"- prediction: {result.prediction_path or 'not written'}")
        print(f"- V2 analysis: {result.analysis_export_dir}")
        print(f"- findings: {len(result.v2_analysis.findings)}")
        print("Recommendations:")
        for item in result.v2_analysis.recommendations or ["No recommendation generated."]:
            print(f"- {item}")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        return

    print("Workforce Runtime: organization runtime skeleton is ready.")
