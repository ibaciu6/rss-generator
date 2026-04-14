#!/bin/bash

LOG_FILE="trigger_monitor_log.txt"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Starting workflow_dispatch trigger and monitor at $(date)"

REPO="ibaciu6/rss-generator"
TOKEN=$(cat ~/.git-token)

# Step 1: Trigger workflow_dispatch for RSS update
echo "Triggering workflow_dispatch for RSS update..."
RESPONSE=$(curl -s -X POST \
  -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/$REPO/actions/workflows/site-filmehd-filme.yml/dispatches" \
  -d '{"ref":"main"}')

if [ $? -eq 0 ]; then
  echo "Workflow dispatched successfully."
else
  echo "Failed to dispatch workflow."
  exit 1
fi

# Step 2: Wait a bit for workflow to start
echo "Waiting 30 seconds for workflow to start..."
sleep 30

# Step 3: Get latest workflow runs
echo "Fetching latest workflow runs..."
RUNS=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs")

LATEST_RUN_ID=$(echo "$RUNS" | jq -r '.workflow_runs[0].id')
LATEST_STATUS=$(echo "$RUNS" | jq -r '.workflow_runs[0].status')

echo "Latest run ID: $LATEST_RUN_ID, Status: $LATEST_STATUS"

# Step 4: Wait for completion if not completed
if [ "$LATEST_STATUS" != "completed" ]; then
  echo "Waiting for workflow to complete..."
  for i in {1..60}; do  # Wait up to 10 minutes
    sleep 10
    STATUS=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs/$LATEST_RUN_ID" | jq -r '.status')
    echo "Status check $i: $STATUS"
    if [ "$STATUS" = "completed" ]; then
      break
    fi
  done
fi

# Step 5: Check conclusion
CONCLUSION=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs/$LATEST_RUN_ID" | jq -r '.conclusion')
echo "Workflow conclusion: $CONCLUSION"

if [ "$CONCLUSION" = "success" ]; then
  echo "Workflow succeeded. Checking if feeds were created..."

  # Check GitHub Pages for feeds
  FEEDS_CONTENT=$(curl -s "https://ibaciu6.github.io/rss-generator/feeds/")
  if echo "$FEEDS_CONTENT" | grep -q "veziseriale.xml"; then
    echo "Feeds were created successfully."
  else
    echo "Feeds were not created. Need to investigate further."
  fi
else
  echo "Workflow failed. Extracting logs..."
  JOBS=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs/$LATEST_RUN_ID/jobs")
  JOB_ID=$(echo "$JOBS" | jq -r '.jobs[0].id')

  if [ -n "$JOB_ID" ] && [ "$JOB_ID" != "null" ]; then
    curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/jobs/$JOB_ID/logs" > failed_job_logs.txt
    echo "Logs saved to failed_job_logs.txt"
    echo "Please check failed_job_logs.txt for errors."
  else
    echo "No job ID found"
  fi
fi

echo "Trigger and monitor finished at $(date)"
