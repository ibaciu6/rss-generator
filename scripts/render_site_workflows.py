#!/usr/bin/env python3
"""
Emit ``.github/workflows/site-<slug>.yml`` for each site YAML under ``config/sites/``
(flat ``*.yaml`` or ``movies/*.yaml`` and ``series/*.yaml``).

Each site file should define ``schedule`` (cron string). Missing schedule defaults to ``17 */4 * * *``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.config import discover_site_yaml_files, load_config  # noqa: E402

# Placeholders avoid str.format / f-string clashes with GitHub ``${{ }}`` and bash ``${#array}``.
_GH_SECRET_PROXY = "${{ secrets.RSS_GENERATOR_PROXY_URL }}"
_GH_RUN_ID = "${{ github.run_id }}"


def build_workflow(
    site_name: str,
    display_title: str,
    cron: str,
    feed_file: str,
    config_yaml_posix: str,
) -> str:
    body = dedent(
        """
        name: Site feed — __DISPLAY_TITLE__

        on:
          schedule:
            - cron: '__CRON__'
          workflow_dispatch:
          push:
            branches: [main]
            paths:
              - '__CONFIG_YAML__'
              - 'core/**'
              - 'scraper/**'
              - 'scripts/**'
              - 'requirements.txt'
              - 'pyproject.toml'

        concurrency:
          group: site-feed-__SITE__
          cancel-in-progress: true

        permissions:
          contents: write

        env:
          FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"

        jobs:
          generate:
            runs-on: ubuntu-latest
            timeout-minutes: 35
            steps:
              - uses: actions/checkout@v6
              - uses: actions/setup-python@v6
                with:
                  python-version: '3.11'
              - name: Install dependencies
                run: |
                  python -m pip install --upgrade pip
                  pip install -r requirements.txt
                  python -m playwright install chromium
              - name: Random startup delay
                if: github.event_name == 'schedule'
                run: |
                  DELAY=$((RANDOM % 45))
                  echo "Waiting $DELAY s before scrape..."
                  sleep $DELAY
              - name: Generate feed
                id: generate_feed
                continue-on-error: true
                env:
                  RSS_GENERATOR_PROXY_URL: __GH_SECRET_PROXY__
                  RSS_FEED_PUBLIC_BASE: https://ibaciu6.github.io/rss-generator
                run: |
                  set -o pipefail
                  PYTHONPATH=. python scripts/generate_feeds.py --site __SITE__ 2>&1 | tee feed_generation.log
              - name: Upload generation log
                if: always()
                uses: actions/upload-artifact@v4
                with:
                  name: feed-__SITE__-__GH_RUN_ID__
                  path: feed_generation.log
                  if-no-files-found: warn
              - name: Commit feed if changed
                if: steps.generate_feed.outcome == 'success'
                run: |
                  git config user.name "github-actions[bot]"
                  git config user.email "github-actions[bot]@users.noreply.github.com"
                  git add "feeds/__FEED_FILE__"
                  if git diff --cached --quiet; then
                    echo "No feed changes for __SITE__."
                  else
                    git commit -m "Update feed __SITE__"
                    git fetch origin main
                    until git rebase origin/main; do
                      mapfile -t conflicts < <(git diff --name-only --diff-filter=U)
                      if [ "${#conflicts[@]}" -eq 0 ]; then
                        echo "Rebase stopped without listed conflicts"
                        git status
                        exit 1
                      fi
                      for f in "${conflicts[@]}"; do
                        case "$f" in
                          feeds/*)
                            git checkout --theirs -- "$f"
                            git add -- "$f"
                            ;;
                          *)
                            echo "Unexpected conflict in $f"
                            git status
                            exit 1
                            ;;
                        esac
                      done
                      GIT_EDITOR=true git rebase --continue || exit 1
                    done
                    git push origin HEAD:main
                  fi
              - name: Fail if feed step crashed
                if: always() && steps.generate_feed.outcome != 'success'
                run: |
                  echo "::error::Feed generation failed for __SITE__"
                  exit 1
        """
    )
    return (
        body.replace("__DISPLAY_TITLE__", display_title.replace(":", "—"))
        .replace("__CRON__", cron)
        .replace("__SITE__", site_name)
        .replace("__FEED_FILE__", feed_file)
        .replace("__CONFIG_YAML__", config_yaml_posix)
        .replace("__GH_SECRET_PROXY__", _GH_SECRET_PROXY)
        .replace("__GH_RUN_ID__", _GH_RUN_ID)
    )


def main() -> None:
    cfg_dir = REPO / "config" / "sites"
    out_dir = REPO / ".github" / "workflows"
    if not cfg_dir.is_dir():
        raise SystemExit(f"Missing config directory: {cfg_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(cfg_dir)
    for site in cfg.sites:
        cron = site.schedule_cron or "17 */4 * * *"
        title = site.display_name or site.name
        yaml_path = next(
            p for p, b in discover_site_yaml_files(cfg_dir) if p.stem == site.name
        )
        config_posix = yaml_path.relative_to(REPO).as_posix()
        body = build_workflow(site.name, title, cron, site.feed_file, config_posix)
        out = out_dir / f"site-{site.name}.yml"
        out.write_text(body.strip() + "\n", encoding="utf-8")
        print("Wrote", out.relative_to(REPO))


if __name__ == "__main__":
    main()
