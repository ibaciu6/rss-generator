FROM mcr.microsoft.com/devcontainers/python:3.11

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install --with-deps chromium

COPY . .

CMD ["python", "scripts/generate_feeds.py"]

