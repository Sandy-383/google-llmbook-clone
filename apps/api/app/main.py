from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.routes import auth, chat, health, notebooks, notes, sources
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.middleware import OriginCheckMiddleware, RequestContextMiddleware
from app.db.session import build_engine, build_sessionmaker
from app.llm.ollama import OllamaClient
from app.storage import LocalStorage
from app.workers.queue import CeleryTaskQueue

log = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory; run with `uvicorn --factory app.main:create_app`."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(
            settings.database_url, null_pool=settings.db_null_pool, pool_size=settings.db_pool_size
        )
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        http = httpx.AsyncClient(
            base_url=settings.ollama_base_url, timeout=httpx.Timeout(10.0, read=300.0)
        )
        app.state.settings = settings
        app.state.sessionmaker = build_sessionmaker(engine)
        app.state.redis = redis
        app.state.ollama = OllamaClient(http)
        app.state.storage = LocalStorage(settings.storage_dir)
        app.state.task_queue = CeleryTaskQueue()
        log.info("startup", environment=settings.environment, chat_model=settings.chat_model)
        try:
            yield
        finally:
            await http.aclose()
            await redis.aclose()
            await engine.dispose()

    docs = not settings.is_production
    app = FastAPI(
        title="LLMBook API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        openapi_url="/api/openapi.json" if docs else None,
        redoc_url=None,
    )
    # Added last = outermost, so the access log also covers requests the origin check rejects.
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(notebooks.router, prefix="/api/notebooks")
    app.include_router(sources.router, prefix="/api/notebooks/{notebook_id}/sources")
    app.include_router(chat.router, prefix="/api/notebooks/{notebook_id}")
    app.include_router(notes.router, prefix="/api/notebooks/{notebook_id}")
    return app
