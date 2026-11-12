import asyncio
from pathlib import Path

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from alembic.config import Config
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from deskmate.cache.context import ContextStore
from deskmate.cache.llm_cache import LLMCache
from deskmate.chat.service import ChatService, utcnow
from deskmate.config import Settings
from deskmate.db.session import make_engine, make_session_factory
from deskmate.llm.fake import FakeLLMClient
from deskmate.main import create_app


def _upgrade(url: str) -> None:
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:17") as pg:
        yield pg.get_connection_url(driver="asyncpg")


@pytest_asyncio.fixture(scope="session")
async def engine(postgres_url):
    # alembic's env.py calls asyncio.run, so it must not run on the test loop's thread
    await asyncio.to_thread(_upgrade, postgres_url)
    engine = make_engine(postgres_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    yield make_session_factory(engine)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, dialogs, messages RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class _BrokenRedis:
    """Stands in for a Redis client whose every call fails."""

    def __getattr__(self, name):
        async def fail(*args, **kwargs):
            raise RedisConnectionError("redis is down")

        return fail


@pytest.fixture
def broken_redis():
    return _BrokenRedis()


@pytest.fixture
def settings():
    return Settings(api_key="test-key", _env_file=None)


@pytest.fixture
def make_service(session_factory, settings, redis):
    default_redis = redis

    def _make(llm=None, redis=None, clock=None):
        client = redis if redis is not None else default_redis
        return ChatService(
            session_factory,
            llm or FakeLLMClient(),
            ContextStore(client, settings.context_window, settings.context_ttl_s),
            LLMCache(client, settings.cache_ttl_s),
            settings,
            clock or utcnow,
        )

    return _make


@pytest.fixture
def fake_llm():
    return FakeLLMClient()


async def _client(app):
    # ASGITransport does not run the ASGI lifespan, so enter it explicitly.
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest_asyncio.fixture
async def client(settings, fake_llm, redis, engine):
    app = create_app(settings, llm=fake_llm, redis=redis, engine=engine)
    async for c in _client(app):
        yield c


@pytest_asyncio.fixture
async def client_with_broken_redis(settings, fake_llm, broken_redis, engine):
    app = create_app(settings, llm=fake_llm, redis=broken_redis, engine=engine)
    async for c in _client(app):
        yield c


@pytest_asyncio.fixture
async def client_with_dead_postgres(settings, fake_llm, redis):
    dead = make_engine("postgresql+asyncpg://x:x@127.0.0.1:1/x")
    app = create_app(settings, llm=fake_llm, redis=redis, engine=dead)
    async for c in _client(app):
        yield c
    await dead.dispose()
