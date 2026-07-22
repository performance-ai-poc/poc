FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# psycopg[binary] bundles libpq, so no system packages are needed here.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copies the app package including app/seed (schema.sql, endpoints.json, seed
# data + build_seed.py), so the image can both seed and serve.
COPY app ./app

EXPOSE 8000

# Default: serve over streamable-http. docker-compose overrides this to seed
# first (python -m app.seed.build_seed) and then serve, once Postgres is ready.
CMD ["python", "-m", "app.server"]
