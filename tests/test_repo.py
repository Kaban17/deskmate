from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from deskmate.db import repo
from deskmate.db.models import Dialog

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
IDLE = timedelta(minutes=30)


@pytest_asyncio.fixture
async def user(session):
    return await repo.get_or_create_user(session, "tg:1", "Анна")


@pytest_asyncio.fixture
async def dialog(session, user):
    return await repo.get_or_create_active_dialog(session, user.id, T0, IDLE)


async def test_user_is_created_once(session):
    a = await repo.get_or_create_user(session, "tg:1", "Анна")
    b = await repo.get_or_create_user(session, "tg:1")
    assert a.id == b.id and a.role == "employee"


async def test_active_dialog_is_reused_within_idle_window(session, user):
    d1 = await repo.get_or_create_active_dialog(session, user.id, T0, IDLE)
    d2 = await repo.get_or_create_active_dialog(session, user.id, T0 + timedelta(minutes=29), IDLE)
    assert d1.id == d2.id


async def test_idle_dialog_is_closed_and_replaced(session, user):
    d1 = await repo.get_or_create_active_dialog(session, user.id, T0, IDLE)
    d2 = await repo.get_or_create_active_dialog(session, user.id, T0 + timedelta(minutes=31), IDLE)
    assert d2.id != d1.id and d1.status == "closed" and d2.status == "active"


async def test_second_active_dialog_is_rejected_by_the_database(session, user):
    session.add_all(
        [Dialog(user_id=user.id, status="active"), Dialog(user_id=user.id, status="active")]
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_close_active_dialog(session, user):
    d = await repo.get_or_create_active_dialog(session, user.id, T0, IDLE)
    assert await repo.close_active_dialog(session, user.id) == d.id
    assert await repo.close_active_dialog(session, user.id) is None


async def test_recent_messages_returns_newest_oldest_first(session, dialog):
    for i in range(5):
        await repo.add_message(session, dialog.id, "user", f"m{i}")
    assert [m.content for m in await repo.recent_messages(session, dialog.id, 3)] == [
        "m2",
        "m3",
        "m4",
    ]


async def test_assistant_metadata_is_stored(session, dialog):
    m = await repo.add_message(
        session,
        dialog.id,
        "assistant",
        "ответ",
        model="m",
        prompt_version="v1",
        input_tokens=10,
        output_tokens=5,
        latency_ms=120,
    )
    assert (m.model, m.prompt_version, m.input_tokens, m.output_tokens, m.latency_ms) == (
        "m",
        "v1",
        10,
        5,
        120,
    )


async def test_add_message_uses_explicit_now(session, dialog):
    later = T0 + timedelta(minutes=5)
    m = await repo.add_message(session, dialog.id, "user", "hi", now=later)
    await session.refresh(dialog)
    assert m.created_at == later and dialog.last_message_at == later
