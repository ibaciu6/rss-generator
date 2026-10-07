#!/usr/bin/env python3
"""
Stage runner - executes each pipeline stage in isolation with artifact retention.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ruff: noqa: E402
from core.config import load_config
from dev_harness.crawl_recorder import CrawlRecorder, set_recorder


@dataclass
class StageResult:
    success: bool
    details: dict = None
    error: str = ""


class StageRunner:
    """Runs individual pipeline stages with isolated working directories."""

    def __init__(
        self,
        repo_root: Path,
        run_dir: Path,
        metadata: Any,
        selected_sites: list[str],
        no_tmdb: bool = False,
        replay_dir: Path | None = None,
    ):
        self.repo_root = repo_root
        self.run_dir = run_dir
        self.metadata = metadata
        self.selected_sites = selected_sites
        self.no_tmdb = no_tmdb
        self.replay_dir = replay_dir

        # Stage directories
        self.stage_dirs = {}
        stage_names = ["crawl", "generate", "enrich", "fix", "index", "reader"]
        for i, name in enumerate(stage_names):
            stage_dir = run_dir / f"{i:02d}_{name}"
            stage_dir.mkdir(parents=True, exist_ok=True)
            self.stage_dirs[name] = stage_dir

        # Feeds directory for this run (isolated)
        self.feeds_dir = run_dir / "feeds_working"
        self.feeds_dir.mkdir(exist_ok=True)

        # Cache directory for this run
        self.cache_dir = run_dir / "cache"
        self.cache_dir.mkdir(exist_ok=True)

        # Logs directory
        self.logs_dir = run_dir / "logs"
        self.logs_dir.mkdir(exist_ok=True)

        # Crawl recorder
        self.recorder = None
        if replay_dir:
            self.recorder = CrawlRecorder(run_dir, mode="replay")
        else:
            self.recorder = CrawlRecorder(run_dir, mode="record")
        set_recorder(self.recorder)

        # Copy config
        self._copy_config()

    def _copy_config(self):
        """Copy config to run directory."""
        config_src = self.repo_root / "config" / "sites.yaml"
        config_dst = self.run_dir / "00_config" / "sites.yaml"
        config_dst.parent.mkdir(exist_ok=True)
        shutil.copy2(config_src, config_dst)

    def run_stage(self, stage: str) -> StageResult:
        """Run a specific pipeline stage."""
        stage_method = getattr(self, f"_run_{stage}", None)
        if not stage_method:
            return StageResult(success=False, error=f"Unknown stage: {stage}")
        return stage_method()

    def _run_crawl(self) -> StageResult:
        """Stage 0: Crawl - fetch raw HTML/pages for all sites."""
        # This is essentially the same as generate but we save raw responses
        # We'll run generate_feeds.py with instrumentation to save raw HTML
        details = {"sites": {}}

        for site_name in self.selected_sites:
            site_log = self.logs_dir / f"crawl_{site_name}.log"
            try:
                # Run a custom crawl that saves raw responses
                result = self._crawl_site(site_name, site_log)
                details["sites"][site_name] = result
            except Exception as e:
                details["sites"][site_name] = {"error": str(e)}

        # Finalize recorder
        self.recorder.finalize()

        return StageResult(success=True, details=details)

    def _crawl_site(self, site_name: str, log_file: Path) -> dict:
        """Crawl a single site and save raw responses."""
        # Load site config
        config = load_config(self.repo_root / "config" / "sites.yaml")
        site = next((s for s in config.sites if s.name == site_name), None)
        if not site:
            return {"error": "Site not found"}

        # Use the engine directly to crawl
        from core.engine import GenerationEngine
        from scraper.fetcher import Fetcher

        async def do_crawl():
            cache_file = self.cache_dir / "cache.json"
            engine = GenerationEngine(config, cache_file, self.feeds_dir, [site_name])
            fetcher = Fetcher()

            # We need to intercept the fetcher to save raw responses
            # For now, run the normal generation but with our recorder active
            try:
                await engine.run()
                return {"status": "ok", "items": "unknown"}
            except Exception as e:
                return {"status": "failed", "error": str(e)}
            finally:
                await fetcher.close()

        return asyncio.run(do_crawl())

    def _run_generate(self) -> StageResult:
        """Stage 1: Generate feeds from crawled data."""
        details = {"feeds_generated": 0, "feeds_failed": 0}

        # Prepare environment
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)
        if self.no_tmdb:
            env["TMDB_API_KEY"] = ""  # Will cause streaming enrichment to be skipped

        # Build command
        cmd = [
            sys.executable, "-m", "core.cli", "generate",
            "--config", str(self.repo_root / "config" / "sites.yaml"),
            "--cache", str(self.cache_dir / "cache.json"),
            "--feeds-dir", str(self.feeds_dir),
        ]

        # Add site filter if not all sites
        if self.selected_sites:
            for site in self.selected_sites:
                cmd.extend(["--site", site])

        # Run with logging
        log_file = self.logs_dir / "generate.log"
        with log_file.open("w") as log:
            proc = subprocess.run(
                cmd,
                cwd=self.repo_root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=3600,
            )

        # Count generated feeds
        feed_files = list(self.feeds_dir.glob("*.xml"))
        details["feeds_generated"] = len(feed_files)
        details["exit_code"] = proc.returncode

        # Copy feeds to stage directory
        stage_feeds = self.stage_dirs["generate"]
        for f in feed_files:
            shutil.copy2(f, stage_feeds / f.name)

        return StageResult(
            success=proc.returncode == 0,
            details=details,
            error="" if proc.returncode == 0 else f"Exit code {proc.returncode}"
        )

    def _run_enrich(self) -> StageResult:
        """Stage 2: Enrich feeds with TMDb/article content."""
        details = {"feeds_enriched": 0, "feeds_skipped": 0}

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)
        if self.no_tmdb:
            env["TMDB_API_KEY"] = ""

        cmd = [
            sys.executable, "-m", "scripts.enrich_feeds",
        ]

        if self.selected_sites:
            for site in self.selected_sites:
                cmd.extend(["--site", site])

        log_file = self.logs_dir / "enrich.log"
        with log_file.open("w") as log:
            proc = subprocess.run(
                cmd,
                cwd=self.repo_root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=3600,
            )

        # Copy enriched feeds to stage directory
        stage_feeds = self.stage_dirs["enrich"]
        for f in self.feeds_dir.glob("*.xml"):
            shutil.copy2(f, stage_feeds / f.name)

        details["exit_code"] = proc.returncode
        return StageResult(
            success=proc.returncode == 0,
            details=details,
            error="" if proc.returncode == 0 else f"Exit code {proc.returncode}"
        )

    def _run_fix(self) -> StageResult:
        """Stage 3: Fix feeds (post-processing)."""
        details = {}

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)

        cmd = [
            sys.executable, "-m", "scripts.fix_feeds",
        ]

        if self.selected_sites:
            for site in self.selected_sites:
                cmd.extend(["--site", site])

        log_file = self.logs_dir / "fix.log"
        with log_file.open("w") as log:
            proc = subprocess.run(
                cmd,
                cwd=self.repo_root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1800,
            )

        # Copy fixed feeds to stage directory
        stage_feeds = self.stage_dirs["fix"]
        for f in self.feeds_dir.glob("*.xml"):
            shutil.copy2(f, stage_feeds / f.name)

        details["exit_code"] = proc.returncode
        return StageResult(
            success=proc.returncode == 0,
            details=details,
            error="" if proc.returncode == 0 else f"Exit code {proc.returncode}"
        )

    def _run_index(self) -> StageResult:
        """Stage 4: Generate index.html and feeds.opml."""
        details = {}

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)

        # We need to call generate_index.py with our feeds directory
        # The script uses hardcoded paths, so we'll import and call directly
        log_file = self.logs_dir / "index.log"
        with log_file.open("w") as log:
            try:
                # Import and run
                sys.path.insert(0, str(self.repo_root / "scripts"))
                from generate_index import generate_index

                generate_index(
                    config_path=self.repo_root / "config" / "sites.yaml",
                    feeds_dir=self.feeds_dir,
                    output_file=self.stage_dirs["index"] / "index.html",
                    output_opml=self.stage_dirs["index"] / "feeds.opml",
                )
                # Also copy to run root for convenience
                shutil.copy2(
                    self.stage_dirs["index"] / "index.html",
                    self.run_dir / "index.html"
                )
                shutil.copy2(
                    self.stage_dirs["index"] / "feeds.opml",
                    self.run_dir / "feeds.opml"
                )
                details["status"] = "ok"
                return StageResult(success=True, details=details)
            except Exception as e:
                log.write(f"ERROR: {e}\n")
                details["error"] = str(e)
                return StageResult(success=False, details=details, error=str(e))

    def _run_reader(self) -> StageResult:
        """Stage 5: Generate reader.html and feeds/manifest.json."""
        details = {}

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)

        log_file = self.logs_dir / "reader.log"
        with log_file.open("w") as log:
            try:
                sys.path.insert(0, str(self.repo_root / "scripts"))

                # generate_reader uses hardcoded paths, we need to work around
                # For now, run as subprocess
                cmd = [
                    sys.executable, "-m", "scripts.generate_reader",
                ]
                proc = subprocess.run(
                    cmd,
                    cwd=self.repo_root,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                )

                # Copy outputs
                if (self.repo_root / "reader.html").exists():
                    shutil.copy2(
                        self.repo_root / "reader.html",
                        self.stage_dirs["reader"] / "reader.html"
                    )
                if (self.repo_root / "feeds" / "manifest.json").exists():
                    shutil.copy2(
                        self.repo_root / "feeds" / "manifest.json",
                        self.stage_dirs["reader"] / "manifest.json"
                    )

                details["exit_code"] = proc.returncode
                return StageResult(
                    success=proc.returncode == 0,
                    details=details,
                    error="" if proc.returncode == 0 else f"Exit code {proc.returncode}"
                )
            except Exception as e:
                log.write(f"ERROR: {e}\n")
                details["error"] = str(e)
                return StageResult(success=False, details=details, error=str(e))

    def get_feeds_dir(self) -> Path:
        """Get the working feeds directory for this run."""
        return self.feeds_dir

    def get_stage_dir(self, stage: str) -> Path:
        """Get the artifact directory for a stage."""
        return self.stage_dirs.get(stage, self.run_dir / stage)