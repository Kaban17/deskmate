import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from aiogram.methods import SendMessage
from fastapi import FastAPI
from sqlalchemy import select

from deskmate.bot import texts
from deskmate.bot.handlers import Handlers, split_reply
from deskmate.bot.setup import start_bot, stop_bot, webhook_router
from deskmate.cache.dedupe import UpdateDeduper
from deskmate.config import Settings
from deskmate.db.models import Message, User
from deskmate.main import create_app

SECRET = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}
UPDATE = {
    "update_id": 1001,
    "message": {
        "message_id": 1,
        "date": 1700000000,
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1, "is_bot": False, "first_name": "A"},
        "text": "привет",
    },
}


def fake_message(text="привет", user_id=1, full_name="Тест"):
    msg = MagicMock()
    msg.text = text
    msg.from_user.id = user_id
    msg.from_user.full_name = full_name
    msg.chat.id = user_id
    msg.answer = AsyncMock()
    msg.bot.send_chat_action = AsyncMock()
    return msg


@pytest.fixture
def handlers(make_service, fake_llm):
    return Handlers(make_service(fake_llm))


@pytest.fixture
def dp():
    mock = MagicMock()
    mock.feed_update = AsyncMock()
    return mock


@pytest_asyncio.fixture
async def webhook_client(dp, redis):
    app = FastAPI()
    app.include_router(webhook_router(MagicMock(), dp, UpdateDeduper(redis, 60), "s3cret"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def mock_bot():
    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    bot.delete_webhook = AsyncMock()
    bot.session.close = AsyncMock()
    return bot


def test_split_reply_short_text_is_one_chunk():
    assert split_reply("привет") == ["привет"]


@pytest.mark.parametrize(
    "text", ["я" * 10000, ("слово " * 2000), ("строка\n" * 1500), "a" * 4096, "a" * 4097]
)
def test_split_reply_chunks_fit_and_lose_nothing(text):
    chunks = split_reply(text)
    assert all(0 < len(c) <= 4096 for c in chunks) and "".join(chunks) == text


def test_split_reply_prefers_newline():
    assert split_reply("a" * 10 + "\n" + "b" * 10, limit=15) == ["a" * 10 + "\n", "b" * 10]


async def test_start(handlers):
    msg = fake_message(text="/start")
    await handlers.on_start(msg)
    msg.answer.assert_awaited_once_with(texts.START)


async def test_text_goes_to_service_and_reply_is_sent(handlers, fake_llm, session):
    msg = fake_message(text="  Привет  ", user_id=42, full_name="Анна")
    await handlers.on_text(msg)
    msg.answer.assert_awaited_once_with("ответ")
    msg.bot.send_chat_action.assert_awaited_once()
    assert fake_llm.calls[0][1][-1].content == "Привет"
    assert (await session.scalars(select(User.external_id))).all() == ["tg:42"]


async def test_long_answer_is_sent_in_chunks(handlers, fake_llm):
    fake_llm.reply = "я" * 9000
    msg = fake_message(text="вопрос")
    await handlers.on_text(msg)
    sent = [c.args[0] for c in msg.answer.await_args_list]
    assert len(sent) == 3 and "".join(sent) == "я" * 9000


async def test_too_long_input_is_refused_without_llm(handlers, fake_llm):
    msg = fake_message(text="я" * 4001)
    await handlers.on_text(msg)
    msg.answer.assert_awaited_once_with(texts.TOO_LONG)
    assert fake_llm.calls == []


async def test_whitespace_only_text_is_ignored(handlers, fake_llm):
    msg = fake_message(text="   ")
    await handlers.on_text(msg)
    msg.answer.assert_not_awaited()
    assert fake_llm.calls == []


async def test_non_text_message(handlers, fake_llm):
    msg = fake_message(text=None)
    await handlers.on_other(msg)
    msg.answer.assert_awaited_once_with(texts.NON_TEXT)
    assert fake_llm.calls == []


async def test_llm_failure_sends_apology(handlers, fake_llm):
    fake_llm.fail = True
    msg = fake_message(text="вопрос")
    await handlers.on_text(msg)
    msg.answer.assert_awaited_once_with(texts.UNAVAILABLE)


async def test_new_resets_dialog(handlers, fake_llm):
    await handlers.on_text(fake_message(text="раз"))
    msg = fake_message(text="/new")
    await handlers.on_new(msg)
    msg.answer.assert_awaited_once_with(texts.NEW_DIALOG)
    await handlers.on_text(fake_message(text="два"))
    assert [m.content for m in fake_llm.calls[-1][1]] == ["два"]


async def test_webhook_rejects_wrong_secret(webhook_client, dp):
    r = await webhook_client.post(
        "/telegram/webhook", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "bad"}
    )
    assert r.status_code == 403
    dp.feed_update.assert_not_awaited()


async def test_webhook_rejects_missing_secret(webhook_client):
    assert (await webhook_client.post("/telegram/webhook", json=UPDATE)).status_code == 403


async def test_webhook_feeds_update_once_for_duplicates(webhook_client, dp):
    for _ in range(2):
        r = await webhook_client.post("/telegram/webhook", json=UPDATE, headers=SECRET)
        assert r.status_code == 200
    await asyncio.sleep(0)
    assert dp.feed_update.await_count == 1


async def test_webhook_returns_before_processing_finishes(webhook_client, dp):
    async def slow(*args, **kwargs):
        await asyncio.sleep(0.5)

    dp.feed_update.side_effect = slow
    start = time.monotonic()
    assert (
        await webhook_client.post("/telegram/webhook", json=UPDATE, headers=SECRET)
    ).status_code == 200
    assert time.monotonic() - start < 0.2


async def test_webhook_logs_failure_of_background_processing(webhook_client, dp, caplog):
    dp.feed_update.side_effect = RuntimeError("boom")
    r = await webhook_client.post("/telegram/webhook", json=UPDATE, headers=SECRET)
    assert r.status_code == 200
    await asyncio.sleep(0.05)
    assert any(
        "update processing failed" in rec.getMessage() and rec.exc_info for rec in caplog.records
    )


async def _app_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_app_in_webhook_mode_registers_webhook_and_accepts_updates(fake_llm, redis, engine):
    cfg = Settings(
        api_key="k",
        bot_mode="webhook",
        telegram_bot_token="1:x",
        webhook_base_url="https://example.test/",
        webhook_secret="s3cret",
        _env_file=None,
    )
    bot = mock_bot()
    app = create_app(cfg, llm=fake_llm, redis=redis, engine=engine, bot=bot)
    async with app.router.lifespan_context(app):
        bot.set_webhook.assert_awaited_once_with(
            "https://example.test/telegram/webhook", secret_token="s3cret"
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/telegram/webhook", json=UPDATE, headers=SECRET)
            assert r.status_code == 200
            await asyncio.sleep(0.05)
    bot.session.close.assert_not_awaited()  # injected bot belongs to the caller


async def test_app_with_bot_off_has_no_webhook_route(settings, fake_llm, redis, engine):
    bot = mock_bot()
    app = create_app(settings, llm=fake_llm, redis=redis, engine=engine, bot=bot)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            assert (
                await c.post("/telegram/webhook", json=UPDATE, headers=SECRET)
            ).status_code == 404
    assert bot.method_calls == []


async def test_polling_mode_starts_and_stops_polling():
    started = asyncio.Event()

    async def polling(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    bot, dp = mock_bot(), MagicMock()
    dp.start_polling = MagicMock(side_effect=polling)
    app = SimpleNamespace(state=SimpleNamespace(bot=bot, dp=dp, bot_owned=True))
    cfg = Settings(api_key="k", bot_mode="polling", telegram_bot_token="1:x", _env_file=None)

    await start_bot(app, cfg)
    await asyncio.wait_for(started.wait(), 1)
    bot.delete_webhook.assert_awaited_once()
    dp.start_polling.assert_called_once_with(bot, handle_signals=False)
    task = app.state.polling_task
    assert not task.done()

    await stop_bot(app)
    assert task.cancelled()
    bot.session.close.assert_awaited_once()


async def test_failed_typing_action_does_not_block_the_answer(handlers, fake_llm):
    msg = fake_message(text="вопрос")
    msg.bot.send_chat_action.side_effect = RuntimeError("telegram is down")
    await handlers.on_text(msg)
    msg.answer.assert_awaited_once_with("ответ")


async def test_messages_from_one_user_are_answered_in_arrival_order(handlers, fake_llm, session):
    fake_llm.reply = lambda msgs: f"ответ на {msgs[-1].content}"
    answered: list[str] = []

    def tracked(text, typing_delay):
        msg = fake_message(text=text, user_id=7)

        async def typing(*args, **kwargs):
            await asyncio.sleep(typing_delay)

        async def answer(reply):
            answered.append(reply)

        msg.bot.send_chat_action.side_effect = typing
        msg.answer.side_effect = answer
        return msg

    await asyncio.gather(
        handlers.on_text(tracked("первый", 0.1)), handlers.on_text(tracked("второй", 0))
    )
    assert answered == ["ответ на первый", "ответ на второй"]
    rows = (await session.scalars(select(Message.content).order_by(Message.id))).all()
    assert rows == ["первый", "ответ на первый", "второй", "ответ на второй"]


def webhook_settings():
    return Settings(
        api_key="k",
        bot_mode="webhook",
        telegram_bot_token="1:x",
        webhook_base_url="https://example.test/",
        webhook_secret="s3cret",
        _env_file=None,
    )


async def test_shutdown_waits_for_in_flight_webhook_updates(fake_llm, redis, engine):
    fake_llm.delay = 0.2
    bot = AsyncMock()  # awaiting bot(method) is how aiogram sends a reply
    app = create_app(webhook_settings(), llm=fake_llm, redis=redis, engine=engine, bot=bot)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/telegram/webhook", json=UPDATE, headers=SECRET)
            assert r.status_code == 200
    sent = [call.args[0] for call in bot.await_args_list]
    assert [m.text for m in sent if isinstance(m, SendMessage)] == ["ответ"]


async def test_shutdown_wait_is_bounded_and_logged(monkeypatch, caplog):
    monkeypatch.setattr("deskmate.bot.setup.SHUTDOWN_GRACE_S", 0.05)
    stuck = asyncio.create_task(asyncio.Event().wait())
    bot = mock_bot()
    app = SimpleNamespace(state=SimpleNamespace(bot=bot, bot_owned=True, webhook_tasks={stuck}))
    await stop_bot(app)
    assert any("1 webhook updates still running" in r.getMessage() for r in caplog.records)
    bot.session.close.assert_awaited_once()
    stuck.cancel()
