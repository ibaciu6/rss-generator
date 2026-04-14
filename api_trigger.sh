#!/bin/bash

LOG_FILE="api_trigger_log.txt"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Triggering workflow via API at $(date)"

REPO="ibaciu6/rss-generator"
TOKEN=$(cat ~/.git-token)

# Trigger workflow dispatch
curl -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/$REPO/actions/workflows/site-filmehd-filme.yml/dispatches \
  -d '{"ref":"main"}'

echo "Trigger request sent"

# Wait and check status
sleep 10
echo "Checking for new runs..."
RUNS=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs")

LATEST_RUN_ID=$(echo "$RUNS" | jq -r '.workflow_runs[0].id')
LATEST_STATUS=$(echo "$RUNS" | jq -r '.workflow_runs[0].status')

echo "Latest run ID: $LATEST_RUN_ID, Status: $LATEST_STATUS"

echo "API trigger finished at $(date)"
