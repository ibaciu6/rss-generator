# Security Policy

## Supported Versions

The `main` branch is the supported version of this project.

## Reporting a Vulnerability

Do not report suspected secrets, tokens, or vulnerabilities in a public issue.

Preferred reporting paths:

1. Use GitHub's private vulnerability reporting for this repository, if enabled.
2. If private reporting is not available, contact the repository maintainer privately before disclosing details publicly.

Include:

- a short description of the issue
- affected files, workflow names, or commands
- reproduction steps
- potential impact

## Secret Handling

- Store secrets only in GitHub Actions repository secrets or another secret manager.
- Never commit `.env` files, tokens, API keys, or private keys.
- This repository includes automated secret scanning in CI to catch accidental disclosures on pushes and pull requests.
