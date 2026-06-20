#!/bin/bash

LOG_FILE="trigger_log.txt"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Starting workflow trigger and monitor at $(date)"

REPO="ibaciu6/rss-generator"
TOKEN=$(cat ~/.git-token)

# Step 1: Make a small change to trigger workflow
echo "Making dummy change to trigger workflow"
echo "# Dummy change to trigger workflow" >> dummy_trigger.txt
git add dummy_trigger.txt
git commit -m "Trigger workflow manually"
git push origin main
echo "Push complete, workflow should start"

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

# Step 5: Extract logs
echo "Workflow completed, extracting logs..."
JOBS=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs/$LATEST_RUN_ID/jobs")

JOB_ID=$(echo "$JOBS" | jq -r '.jobs[0].id')

if [ -n "$JOB_ID" ] && [ "$JOB_ID" != "null" ]; then
  curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/jobs/$JOB_ID/logs" > triggered_job_logs.txt
  echo "Logs saved to triggered_job_logs.txt"
else
  echo "No job ID found"
fi

echo "Trigger and monitor finished at $(date)"
