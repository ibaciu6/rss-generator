#!/bin/bash

# Script to fetch GitHub Actions job log using API (requires GITHUB_TOKEN)

RUN_ID="23103639690"
REPO="ibaciu6/rss-generator"
TOKEN="${GITHUB_TOKEN:-}"  # Set your token here or as env var

if [ -z "$TOKEN" ]; then
  echo "Set GITHUB_TOKEN environment variable or edit script."
  exit 1
fi

# Get jobs for the run
JOBS=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs/$RUN_ID/jobs")

# Extract job ID (assuming first job)
JOB_ID=$(echo "$JOBS" | jq -r '.jobs[0].id')

if [ -z "$JOB_ID" ] || [ "$JOB_ID" = "null" ]; then
  echo "Failed to get job ID"
  exit 1
fi

# Get logs
curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/jobs/$JOB_ID/logs" > job_logs.txt

echo "Logs saved to job_logs.txt"
echo "Search for 'sitefilme' errors in the file."
