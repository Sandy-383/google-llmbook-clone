from redis.asyncio import Redis


async def hit(redis: Redis, key: str, *, limit: int, window_seconds: int) -> int | None:
    """Fixed-window counter. Returns None if allowed, else seconds until the window resets."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = await pipe.execute()
    if int(count) <= limit:
        return None
    ttl = await redis.ttl(key)
    return max(int(ttl), 1)
