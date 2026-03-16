#!/bin/bash

LOG_FILE="monitor_log.txt"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Starting GitHub Actions monitor at $(date)"

REPO="ibaciu6/rss-generator"
TOKEN=$(cat ~/.git-token)

# Get latest workflow runs
echo "Fetching latest workflow runs..."
RUNS=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs")

if [ $? -ne 0 ]; then
  echo "Error fetching runs"
  exit 1
fi

# Extract latest run ID and status
LATEST_RUN_ID=$(echo "$RUNS" | jq -r '.workflow_runs[0].id')
LATEST_STATUS=$(echo "$RUNS" | jq -r '.workflow_runs[0].status')
LATEST_CREATED=$(echo "$RUNS" | jq -r '.workflow_runs[0].created_at')

echo "Latest run ID: $LATEST_RUN_ID, Status: $LATEST_STATUS, Created: $LATEST_CREATED"

if [ "$LATEST_STATUS" = "completed" ]; then
  echo "Run completed, fetching jobs..."
  JOBS=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs/$LATEST_RUN_ID/jobs")

  JOB_ID=$(echo "$JOBS" | jq -r '.jobs[0].id')
  echo "Job ID: $JOB_ID"

  if [ -n "$JOB_ID" ] && [ "$JOB_ID" != "null" ]; then
    echo "Fetching logs..."
    curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/jobs/$JOB_ID/logs" > latest_job_logs.txt
    echo "Logs saved to latest_job_logs.txt"
  else
    echo "No job ID found"
  fi
else
  echo "Run not completed yet"
fi

echo "Monitor finished at $(date)"
