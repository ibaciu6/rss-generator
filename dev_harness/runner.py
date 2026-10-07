#!/usr/bin/env python3
"""
Core runner for the dev harness - orchestrates staged pipeline runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.config import load_config, resolve_feed_files
from dev_harness.git_utils import get_git_info, diff_sites_yaml, find_new_sites
from dev_harness.crawl_recorder import CrawlRecorder
from dev_harness.stage_runner import StageRunner


@dataclass
class RunMetadata:
    """Metadata for a single harness run."""
    run_id: str
    timestamp: str
    git_sha: str
    git_dirty: bool
    command_line: str
    sites_selected: list[str]
    new_sites: list[str]
    baseline_sites: list[str]
    stages_requested: list[str]
    env_flags: dict[str, Any] = field(default_factory=dict)
    stage_results: dict[str, dict] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)


class RunHarness:
    """Main harness class for running and managing pipeline executions."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.runs_dir = repo_root / "runs"
        self.runs_dir.mkdir(exist_ok=True)

    def run(self, args: argparse.Namespace) -> int:
        """Execute a full or partial pipeline run."""
        # Determine run ID and create run directory
        git_info = get_git_info(self.repo_root)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        short_sha = git_info["sha"][:8]
        label_suffix = f"_{args.label}" if args.label else ""
        run_id = f"{timestamp}_{short_sha}{label_suffix}"
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True)

        # Determine sites to run
        sites_config = load_config(self.repo_root / "config" / "sites.yaml")
        selected_sites, new_sites, baseline_sites = self._resolve_sites(
            sites_config, args, run_dir
        )

        # Initialize metadata
        metadata = RunMetadata(
            run_id=run_id,
            timestamp=timestamp,
            git_sha=git_info["sha"],
            git_dirty=git_info["dirty"],
            command_line=" ".join(sys.argv),
            sites_selected=selected_sites,
            new_sites=new_sites,
            baseline_sites=baseline_sites,
            stages_requested=args.stages.split(","),
            env_flags={
                "tmdb_enabled": not args.no_tmdb,
                "replay_mode": args.replay is not None,
                "replay_source": args.replay,
            },
        )

        # Save initial metadata
        self._save_metadata(run_dir, metadata)

        # Initialize stage runner
        stage_runner = StageRunner(
            repo_root=self.repo_root,
            run_dir=run_dir,
            metadata=metadata,
            selected_sites=selected_sites,
            no_tmdb=args.no_tmdb,
            replay_dir=Path(args.replay) if args.replay else None,
        )

        # Run requested stages
        stages = args.stages.split(",")
        for stage in stages:
            print(f"\n{'='*60}")
            print(f"Stage: {stage}")
            print(f"{'='*60}")
            start_time = time.time()
            try:
                result = stage_runner.run_stage(stage)
                duration = time.time() - start_time
                metadata.stage_results[stage] = {
                    "status": "ok" if result.success else "failed",
                    "duration_seconds": round(duration, 2),
                    "details": result.details,
                    "error": result.error,
                }
                if not result.success:
                    print(f"Stage {stage} failed: {result.error}")
                    # Continue with other stages per requirements
            except Exception as e:
                duration = time.time() - start_time
                metadata.stage_results[stage] = {
                    "status": "error",
                    "duration_seconds": round(duration, 2),
                    "error": f"{type(e).__name__}: {e}",
                }
                print(f"Stage {stage} errored: {e}")

        # Compute artifact hashes
        metadata.artifact_hashes = self._compute_artifact_hashes(run_dir)

        # Save final metadata
        self._save_metadata(run_dir, metadata)

        # Generate report
        from dev_harness.report import generate_report
        generate_report(run_dir, "all")

        print(f"\nRun complete. Artifacts in: {run_dir}")
        return 0

    def rerun_stage(self, args: argparse.Namespace) -> int:
        """Re-run pipeline from a specific stage using saved artifacts."""
        run_dir = Path(args.run_dir)
        if not run_dir.exists():
            print(f"Run directory not found: {run_dir}")
            return 1

        # Load existing metadata
        metadata_path = run_dir / "manifest.json"
        with metadata_path.open() as f:
            metadata_dict = json.load(f)
        metadata = RunMetadata(**metadata_dict)

        # Determine stages to run
        all_stages = ["crawl", "generate", "enrich", "fix", "index", "reader"]
        start_idx = all_stages.index(args.from_stage)
        if args.stages:
            stages = args.stages.split(",")
        else:
            stages = all_stages[start_idx:]

        # Create new run directory for this re-run
        git_info = get_git_info(self.repo_root)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        short_sha = git_info["sha"][:8]
        new_run_id = f"{timestamp}_{short_sha}_rerun_from_{args.from_stage}"
        new_run_dir = self.runs_dir / new_run_id
        new_run_dir.mkdir(parents=True)

        # Copy artifacts from source run up to the starting stage
        self._copy_artifacts_up_to_stage(run_dir, new_run_dir, args.from_stage)

        # Update metadata for new run
        metadata.run_id = new_run_id
        metadata.timestamp = timestamp
        metadata.git_sha = git_info["sha"]
        metadata.git_dirty = git_info["dirty"]
        metadata.command_line = " ".join(sys.argv)
        metadata.stages_requested = stages
        metadata.env_flags["rerun_from"] = args.from_stage
        metadata.env_flags["source_run"] = str(run_dir)
        metadata.stage_results = {}

        self._save_metadata(new_run_dir, metadata)

        # Run stages
        stage_runner = StageRunner(
            repo_root=self.repo_root,
            run_dir=new_run_dir,
            metadata=metadata,
            selected_sites=metadata.sites_selected,
            no_tmdb=not metadata.env_flags.get("tmdb_enabled", True),
            replay_dir=Path(metadata.env_flags.get("replay_source")) if metadata.env_flags.get("replay_source") else None,
        )

        for stage in stages:
            print(f"\n{'='*60}")
            print(f"Re-run Stage: {stage}")
            print(f"{'='*60}")
            start_time = time.time()
            try:
                result = stage_runner.run_stage(stage)
                duration = time.time() - start_time
                metadata.stage_results[stage] = {
                    "status": "ok" if result.success else "failed",
                    "duration_seconds": round(duration, 2),
                    "details": result.details,
                    "error": result.error,
                }
                if not result.success:
                    print(f"Stage {stage} failed: {result.error}")
            except Exception as e:
                duration = time.time() - start_time
                metadata.stage_results[stage] = {
                    "status": "error",
                    "duration_seconds": round(duration, 2),
                    "error": f"{type(e).__name__}: {e}",
                }
                print(f"Stage {stage} errored: {e}")

        metadata.artifact_hashes = self._compute_artifact_hashes(new_run_dir)
        self._save_metadata(new_run_dir, metadata)

        from dev_harness.report import generate_report
        generate_report(new_run_dir, "all")

        print(f"\nRe-run complete. Artifacts in: {new_run_dir}")
        return 0

    def _resolve_sites(self, sites_config, args, run_dir) -> tuple[list[str], list[str], list[str]]:
        """Resolve which sites to run based on args."""
        if args.new_since:
            new_site_names, changed_site_names = find_new_sites(
                self.repo_root, args.new_since
            )
            new_sites = list(new_site_names | changed_site_names)
        elif args.new:
            new_sites = [s.strip() for s in args.new.split(",")]
        elif args.sites:
            new_sites = [s.strip() for s in args.sites.split(",")]
        elif args.all:
            new_sites = [site.name for site in sites_config.sites if site.enabled]
        else:
            new_sites = [site.name for site in sites_config.sites if site.enabled]

        # Resolve to feed_files
        matched, unmatched = resolve_feed_files(sites_config, new_sites)
        if unmatched:
            print(f"WARNING: Unmatched site requests: {unmatched}")

        # Get site names from matched feed_files
        site_names = []
        feed_files = set()
        for site in sites_config.sites:
            if site.feed_file in matched:
                site_names.append(site.name)
                feed_files.add(site.feed_file)

        # Select baseline sites for comparison
        baseline_sites = self._select_baseline_sites(sites_config, feed_files, args.baseline_sample)

        return site_names, list(new_sites), baseline_sites

    def _select_baseline_sites(self, sites_config, new_site_files: set[str], sample_per_new: int) -> list[str]:
        """Select baseline sites matching new sites' characteristics."""
        new_site_configs = [s for s in sites_config.sites if s.feed_file in new_site_files]
        if not new_site_configs:
            return []

        # Group enabled sites by (category, kind, method, language, enhance_mode)
        from collections import defaultdict
        groups = defaultdict(list)
        for site in sites_config.sites:
            if not site.enabled or site.feed_file in new_site_files:
                continue
            key = (
                site.category or "uncategorized",
                site.kind or "undeclared",
                site.method,
                site.language,
                site.enhance_mode or "default",
            )
            groups[key].append(site.feed_file)

        baseline = []
        for new_site in new_site_configs:
            key = (
                new_site.category or "uncategorized",
                new_site.kind or "undeclared",
                new_site.method,
                new_site.language,
                new_site.enhance_mode or "default",
            )
            candidates = groups.get(key, [])
            baseline.extend(candidates[:sample_per_new])

        return list(dict.fromkeys(baseline))  # deduplicate preserving order

    def _copy_artifacts_up_to_stage(self, src_dir: Path, dst_dir: Path, up_to_stage: str):
        """Copy stage artifacts from source run up to (but not including) the given stage."""
        stage_order = ["crawl", "generate", "enrich", "fix", "index", "reader"]
        copy_stages = stage_order[:stage_order.index(up_to_stage)]

        for stage in copy_stages:
            src_stage = src_dir / f"{stage_order.index(stage):02d}_{stage}"
            if src_stage.exists():
                dst_stage = dst_dir / f"{stage_order.index(stage):02d}_{stage}"
                shutil.copytree(src_stage, dst_stage)

        # Also copy config and logs
        for name in ["00_config", "logs"]:
            src = src_dir / name
            if src.exists():
                shutil.copytree(src, dst_dir / name)

    def _save_metadata(self, run_dir: Path, metadata: RunMetadata):
        """Save run metadata to manifest.json."""
        manifest_path = run_dir / "manifest.json"
        with manifest_path.open("w") as f:
            json.dump(asdict(metadata), f, indent=2, default=str)

    def _compute_artifact_hashes(self, run_dir: Path) -> dict[str, str]:
        """Compute SHA256 hashes of all artifacts in the run directory."""
        hashes = {}
        for file_path in run_dir.rglob("*"):
            if file_path.is_file() and file_path.name != "manifest.json":
                rel_path = file_path.relative_to(run_dir)
                with file_path.open("rb") as f:
                    hashes[str(rel_path)] = hashlib.sha256(f.read()).hexdigest()
        return hashes