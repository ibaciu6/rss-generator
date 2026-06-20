## Development Guide

### Requirements

- Python 3.11+
- Playwright (Chromium browsers)

Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

### Running locally

```bash
python -m core.cli generate
# or
python scripts/generate_feeds.py
```

Feeds are written to `feeds/`.

### Running tests

```bash
pytest
```

### Docker

Build and run:

```bash
docker build -t rss-generator docker
docker run --rm -v "$PWD/feeds:/app/feeds" rss-generator
```

### Coding style

- Python 3.11+
- Type hints and docstrings where appropriate.
- Modular architecture and small, testable functions.

