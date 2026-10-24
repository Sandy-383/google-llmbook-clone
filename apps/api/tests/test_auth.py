import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import AuthSession
from tests.conftest import ClientFactory
from tests.utils import DEFAULT_PASSWORD, client_for, register, running_app


async def test_register_starts_a_session(client: httpx.AsyncClient) -> None:
    resp = await register(client)
    assert resp.status_code == 201
    assert resp.json()["email"] == "alice@example.com"
    assert "password_hash" not in resp.json()

    cookie = resp.headers["set-cookie"].lower()
    assert "llmbook_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


async def test_email_is_case_insensitive(make_client: ClientFactory) -> None:
    assert (await register(make_client(), email="Alice@Example.com")).status_code == 201
    dup = await register(make_client(), email="alice@example.com")
    assert dup.status_code == 409


async def test_short_password_is_rejected(client: httpx.AsyncClient) -> None:
    resp = await register(client, password="short")
    assert resp.status_code == 422


async def test_wrong_password_and_unknown_email_look_the_same(
    make_client: ClientFactory,
) -> None:
    await register(make_client())
    wrong = await make_client().post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "not-the-password"}
    )
    unknown = await make_client().post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "not-the-password"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_logout_revokes_the_session_server_side(make_client: ClientFactory) -> None:
    await register(make_client())
    browser = make_client()
    login = await browser.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200
    token = browser.cookies["llmbook_session"]

    assert (await browser.post("/api/auth/logout")).status_code == 204
    assert (await browser.get("/api/auth/me")).status_code == 401

    # Replaying the old cookie from elsewhere must not work either.
    attacker = make_client()
    attacker.cookies.set("llmbook_session", token)
    assert (await attacker.get("/api/auth/me")).status_code == 401


async def test_session_token_is_stored_hashed(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await register(client)
    token = client.cookies["llmbook_session"]
    stored = (await db.scalars(select(AuthSession.id))).all()
    assert len(stored) == 1
    assert token not in stored


async def test_login_is_rate_limited_per_account(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    body = {"email": "alice@example.com", "password": "wrong-password"}
    for _ in range(settings.login_rate_limit):
        assert (await client.post("/api/auth/login", json=body)).status_code == 401
    blocked = await client.post("/api/auth/login", json=body)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


async def test_successful_login_resets_the_account_limit(
    make_client: ClientFactory, settings: Settings
) -> None:
    await register(make_client())
    browser = make_client()
    wrong = {"email": "alice@example.com", "password": "wrong-password"}
    right = {"email": "alice@example.com", "password": DEFAULT_PASSWORD}

    for _ in range(settings.login_rate_limit - 1):
        await browser.post("/api/auth/login", json=wrong)
    assert (await browser.post("/api/auth/login", json=right)).status_code == 200

    # Later attempts on the same account must not inherit the earlier failures.
    other = make_client()
    for _ in range(settings.login_rate_limit - 1):
        assert (await other.post("/api/auth/login", json=wrong)).status_code == 401


async def test_foreign_origin_is_rejected(client: httpx.AsyncClient) -> None:
    body = {"email": "alice@example.com", "password": "whatever-password"}
    evil = await client.post(
        "/api/auth/login", json=body, headers={"origin": "https://evil.example"}
    )
    assert evil.status_code == 403

    ours = await client.post(
        "/api/auth/login", json=body, headers={"origin": "http://localhost:8080"}
    )
    assert ours.status_code == 401


async def test_registration_can_be_disabled(settings: Settings) -> None:
    closed = settings.model_copy(update={"allow_registration": False})
    async with running_app(closed) as app, client_for(app) as client:
        assert (await register(client)).status_code == 403
        assert (await client.get("/api/auth/config")).json() == {"registration": "closed"}


async def test_invite_code_is_required_when_set(settings: Settings) -> None:
    invite_only = settings.model_copy(update={"registration_code": "friends-2026"})
    async with running_app(invite_only) as app, client_for(app) as client:
        assert (await client.get("/api/auth/config")).json() == {"registration": "invite"}
        body = {"email": "friend@example.com", "password": DEFAULT_PASSWORD}
        assert (await client.post("/api/auth/register", json=body)).status_code == 403
        wrong = await client.post("/api/auth/register", json={**body, "invite_code": "guess"})
        assert wrong.status_code == 403
        right = await client.post(
            "/api/auth/register", json={**body, "invite_code": " friends-2026 "}
        )
        assert right.status_code == 201


async def test_open_registration_is_reported(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/auth/config")).json() == {"registration": "open"}


async def test_requests_from_the_page_own_origin_are_allowed(client: httpx.AsyncClient) -> None:
    """Served under a tunnel name that isn't in ALLOWED_ORIGINS, same-origin requests work."""
    body = {"email": "alice@example.com", "password": "whatever-password"}
    host = {"host": "random-words.trycloudflare.com"}
    same = await client.post(
        "/api/auth/login",
        json=body,
        headers={**host, "origin": "https://random-words.trycloudflare.com"},
    )
    assert same.status_code == 401  # passed the origin check; wrong password
    cross = await client.post(
        "/api/auth/login",
        json=body,
        headers={**host, "origin": "https://evil.trycloudflare.com"},
    )
    assert cross.status_code == 403
    opaque = await client.post("/api/auth/login", json=body, headers={**host, "origin": "null"})
    assert opaque.status_code == 403
