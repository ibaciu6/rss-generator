#!/usr/bin/env bash
# Used by GitHub Actions per-site workflows: commit a single feed file with rebase + push retries.
set -euo pipefail

FEED_FILE="${1:?feed file under feeds/}"
SITE_ID="${2:?site id for commit message}"

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git add "feeds/${FEED_FILE}"
if git diff --cached --quiet; then
  echo "No feed changes for ${SITE_ID}."
  exit 0
fi

git commit -m "Update feed ${SITE_ID}"

resolve_rebase() {
  until git rebase origin/main; do
    mapfile -t conflicts < <(git diff --name-only --diff-filter=U)
    if [ "${#conflicts[@]}" -eq 0 ]; then
      echo "Rebase stopped without listed conflicts"
      git status
      return 1
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
          return 1
          ;;
      esac
    done
    GIT_EDITOR=true git rebase --continue || return 1
  done
  return 0
}

git fetch origin main
resolve_rebase

for attempt in $(seq 1 30); do
  if git push origin HEAD:main; then
    exit 0
  fi
  echo "Push failed (attempt ${attempt}), sleeping before retry..."
  sleep $((12 + RANDOM % 40))
  git fetch origin main || exit 1
  resolve_rebase || exit 1
done

echo "::error::Could not push feed for ${SITE_ID} after retries"
exit 1
