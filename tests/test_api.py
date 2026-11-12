import pytest
from sqlalchemy import select

from deskmate.db.models import User
from deskmate.db.session import make_engine
from deskmate.main import build_redis, create_app

H = {"X-API-Key": "test-key"}


async def test_chat_happy_path(client):
    r = await client.post("/chat", json={"user_id": "alice", "text": "Привет"}, headers=H)
    assert r.status_code == 200
    assert (
        r.json().keys() == {"answer", "dialog_id", "message_id", "cached"}
        and r.json()["answer"] == "ответ"
    )


async def test_chat_user_is_namespaced(client, session):
    await client.post("/chat", json={"user_id": "alice", "text": "Привет"}, headers=H)
    assert (await session.scalars(select(User.external_id))).all() == ["api:alice"]


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
async def test_chat_requires_api_key(client, headers):
    assert (
        await client.post("/chat", json={"user_id": "a", "text": "x"}, headers=headers)
    ).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"user_id": "a", "text": ""},
        {"user_id": "a", "text": "   \n"},
        {"user_id": "a", "text": "я" * 4001},
        {"user_id": "", "text": "x"},
        {"user_id": "u" * 65, "text": "x"},
        {"text": "x"},
    ],
)
async def test_chat_rejects_invalid_input_without_calling_llm(client, fake_llm, body):
    assert (await client.post("/chat", json=body, headers=H)).status_code == 422
    assert fake_llm.calls == []


async def test_chat_accepts_exactly_4000_characters_and_trims(client, fake_llm):
    r = await client.post(
        "/chat", json={"user_id": "a", "text": "  " + "я" * 4000 + "  "}, headers=H
    )
    assert r.status_code == 200 and len(fake_llm.calls[0][1][-1].content) == 4000


async def test_chat_llm_failure_is_502(client, fake_llm):
    fake_llm.fail = True
    r = await client.post("/chat", json={"user_id": "a", "text": "x"}, headers=H)
    assert r.status_code == 502 and r.json() == {"detail": "LLM unavailable"}


async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "postgres": True, "redis": True}


async def test_health_degraded_when_redis_down(client_with_broken_redis):
    r = await client_with_broken_redis.get("/health")
    assert r.status_code == 200 and r.json() == {
        "status": "degraded",
        "postgres": True,
        "redis": False,
    }


async def test_health_503_when_postgres_down(client_with_dead_postgres):
    r = await client_with_dead_postgres.get("/health")
    assert r.status_code == 503 and r.json()["postgres"] is False


def test_build_redis_decodes_responses():
    client = build_redis("redis://localhost:6379/0")
    assert client.connection_pool.connection_kwargs["decode_responses"] is True


async def test_startup_fails_without_llm_and_api_key(settings, redis):
    app = create_app(settings, redis=redis, engine=make_engine(DEAD_URL))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        async with app.router.lifespan_context(app):
            pass


DEAD_URL = "postgresql+asyncpg://x:x@127.0.0.1:1/x"
