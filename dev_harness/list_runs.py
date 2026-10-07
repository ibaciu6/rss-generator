#!/usr/bin/env python3
"""
List previous harness runs.
"""
from __future__ import annotations

import json
from pathlib import Path


def list_runs(runs_dir: Path, limit: int = 20) -> int:
    """List previous runs with summary info."""
    if not runs_dir.exists():
        print("No runs directory found.")
        return 0

    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )

    if not run_dirs:
        print("No runs found.")
        return 0

    print(f"{'Run ID':<50} {'Timestamp':<20} {'SHA':<10} {'Sites':>5} {'Stages':>10} {'Status':<10}")
    print("-" * 120)

    count = 0
    for run_dir in run_dirs:
        if count >= limit:
            break

        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            continue

        try:
            with manifest_path.open() as f:
                manifest = json.load(f)

            run_id = manifest.get('run_id', run_dir.name)
            timestamp = manifest.get('timestamp', 'unknown')
            sha = manifest.get('git_sha', 'unknown')[:8]
            sites_count = len(manifest.get('sites_selected', []))
            stages_count = len(manifest.get('stages_requested', []))

            # Determine overall status
            stage_results = manifest.get('stage_results', {})
            statuses = [r.get('status', 'unknown') for r in stage_results.values()]
            if all(s == 'ok' for s in statuses):
                overall = "✅ PASS"
            elif any(s == 'failed' for s in statuses):
                overall = "❌ FAIL"
            elif any(s == 'error' for s in statuses):
                overall = "💥 ERROR"
            else:
                overall = "⚠️ PARTIAL"

            print(f"{run_id:<50} {timestamp:<20} {sha:<10} {sites_count:>5} {stages_count:>10} {overall:<10}")
            count += 1

        except Exception as e:
            print(f"{run_dir.name:<50} ERROR reading manifest: {e}")

    return 0