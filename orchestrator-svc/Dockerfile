FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as a non-root user: the app only ever reads its own code and writes to
# stdout, so it needs no elevated privileges inside the container.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV APP_ENV=production
ENV HOST=0.0.0.0
ENV PORT=8001
EXPOSE 8001

# Programmatic entrypoint (app/main.py's __main__ block) reads HOST/PORT via
# app/config.py's Settings, so changing PORT here is the only place to do it
# — no separate --port flag to keep in sync.
CMD ["python", "-m", "app.main"]
