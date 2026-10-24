"""Server-Sent Events plumbing that survives the client going away.

Starlette doesn't reliably close a streaming body when the client disconnects (the generator is
left for the garbage collector), so work and cleanup can't live in the response generator.
Instead the work runs as its own task that pushes events into a queue, and the response only
relays them. Closing the relay cancels the task; if the relay is merely abandoned, the task
notices that nobody reads its events and stops itself.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, Awaitable, Callable

Emit = Callable[[str, dict[str, object]], Awaitable[None]]

# Strong references: asyncio only keeps weak ones, and a running answer must not vanish.
_running: set[asyncio.Task[None]] = set()


def sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def relay(
    produce: Callable[[Emit], Awaitable[None]], *, idle_timeout: float = 30.0
) -> AsyncGenerator[str]:
    queue: asyncio.Queue[tuple[str, dict[str, object]] | None] = asyncio.Queue(maxsize=256)

    async def emit(event: str, data: dict[str, object]) -> None:
        try:
            await asyncio.wait_for(queue.put((event, data)), timeout=idle_timeout)
        except TimeoutError:
            # The queue stayed full: nobody is reading, so the client is gone. Stop like Stop.
            raise asyncio.CancelledError from None

    async def run() -> None:
        try:
            await produce(emit)
        finally:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(queue.put(None), timeout=idle_timeout)

    task = asyncio.create_task(run())
    _running.add(task)
    task.add_done_callback(_running.discard)
    try:
        while (item := await queue.get()) is not None:
            yield sse(*item)
    finally:
        if not task.done():
            task.cancel()
