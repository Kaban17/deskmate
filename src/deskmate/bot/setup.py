import asyncio
import contextlib
import logging
import secrets

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, FastAPI, HTTPException, Request

from deskmate.bot.handlers import build_router
from deskmate.cache.dedupe import UpdateDeduper
from deskmate.chat.service import ChatService
from deskmate.config import Settings

logger = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
# How long shutdown waits for webhook updates that are still being processed.
SHUTDOWN_GRACE_S = 10.0


def build_dispatcher(service: ChatService) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(build_router(service))
    return dp


def build_bot(settings: Settings, service: ChatService) -> tuple[Bot, Dispatcher]:
    assert settings.telegram_bot_token
    return Bot(settings.telegram_bot_token), build_dispatcher(service)


def webhook_router(
    bot: Bot,
    dp: Dispatcher,
    deduper: UpdateDeduper,
    secret: str,
    tasks: set[asyncio.Task[None]] | None = None,
) -> APIRouter:
    router = APIRouter()
    # Strong references: the event loop keeps only weak ones to running tasks. The app passes
    # its own set so that shutdown can wait for the updates still being processed.
    if tasks is None:
        tasks = set()

    def _finished(task: asyncio.Task[None]) -> None:
        tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("update processing failed", exc_info=task.exception())

    @router.post("/telegram/webhook")
    async def webhook(request: Request) -> dict[str, bool]:
        received = request.headers.get(SECRET_HEADER, "")
        if not secrets.compare_digest(received.encode(), secret.encode()):
            raise HTTPException(status_code=403, detail="invalid secret")
        update = Update.model_validate(await request.json(), context={"bot": bot})
        if await deduper.first_seen(update.update_id):
            task = asyncio.create_task(dp.feed_update(bot, update))
            tasks.add(task)
            task.add_done_callback(_finished)
        return {"ok": True}

    return router


async def start_bot(app: FastAPI, settings: Settings) -> None:
    bot: Bot = app.state.bot
    if settings.bot_mode == "webhook":
        base = (settings.webhook_base_url or "").rstrip("/")
        await bot.set_webhook(f"{base}/telegram/webhook", secret_token=settings.webhook_secret)
    elif settings.bot_mode == "polling":
        await bot.delete_webhook()
        app.state.polling_task = asyncio.create_task(
            app.state.dp.start_polling(bot, handle_signals=False)
        )


async def stop_bot(app: FastAPI) -> None:
    task: asyncio.Task[None] | None = getattr(app.state, "polling_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    # Updates already answered 200 and marked as seen are never resent, so let them finish
    # before the bot session, Redis and the engine are closed.
    pending_updates: set[asyncio.Task[None]] = getattr(app.state, "webhook_tasks", set())
    if pending_updates:
        _, still_running = await asyncio.wait(set(pending_updates), timeout=SHUTDOWN_GRACE_S)
        if still_running:
            logger.warning(
                "%d webhook updates still running after %.0f s at shutdown",
                len(still_running),
                SHUTDOWN_GRACE_S,
            )
    # The webhook stays registered on purpose: a restart should not drop updates.
    if app.state.bot_owned:
        await app.state.bot.session.close()
