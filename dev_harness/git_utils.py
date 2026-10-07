#!/usr/bin/env python3
"""
Git utilities for the dev harness.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GitInfo:
    sha: str
    dirty: bool
    branch: str


def run_git(repo_root: Path, *args: str) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def get_git_info(repo_root: Path) -> dict[str, Any]:
    """Get current git commit info."""
    sha = run_git(repo_root, "rev-parse", "HEAD")
    # Check for uncommitted changes
    status = run_git(repo_root, "status", "--porcelain")
    dirty = bool(status.strip())
    branch = run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    return {"sha": sha, "dirty": dirty, "branch": branch}


def diff_sites_yaml(repo_root: Path, ref: str) -> str:
    """Get diff of config/sites.yaml between ref and working tree."""
    try:
        result = subprocess.run(
            ["git", "diff", ref, "--", "config/sites.yaml"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout
    except Exception:
        return ""


def find_new_sites(repo_root: Path, ref: str) -> tuple[set[str], set[str]]:
    """
    Find sites that are new or changed since a git ref.
    Returns (new_sites, changed_sites) as sets of site names.
    """
    # Get sites.yaml at ref
    try:
        ref_content = subprocess.run(
            ["git", "show", f"{ref}:config/sites.yaml"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except Exception:
        ref_content = ""

    # Get current sites.yaml
    current_content = (repo_root / "config" / "sites.yaml").read_text()

    # Parse both to find site names
    import yaml
    try:
        ref_sites = set(yaml.safe_load(ref_content).get("sites", {}).keys()) if ref_content else set()
    except Exception:
        ref_sites = set()

    try:
        current_sites = set(yaml.safe_load(current_content).get("sites", {}).keys())
    except Exception:
        current_sites = set()

    new_sites = current_sites - ref_sites
    # For changed sites, we'd need to compare the actual config values
    # For now, just return sites that exist in both but might have changed
    common_sites = current_sites & ref_sites
    changed_sites = set()

    # Quick check: if the YAML block for a site differs, consider it changed
    for site in common_sites:
        # Extract the site block from both versions
        import re
        pattern = rf"^\s+{re.escape(site)}:\n(?:^\s.*\n)*"
        ref_block = re.search(pattern, ref_content, re.MULTILINE)
        cur_block = re.search(pattern, current_content, re.MULTILINE)
        if (ref_block and cur_block and ref_block.group(0) != cur_block.group(0)) or (not ref_block and cur_block):
            changed_sites.add(site)

    return new_sites, changed_sites