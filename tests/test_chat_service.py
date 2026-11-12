import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from deskmate.db import repo
from deskmate.db.models import Message
from deskmate.llm.base import LLMError
from deskmate.llm.fake import FakeLLMClient
from deskmate.prompts import load_prompt

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def test_first_message_creates_user_dialog_and_both_messages(make_service, session):
    llm = FakeLLMClient(reply="Здравствуйте")
    r = await make_service(llm).handle_message("tg:1", "Привет", "Анна")
    assert r.text == "Здравствуйте" and r.cached is False
    rows = await repo.recent_messages(session, r.dialog_id, 10)
    assert [(m.role, m.content) for m in rows] == [
        ("user", "Привет"),
        ("assistant", "Здравствуйте"),
    ]
    assert (rows[1].id, rows[1].model, rows[1].prompt_version) == (r.message_id, "fake", "v1")
    assert rows[1].latency_ms is not None


async def test_llm_receives_system_prompt_and_history(make_service):
    llm = FakeLLMClient(reply="ок")
    svc = make_service(llm)
    await svc.handle_message("tg:1", "раз")
    await svc.handle_message("tg:1", "два")
    system, messages = llm.calls[-1]
    assert system == load_prompt("v1")
    assert [(m.role, m.content) for m in messages] == [
        ("user", "раз"),
        ("assistant", "ок"),
        ("user", "два"),
    ]


async def test_window_is_capped(make_service, settings):
    settings.context_window = 4
    llm = FakeLLMClient(reply=lambda msgs: "a" + msgs[-1].content[1:])
    svc = make_service(llm)
    for i in range(5):
        await svc.handle_message("tg:1", f"q{i}")
    window = llm.calls[-1][1]
    assert [(m.role, m.content) for m in window] == [
        ("user", "q3"),
        ("assistant", "a3"),
        ("user", "q4"),
    ]


async def test_history_survives_losing_redis_context(make_service, redis):
    llm = FakeLLMClient(reply="ок")
    svc = make_service(llm, redis)
    r = await svc.handle_message("tg:1", "раз")
    await redis.delete(f"ctx:{r.dialog_id}")
    await svc.handle_message("tg:1", "два")
    assert [m.content for m in llm.calls[-1][1]] == ["раз", "ок", "два"]


async def test_works_with_redis_down(make_service, broken_redis, session):
    llm = FakeLLMClient(reply="ок")
    svc = make_service(llm, broken_redis)
    await svc.handle_message("tg:1", "раз")
    r = await svc.handle_message("tg:1", "два")
    assert r.text == "ок" and [m.content for m in llm.calls[-1][1]] == ["раз", "ок", "два"]
    assert len(await repo.recent_messages(session, r.dialog_id, 10)) == 4


async def test_identical_first_question_is_served_from_cache(make_service, session):
    llm = FakeLLMClient(reply="ответ")
    svc = make_service(llm)
    await svc.handle_message("tg:1", "Как оформить отпуск?")
    r = await svc.handle_message("tg:2", "Как оформить отпуск?")
    assert r.cached is True and r.text == "ответ" and len(llm.calls) == 1
    msg = (await repo.recent_messages(session, r.dialog_id, 10))[-1]
    assert msg.input_tokens is None and msg.output_tokens is None


async def test_llm_failure_keeps_user_message_and_raises(make_service, session_factory):
    svc = make_service(FakeLLMClient(fail=True))
    with pytest.raises(LLMError):
        await svc.handle_message("tg:1", "Привет")
    async with session_factory() as s:
        rows = (await s.scalars(select(Message))).all()
    assert [(m.role, m.content) for m in rows] == [("user", "Привет")]


async def test_reset_starts_a_new_dialog_with_empty_history(make_service):
    llm = FakeLLMClient(reply="ок")
    svc = make_service(llm)
    r1 = await svc.handle_message("tg:1", "раз")
    await svc.reset_dialog("tg:1")
    r2 = await svc.handle_message("tg:1", "два")
    assert r2.dialog_id != r1.dialog_id and [m.content for m in llm.calls[-1][1]] == ["два"]


async def test_idle_dialog_is_replaced(make_service):
    now = [T0]
    llm = FakeLLMClient(reply="ок")
    svc = make_service(llm, clock=lambda: now[0])
    r1 = await svc.handle_message("tg:1", "раз")
    now[0] = T0 + timedelta(minutes=31)
    r2 = await svc.handle_message("tg:1", "два")
    assert r2.dialog_id != r1.dialog_id and len(llm.calls[-1][1]) == 1


async def test_concurrent_messages_from_one_user_are_serialised(make_service, session):
    llm = FakeLLMClient(reply=lambda msgs: f"ответ на {msgs[-1].content}", delay=0.05)
    svc = make_service(llm)
    a, b = await asyncio.gather(
        svc.handle_message("tg:1", "первый"), svc.handle_message("tg:1", "второй")
    )
    assert a.dialog_id == b.dialog_id
    rows = await repo.recent_messages(session, a.dialog_id, 10)
    assert [m.content for m in rows] == ["первый", "ответ на первый", "второй", "ответ на второй"]


async def test_different_users_are_not_blocked_by_each_other(make_service):
    svc = make_service(FakeLLMClient(reply="ок", delay=0.2))
    start = time.monotonic()
    await asyncio.gather(svc.handle_message("tg:1", "a"), svc.handle_message("tg:2", "b"))
    assert time.monotonic() - start < 0.35
