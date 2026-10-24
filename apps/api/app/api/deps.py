from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import User
from app.llm.ollama import OllamaClient
from app.services import auth as auth_service
from app.storage import LocalStorage
from app.workers.queue import TaskQueue


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def get_ollama(request: Request) -> OllamaClient:
    ollama: OllamaClient = request.app.state.ollama
    return ollama


def get_storage(request: Request) -> LocalStorage:
    storage: LocalStorage = request.app.state.storage
    return storage


def get_task_queue(request: Request) -> TaskQueue:
    queue: TaskQueue = request.app.state.task_queue
    return queue


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]
OllamaDep = Annotated[OllamaClient, Depends(get_ollama)]
StorageDep = Annotated[LocalStorage, Depends(get_storage)]
TaskQueueDep = Annotated[TaskQueue, Depends(get_task_queue)]


async def get_current_user(request: Request, db: DbSession, settings: SettingsDep) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    user = await auth_service.user_for_session(db, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def client_ip(request: Request) -> str:
    # Uvicorn's --proxy-headers already resolved X-Forwarded-For from trusted proxies.
    return request.client.host if request.client else "unknown"
