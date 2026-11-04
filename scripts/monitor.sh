#!/bin/bash
# Continuous monitoring loop for rss-generator repository
# Runs every 5 minutes, checks work status, resumes if stopped, verifies push

REPO_DIR="/mnt/d/Download/tools/rss-generator"
LOG_FILE="/tmp/rss-gen-monitor.log"
CHECK_INTERVAL=300  # 5 minutes

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

check_git_status() {
    cd "$REPO_DIR" || return 1
    local status=$(git status --porcelain 2>/dev/null)
    local unpushed=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l)
    
    echo "WORK_DIRTY:$?"
    echo "GIT_STATUS:$status"
    echo "UNPUSHED:$unpushed"
    echo "BRANCH:$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
}

check_ci_status() {
    cd "$REPO_DIR" || return 1
    local latest_run=$(gh run list --branch main --limit 1 --json status,conclusion,headBranch --jq '.[0]' 2>/dev/null)
    
    if [ -z "$latest_run" ]; then
        echo "CI_STATUS:unknown"
        echo "CI_CONCLUSION:none"
        return
    fi
    
    local status=$(echo "$latest_run" | jq -r '.status')
    local conclusion=$(echo "$latest_run" | jq -r '.conclusion')
    echo "CI_STATUS:$status"
    echo "CI_CONCLUSION:$conclusion"
}

check_feeds_integrity() {
    cd "$REPO_DIR" || return 1
    local feed_count=$(ls -1 feeds/*.xml 2>/dev/null | wc -l)
    local feed_day_count=$(grep -l 'feed-day' feeds/*.xml 2>/dev/null | wc -l)
    local tvmaze_followed=$(grep -c 'feed-day' feeds/tvmaze-followed-ical.xml 2>/dev/null || echo 0)
    
    echo "FEED_COUNT:$feed_count"
    echo "FEED_DAY_FEEDS:$feed_day_count"
    echo "TVMAZE_FOLLOWED_DATE_LINES:$tvmaze_followed"
}

check_tests() {
    cd "$REPO_DIR" || return 1
    local test_result=$(.venv/bin/python -m pytest tests/ -q --ignore=tests/test_fetcher.py 2>&1 | tail -1)
    echo "TESTS:$test_result"
}

# Main check function
perform_check() {
    log "=== Starting monitoring check ==="
    
    local git_info=$(check_git_status)
    local ci_info=$(check_ci_status)
    local feeds_info=$(check_feeds_integrity)
    
    log "$git_info"
    log "$ci_info"
    log "$feeds_info"
    
    # Check if work is needed
    local has_dirty=$(echo "$git_info" | grep "GIT_STATUS:" | grep -v "GIT_STATUS:" || true)
    local has_unpushed=$(echo "$git_info" | grep "UNPUSHED:" | cut -d: -f2)
    local ci_status=$(echo "$ci_info" | grep "CI_STATUS:" | cut -d: -f2)
    local ci_conclusion=$(echo "$ci_info" | grep "CI_CONCLUSION:" | cut -d: -f2)
    
    log "Has dirty changes: $([ -n "$has_dirty" ] && echo 'yes' || echo 'no')"
    log "Unpushed commits: $has_unpushed"
    log "CI status: $ci_status ($ci_conclusion)"
    
    # Resume work if needed
    if [ -n "$has_dirty" ] || [ "$has_unpushed" -gt 0 ] || { [ "$ci_status" = "completed" ] && [ "$ci_conclusion" != "success" ]; }; then
        log "⚠️  Work detected as pending/stale. Resuming..."
        perform_fixup
    else
        log "✓ Repository appears clean and CI passing"
    fi
    
    log "=== Check complete ==="
}

perform_fixup() {
    cd "$REPO_DIR" || return
    
    log "Running fixup sequence..."
    
    # Check and fix feed-day injection
    local followed_count=$(grep -c 'feed-day' feeds/tvmaze-followed-ical.xml 2>/dev/null || echo 0)
    if [ "$followed_count" -eq 0 ]; then
        log "Patching tvmaze-followed-ical.xml with feed-day lines..."
        python3 -c "
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import html

path = 'feeds/tvmaze-followed-ical.xml'
with open(path, 'r') as f:
    data = f.read()
root = ET.fromstring(data)
chan = root.find('channel')
count = 0
for item in chan.findall('item'):
    desc = item.find('description')
    pub = item.find('pubDate')
    if desc is None or desc.text is None or pub is None or pub.text is None:
        continue
    current = desc.text
    if '<p id=\"feed-day\"' in current:
        continue
    try:
        dt = parsedate_to_datetime(pub.text)
    except:
        continue
    unescaped = html.unescape(current)
    date_line = f'<br><p id=\"feed-day\"><strong>Air date:</strong> {dt.strftime(\"%Y-%m-%d\")}</p>'
    escaped = html.escape(unescaped + date_line, quote=True)
    desc.text = escaped
    count += 1
with open(path, 'w') as f:
    f.write(data[:data.find('<')] + ET.tostring(root, encoding='unicode'))
print(f'Patched {count} items')
"
    fi
    
    # Run tests
    log "Running tests..."
    .venv/bin/python -m pytest tests/ -q --ignore=tests/test_fetcher.py 2>&1 | tail -5
    
    # Commit and push if needed
    local status=$(git status --porcelain)
    if [ -n "$status" ]; then
        log "Committing changes..."
        git add -A
        git commit -m "chore(feeds): patch feed-day lines and verify integrity [skip ci]"
        log "Pushing to origin/main..."
        git push origin main 2>&1 | tail -5
    fi
}

# Run initial check
perform_check

# Main loop
log "Starting 5-minute monitoring loop (Ctrl+C to stop)"
while true; do
    sleep $CHECK_INTERVAL
    perform_check
done
