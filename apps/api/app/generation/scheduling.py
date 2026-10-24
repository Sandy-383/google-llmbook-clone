"""Background generation shares one GPU with people chatting, so it gives way to them.

Ollama runs one request at a time here, so a background call that starts while someone asks a
question makes them wait for it. Before each call, background jobs wait until no answer is being
generated. A call already running still finishes first, so calls are kept short where possible.
"""

import asyncio

import structlog
from redis.asyncio import Redis

from app.chat.lock import CHAT_LOCK_PATTERN

log = structlog.get_logger(__name__)


class TransientGenerationError(Exception):
    """Worth retrying later, e.g. the model server is down or restarting."""


async def wait_for_chat_idle(
    redis: Redis, *, poll_s: float = 1.0, max_wait_s: float = 600.0
) -> None:
    """Return once no chat answer is being generated (or after `max_wait_s`, so a stuck lock
    can't stall background work forever; locks also expire on their own)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    waited = False
    while loop.time() < deadline:
        busy = False
        async for _ in redis.scan_iter(match=CHAT_LOCK_PATTERN, count=100):
            busy = True
            break
        if not busy:
            if waited:
                log.info("background_generation_resumed")
            return
        waited = True
        await asyncio.sleep(poll_s)
    log.warning("chat_idle_wait_timed_out")
