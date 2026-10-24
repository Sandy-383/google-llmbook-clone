import httpx


async def test_healthz(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_needs_db_and_redis_but_not_ollama(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "redis": "ok", "ollama": "unavailable"}


async def test_responses_carry_request_id_and_security_headers(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/healthz", headers={"x-request-id": "abc-123"})
    assert resp.headers["x-request-id"] == "abc-123"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


async def test_malformed_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/healthz", headers={"x-request-id": "bad id; injected=1"})
    assert resp.headers["x-request-id"] != "bad id; injected=1"
    assert len(resp.headers["x-request-id"]) == 32
