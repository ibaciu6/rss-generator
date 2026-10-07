#!/usr/bin/env python3
"""
Report generation for dev harness runs.
Produces markdown, HTML, and JSON reports from run artifacts.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ruff: noqa: E402
from dev_harness.compare import get_stage_dir


def generate_report(run_dir: Path, format: str = "all") -> int:
    """Generate report for a run directory."""
    print(f"Generating report for {run_dir.name}")

    # Load manifest
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"ERROR: No manifest.json in {run_dir}")
        return 1

    with manifest_path.open() as f:
        manifest = json.load(f)

    # Load stage results
    stages = ["generate", "enrich", "fix", "index", "reader"]
    stage_data = {}
    for stage in stages:
        stage_dir = get_stage_dir(run_dir, stage)
        if stage_dir.exists():
            stage_data[stage] = _load_stage_artifacts(stage_dir)

    # Generate reports
    if format in ("md", "all"):
        _generate_markdown_report(run_dir, manifest, stage_data)
    if format in ("html", "all"):
        _generate_html_report(run_dir, manifest, stage_data)
    if format in ("json", "all"):
        _generate_json_report(run_dir, manifest, stage_data)

    print(f"Report generated in {run_dir}/report/")
    return 0


def _load_stage_artifacts(stage_dir: Path) -> dict:
    """Load artifacts from a stage directory."""
    artifacts = {}
    for f in stage_dir.glob("*.xml"):
        artifacts[f.name] = f
    for f in stage_dir.glob("*.json"):
        artifacts[f.name] = f
    for f in stage_dir.glob("*.html"):
        artifacts[f.name] = f
    return artifacts


def _generate_markdown_report(run_dir: Path, manifest: dict, stage_data: dict):
    """Generate markdown report."""
    report_dir = run_dir / "report"
    report_dir.mkdir(exist_ok=True)

    lines = [
        f"# Dev Harness Run Report: {manifest['run_id']}",
        "",
        f"**Timestamp:** {manifest['timestamp']}",
        f"**Git SHA:** {manifest['git_sha']} {'(dirty)' if manifest['git_dirty'] else ''}",
        f"**Command:** `{manifest['command_line']}`",
        f"**TMDb Enabled:** {manifest['env_flags'].get('tmdb_enabled', 'unknown')}",
        f"**Replay Mode:** {manifest['env_flags'].get('replay_mode', 'unknown')}",
        "",
        "## Sites Selected",
        "",
    ]

    for site in manifest['sites_selected']:
        is_new = " 🆕" if site in manifest.get('new_sites', []) else ""
        is_baseline = " 📊" if site in manifest.get('baseline_sites', []) else ""
        lines.append(f"- {site}{is_new}{is_baseline}")

    lines.extend([
        "",
        "## Stage Results",
        "",
        "| Stage | Status | Duration | Details |",
        "|-------|--------|----------|---------|",
    ])

    for stage, result in manifest.get('stage_results', {}).items():
        status_emoji = {"ok": "✅", "failed": "❌", "error": "💥", "skipped": "⏭️"}.get(result['status'], "❓")
        duration = f"{result.get('duration_seconds', 0)}s"
        details = str(result.get('details', {}))[:100]
        lines.append(f"| {stage} | {status_emoji} {result['status']} | {duration} | {details} |")

    lines.extend([
        "",
        "## Feed Artifacts",
        "",
    ])

    # List feeds per stage
    for stage, artifacts in stage_data.items():
        xml_feeds = [a for a in artifacts if a.endswith('.xml')]
        if xml_feeds:
            lines.append(f"### {stage} ({len(xml_feeds)} feeds)")
            lines.append("")
            for feed in sorted(xml_feeds):
                lines.append(f"- `{feed}`")
            lines.append("")

    # Per-site feed quality (from fix stage)
    if "fix" in stage_data:
        lines.append("## Feed Quality (post-fix)")
        lines.append("")
        lines.append("| Feed | Items | Status |")
        lines.append("|------|-------|--------|")
        # This would need feed parsing - simplified for now

    lines.extend([
        "",
        "## Artifact Hashes",
        "",
        "```json",
        json.dumps(manifest.get('artifact_hashes', {}), indent=2),
        "```",
    ])

    (report_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _generate_html_report(run_dir: Path, manifest: dict, stage_data: dict):
    """Generate self-contained HTML report."""
    report_dir = run_dir / "report"
    report_dir.mkdir(exist_ok=True)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dev Harness Report - {manifest['run_id']}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; border-bottom: 2px solid #007bff; padding-bottom: 10px; }}
        h2 {{ color: #555; margin-top: 30px; }}
        h3 {{ color: #666; }}
        table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #f8f9fa; font-weight: 600; }}
        tr:hover {{ background: #f8f9fa; }}
        .status-ok {{ color: #28a745; font-weight: bold; }}
        .status-failed {{ color: #dc3545; font-weight: bold; }}
        .status-error {{ color: #fd7e14; font-weight: bold; }}
        .status-skipped {{ color: #6c757d; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; font-weight: 600; }}
        .badge-new {{ background: #e7f3ff; color: #007bff; }}
        .badge-baseline {{ background: #fff3cd; color: #856404; }}
        .stage-card {{ border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin: 15px 0; }}
        .stage-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
        .duration {{ color: #666; font-family: monospace; }}
        .feed-list {{ columns: 2; column-gap: 40px; }}
        .feed-list li {{ margin: 5px 0; font-family: monospace; }}
        pre {{ background: #f8f9fa; padding: 15px; border-radius: 4px; overflow-x: auto; }}
        code {{ background: #f8f9fa; padding: 2px 6px; border-radius: 3px; }}
        .section {{ margin: 30px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔬 Dev Harness Run Report</h1>
        <p><strong>Run ID:</strong> {manifest['run_id']}</p>
        <p><strong>Timestamp:</strong> {manifest['timestamp']}</p>
        <p><strong>Git SHA:</strong> {manifest['git_sha'][:8]} {'🔴 dirty' if manifest['git_dirty'] else '🟢 clean'}</p>
        <p><strong>Command:</strong> <code>{manifest['command_line']}</code></p>
        <p><strong>TMDb:</strong> {'Enabled' if manifest['env_flags'].get('tmdb_enabled') else 'Disabled'} | <strong>Replay:</strong> {'Yes' if manifest['env_flags'].get('replay_mode') else 'No'}</p>

        <div class="section">
            <h2>📋 Sites Selected ({len(manifest['sites_selected'])})</h2>
            <ul class="feed-list">
"""

    for site in manifest['sites_selected']:
        badges = []
        if site in manifest.get('new_sites', []):
            badges.append('<span class="badge badge-new">NEW</span>')
        if site in manifest.get('baseline_sites', []):
            badges.append('<span class="badge badge-baseline">BASELINE</span>')
        badge_html = " ".join(badges)
        html += f"                <li>{site} {badge_html}</li>\n"

    html += """            </ul>
        </div>

        <div class="section">
            <h2>⚙️ Stage Results</h2>
            <table>
                <thead>
                    <tr><th>Stage</th><th>Status</th><th>Duration</th><th>Details</th></tr>
                </thead>
                <tbody>
"""

    for stage, result in manifest.get('stage_results', {}).items():
        status_class = f"status-{result['status']}"
        status_emoji = {"ok": "✅", "failed": "❌", "error": "💥", "skipped": "⏭️"}.get(result['status'], "❓")
        duration = f"{result.get('duration_seconds', 0)}s"
        details = str(result.get('details', {}))[:200]
        html += f"""                    <tr>
                        <td><strong>{stage}</strong></td>
                        <td class="{status_class}">{status_emoji} {result['status']}</td>
                        <td class="duration">{duration}</td>
                        <td><code>{details}</code></td>
                    </tr>
"""

    html += """                </tbody>
            </table>
        </div>

        <div class="section">
            <h2>📦 Feed Artifacts by Stage</h2>
"""

    for stage, artifacts in stage_data.items():
        xml_feeds = sorted([a for a in artifacts if a.endswith('.xml')])
        if xml_feeds:
            html += f"""            <div class="stage-card">
                <div class="stage-header">
                    <h3>{stage} ({len(xml_feeds)} feeds)</h3>
                    <span class="duration">{len(xml_feeds)} feeds</span>
                </div>
                <ul class="feed-list">
"""
            for feed in xml_feeds:
                html += f"                    <li>{feed}</li>\n"
            html += """                </ul>
            </div>
"""

    html += f"""        </div>

        <div class="section">
            <h2>🔐 Artifact Hashes</h2>
            <pre><code>{json.dumps(manifest.get('artifact_hashes', {}), indent=2)}</code></pre>
        </div>

        <hr>
        <p style="color: #999; font-size: 0.9em;">Generated by dev-harness at {datetime.now(UTC).isoformat()}</p>
    </div>
</body>
</html>"""

    (report_dir / "report.html").write_text(html, encoding="utf-8")


def _generate_json_report(run_dir: Path, manifest: dict, stage_data: dict):
    """Generate machine-readable JSON report."""
    report_dir = run_dir / "report"
    report_dir.mkdir(exist_ok=True)

    # Convert stage_data to serializable format
    serializable_stages = {}
    for stage, artifacts in stage_data.items():
        serializable_stages[stage] = list(artifacts.keys())

    report = {
        "run_id": manifest['run_id'],
        "timestamp": manifest['timestamp'],
        "git_sha": manifest['git_sha'],
        "git_dirty": manifest['git_dirty'],
        "command_line": manifest['command_line'],
        "sites_selected": manifest['sites_selected'],
        "new_sites": manifest.get('new_sites', []),
        "baseline_sites": manifest.get('baseline_sites', []),
        "env_flags": manifest['env_flags'],
        "stage_results": manifest.get('stage_results', {}),
        "stage_artifacts": serializable_stages,
        "artifact_hashes": manifest.get('artifact_hashes', {}),
    }

    (report_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")