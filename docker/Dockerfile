FROM mcr.microsoft.com/devcontainers/python:3.11

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install --with-deps chromium

COPY . .

# Must run as a module: `python scripts/generate_feeds.py` puts scripts/ on
# sys.path instead of the repo root, so `from core.cli import main` fails.
CMD ["python", "-m", "scripts.generate_feeds"]

