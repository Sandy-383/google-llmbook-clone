import httpx

from app.core.config import Settings
from tests.conftest import ClientFactory
from tests.utils import client_for, register, running_app


async def test_requires_login(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/notebooks")).status_code == 401
    assert (await client.post("/api/notebooks", json={"title": "x"})).status_code == 401


async def test_crud(client: httpx.AsyncClient) -> None:
    await register(client)

    created = await client.post("/api/notebooks", json={"title": "  Biology notes  "})
    assert created.status_code == 201
    notebook = created.json()
    assert notebook["title"] == "Biology notes"

    listed = await client.get("/api/notebooks")
    assert [n["id"] for n in listed.json()] == [notebook["id"]]

    renamed = await client.patch(f"/api/notebooks/{notebook['id']}", json={"title": "Bio 101"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Bio 101"
    assert renamed.json()["updated_at"] >= notebook["updated_at"]

    assert (await client.delete(f"/api/notebooks/{notebook['id']}")).status_code == 204
    assert (await client.get(f"/api/notebooks/{notebook['id']}")).status_code == 404


async def test_blank_title_is_rejected(client: httpx.AsyncClient) -> None:
    await register(client)
    assert (await client.post("/api/notebooks", json={"title": "   "})).status_code == 422


async def test_users_cannot_see_or_touch_each_others_notebooks(
    make_client: ClientFactory,
) -> None:
    alice, bob = make_client(), make_client()
    await register(alice, email="alice@example.com")
    await register(bob, email="bob@example.com")
    notebook_id = (await alice.post("/api/notebooks", json={"title": "Private"})).json()["id"]

    assert (await bob.get("/api/notebooks")).json() == []
    assert (await bob.get(f"/api/notebooks/{notebook_id}")).status_code == 404
    renamed = await bob.patch(f"/api/notebooks/{notebook_id}", json={"title": "Pwned"})
    assert renamed.status_code == 404
    assert (await bob.delete(f"/api/notebooks/{notebook_id}")).status_code == 404

    still_there = await alice.get(f"/api/notebooks/{notebook_id}")
    assert still_there.status_code == 200
    assert still_there.json()["title"] == "Private"


async def test_notebook_limit(settings: Settings) -> None:
    limited = settings.model_copy(update={"max_notebooks_per_user": 2})
    async with running_app(limited) as app, client_for(app) as client:
        await register(client)
        for i in range(2):
            ok = await client.post("/api/notebooks", json={"title": f"n{i}"})
            assert ok.status_code == 201
        over = await client.post("/api/notebooks", json={"title": "one too many"})
        assert over.status_code == 409
