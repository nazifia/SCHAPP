"""Liveness and readiness probes.

`/healthz` must not touch the database — a probe that fails when Postgres
blips would have the orchestrator kill healthy web workers.
"""

from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache


@never_cache
def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@never_cache
def readyz(request: HttpRequest) -> JsonResponse:
    checks: dict[str, str] = {}

    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"

    try:
        from django.core.cache import cache

        cache.set("readyz", "1", 5)
        checks["cache"] = "ok" if cache.get("readyz") == "1" else "error: no round-trip"
    except Exception as exc:
        checks["cache"] = f"error: {exc.__class__.__name__}"

    ready = all(v == "ok" for v in checks.values())
    return JsonResponse(
        {"status": "ready" if ready else "degraded", "checks": checks},
        status=200 if ready else 503,
    )
