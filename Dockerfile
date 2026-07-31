# syntax=docker/dockerfile:1
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
ARG REQUIREMENTS=requirements/prod.txt
RUN python -m venv /venv && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install -r ${REQUIREMENTS}


FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod
WORKDIR /app

# Debian's default-libmysqlclient-dev is the MariaDB connector, so the runtime
# library to ship is libmariadb3 — not libmysqlclient.
RUN apt-get update && apt-get install -y --no-install-recommends libmariadb3 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

COPY --from=builder /venv /venv
COPY --chown=app:app . .

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
