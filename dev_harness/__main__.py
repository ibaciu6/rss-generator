#!/usr/bin/env python3
"""
Development harness for rss-generator staged pipeline.

Usage:
    python -m dev_harness run --new-since origin/main --label new-sites --live
    python -m dev_harness run --new xfilme,xcinema --replay runs/<previous_run>
    python -m dev_harness rerun-stage runs/<run> --from enrich
    python -m dev_harness report runs/<run>
    python -m dev_harness compare runs/<run_a> runs/<run_b>
    python -m dev_harness list
    python -m dev_harness selftest
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dev_harness.runner import RunHarness
from dev_harness.compare import compare_runs
from dev_harness.report import generate_report
from dev_harness.list_runs import list_runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_harness",
        description="Staged pipeline test harness for rss-generator",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run command
    run_parser = subparsers.add_parser("run", help="Run a full or partial pipeline")
    run_parser.add_argument("--sites", help="Comma-separated site names or feed files")
    run_parser.add_argument("--all", action="store_true", help="Run all enabled sites")
    run_parser.add_argument("--new-since", metavar="GIT_REF", help="Detect new/changed sites since git ref")
    run_parser.add_argument("--new", metavar="NAMES", help="Comma-separated sites to treat as new")
    run_parser.add_argument("--label", default="", help="Label for run directory")
    run_parser.add_argument("--live", action="store_true", help="Live mode (default)")
    run_parser.add_argument("--replay", metavar="RUN_DIR", help="Replay from recorded artifacts")
    run_parser.add_argument("--stages", default="crawl,generate,enrich,fix,index,reader",
                           help="Comma-separated stages to run")
    run_parser.add_argument("--no-tmdb", action="store_true", help="Disable TMDb enrichment")
    run_parser.add_argument("--baseline-run", metavar="RUN_DIR", help="Baseline run for comparison")
    run_parser.add_argument("--baseline-sample", type=int, default=3,
                           help="Baseline sites per new site (default: 3)")

    # rerun-stage command
    rerun_parser = subparsers.add_parser("rerun-stage", help="Re-run from a specific stage")
    rerun_parser.add_argument("run_dir", help="Run directory to resume from")
    rerun_parser.add_argument("--from", dest="from_stage", required=True,
                             choices=["crawl", "generate", "enrich", "fix", "index", "reader"],
                             help="Stage to start from")
    rerun_parser.add_argument("--stages", default="",
                             help="Comma-separated stages to run (default: from_stage to end)")

    # report command
    report_parser = subparsers.add_parser("report", help="Generate report from run artifacts")
    report_parser.add_argument("run_dir", help="Run directory")
    report_parser.add_argument("--format", choices=["md", "html", "json", "all"], default="all")

    # compare command
    compare_parser = subparsers.add_parser("compare", help="Compare two runs")
    compare_parser.add_argument("run_a", help="First run directory")
    compare_parser.add_argument("run_b", help="Second run directory")
    compare_parser.add_argument("--sites", help="Comma-separated sites to compare")

    # list command
    list_parser = subparsers.add_parser("list", help="List previous runs")
    list_parser.add_argument("--limit", type=int, default=20)

    # selftest command
    test_parser = subparsers.add_parser("selftest", help="Run harness self-tests")
    test_parser.add_argument("--run-dir", help="Run directory to test replay determinism")

    args = parser.parse_args(argv or sys.argv[1:])

    if args.command == "run":
        harness = RunHarness(REPO_ROOT)
        return harness.run(args)
    elif args.command == "rerun-stage":
        harness = RunHarness(REPO_ROOT)
        return harness.rerun_stage(args)
    elif args.command == "report":
        return generate_report(Path(args.run_dir), args.format)
    elif args.command == "compare":
        return compare_runs(Path(args.run_a), Path(args.run_b), args.sites)
    elif args.command == "list":
        return list_runs(REPO_ROOT / "runs", args.limit)
    elif args.command == "selftest":
        from dev_harness.selftest import run_selftest
        return run_selftest(args.run_dir)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())