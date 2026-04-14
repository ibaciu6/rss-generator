#!/bin/bash

# Navigate to the repository directory
cd /mnt/c/work/rss-generator

# Set remote to HTTPS
git remote set-url origin https://github.com/ibaciu6/rss-generator.git

# Pull latest changes
git pull origin main

# Add the config file
git add config/sites/

# Commit with message
git commit -m "Fix sitefilme selectors for cloudscraper + proxy"

# Push to main
git push origin main

echo "Commit and push completed."
