from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from workforce_runtime.dashboard import render_agent_trajectories, render_event_replay, render_text_dashboard
from workforce_runtime.dashboard.web_dashboard import add_web_dashboard_args, serve_web_dashboard
from workforce_runtime.server.demo import run_sample_repo_fix_demo, run_simple_status_demo, run_web_research_demo
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage import load_org_from_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workforce-runtime")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(".workforce_runtime/runtime.sqlite"),
        help="Path to the Workforce Runtime SQLite database.",
    )
    subparsers = parser.add_subparsers(dest="command")

    org_parser = subparsers.add_parser("org", help="Organization graph commands")
    org_subparsers = org_parser.add_subparsers(dest="org_command")
    org_print = org_subparsers.add_parser("print", help="Print an org chart")
    org_print.add_argument("path", type=Path, help="Path to org YAML")

    init_parser = subparsers.add_parser("init", help="Initialize runtime storage")
    init_parser.add_argument("--org", required=True, type=Path, help="Path to org YAML")

    demo_parser = subparsers.add_parser("demo", help="Run a packaged demo")
    demo_parser.add_argument("name", choices=["sample-repo-fix", "simple-status", "web-research"])
    demo_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(".workforce_runtime/demo/sample-repo-fix"),
        help="Workspace directory for demo files.",
    )

    dashboard_parser = subparsers.add_parser("dashboard", help="Print the text dashboard")
    dashboard_parser.add_argument("--replay", action="store_true", help="Print the event replay instead")
    dashboard_parser.add_argument("--trajectories", action="store_true", help="Print per-agent trajectories")
    dashboard_parser.add_argument("--watch", action="store_true", help="Refresh dashboard repeatedly")
    dashboard_parser.add_argument("--serve", action="store_true", help="Serve the web dashboard")
    dashboard_parser.add_argument("--interval", type=float, default=1.0, help="Watch refresh interval in seconds")
    dashboard_parser.add_argument("--iterations", type=int, default=1, help="Number of watch refreshes")
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

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "org" and args.org_command == "print":
        organization = load_org_from_yaml(args.path)
        print(organization.to_org_chart_text())
        return

    if args.command == "init":
        with WorkforceRuntime(args.db) as runtime:
            organization = runtime.initialize_org(args.org)
        print(f"Initialized {organization.company.name} with {len(organization.agents)} agents.")
        return

    if args.command == "dashboard":
        if args.serve:
            serve_web_dashboard(args.db, host=args.host, port=args.port, config_path=args.config)
            return
        with WorkforceRuntime(args.db) as runtime:
            if args.replay:
                print(render_event_replay(runtime.store))
            elif args.trajectories:
                print(render_agent_trajectories(runtime.store))
            elif args.watch:
                for iteration in range(max(args.iterations, 1)):
                    if iteration:
                        print("\n" + "=" * 80 + "\n")
                    print(render_text_dashboard(runtime.store))
                    if iteration < args.iterations - 1:
                        time.sleep(max(args.interval, 0))
            else:
                print(render_text_dashboard(runtime.store))
        return

    if args.command == "demo" and args.name == "sample-repo-fix":
        from workforce_runtime.server.demo import run_sample_repo_fix_demo

        print(run_sample_repo_fix_demo(args.db, args.workspace))
        return

    if args.command == "demo" and args.name == "simple-status":
        print(run_simple_status_demo(args.db, args.workspace))
        return

    if args.command == "demo" and args.name == "web-research":
        print(run_web_research_demo(args.db, args.workspace))
        return

    if args.command == "mcp" and args.mcp_command == "serve":
        from workforce_runtime.mcp.server import serve_stdio

        serve_stdio(args.db)
        return

    if args.command == "mcp" and args.mcp_command == "dashboard":
        serve_web_dashboard(args.db, host=args.host, port=args.port, config_path=args.config)
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

    print("Workforce Runtime: organization runtime skeleton is ready.")
