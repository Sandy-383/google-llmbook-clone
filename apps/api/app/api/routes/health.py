import asyncio
from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import DbSession, OllamaDep, RedisDep, SettingsDep

router = APIRouter(tags=["health"])

_CHECK_TIMEOUT_S = 3.0


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependencies checked."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    response: Response, db: DbSession, redis: RedisDep, ollama: OllamaDep, settings: SettingsDep
) -> dict[str, Any]:
    """Readiness: Postgres and Redis must answer. Ollama is reported but not required,
    so sign-in and notebook management keep working while the model server restarts."""
    checks: dict[str, str] = {}

    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), _CHECK_TIMEOUT_S)
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    try:
        await asyncio.wait_for(redis.ping(), _CHECK_TIMEOUT_S)
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    try:
        missing = await ollama.missing_models([settings.chat_model, settings.embed_model])
        checks["ollama"] = f"missing models: {', '.join(missing)}" if missing else "ok"
    except Exception:
        checks["ollama"] = "unavailable"

    ready = checks["database"] == "ok" and checks["redis"] == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}
