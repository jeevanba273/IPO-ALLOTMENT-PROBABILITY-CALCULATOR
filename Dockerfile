# One image: the built dashboard plus the backend that serves it and runs every job.
#   docker build -t ipo-intel .
#   docker run -p 8000:8000 --env-file .env -e DASHBOARD_PASSWORD=... ipo-intel

# --- the dashboard ---------------------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- the app -----------------------------------------------------------------------------------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# An editable install keeps the repo layout, so migrations/, config/ and
# frontend/dist/ are found next to the package as they are in development.
COPY backend/pyproject.toml backend/
COPY backend/ipo_intel backend/ipo_intel
RUN pip install -e backend

COPY migrations migrations
COPY config config
COPY --from=web /web/dist frontend/dist

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
# Migrations run on startup; the schedule starts in-process (one copy per database).
CMD ["sh", "-c", "exec ipo-intel serve --host 0.0.0.0 --port ${PORT:-8000}"]
