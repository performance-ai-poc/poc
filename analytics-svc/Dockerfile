FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Non-root: this service only reads its own code, makes outbound HTTP to the
# telemetry backend, and writes to stdout. It needs no elevated privileges.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV APP_ENV=production
ENV HOST=0.0.0.0
ENV PORT=8002
EXPOSE 8002

CMD ["python", "-m", "app.main"]
