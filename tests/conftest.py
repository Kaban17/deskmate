import asyncio
from pathlib import Path

import fakeredis.aioredis
import pytest
import pytest_asyncio
from alembic.config import Config
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from deskmate.db.session import make_engine, make_session_factory


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
