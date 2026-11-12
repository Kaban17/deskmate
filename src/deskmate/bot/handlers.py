import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from deskmate.bot import texts
from deskmate.chat.service import ChatService

logger = logging.getLogger(__name__)


def split_reply(text: str, limit: int = texts.MAX_REPLY) -> list[str]:
    """Split text into chunks of at most `limit` characters that concatenate back to `text`."""
    chunks: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = window.rfind("\n") + 1 or window.rfind(" ") + 1 or limit
        chunks.append(text[:cut])
        text = text[cut:]
    chunks.append(text)
    return chunks


class Handlers:
    """Thin Telegram handlers: extract user and text, call the chat service, send the reply."""

    def __init__(self, service: ChatService) -> None:
        self._service = service

    async def on_start(self, message: Message) -> None:
        await message.answer(texts.START)

    async def on_new(self, message: Message) -> None:
        try:
            await self._service.reset_dialog(_external_id(message))
        except Exception:
            logger.exception("failed to reset dialog")
            await message.answer(texts.UNAVAILABLE)
            return
        await message.answer(texts.NEW_DIALOG)

    async def on_text(self, message: Message) -> None:
        text = (message.text or "").strip()
        if not text:
            return
        if len(text) > texts.MAX_INPUT:
            await message.answer(texts.TOO_LONG)
            return
        try:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
            result = await self._service.handle_message(
                _external_id(message), text, message.from_user.full_name
            )
        except Exception:
            logger.exception("failed to answer a Telegram message")
            await message.answer(texts.UNAVAILABLE)
            return
        for chunk in split_reply(result.text):
            await message.answer(chunk)

    async def on_other(self, message: Message) -> None:
        await message.answer(texts.NON_TEXT)


def _external_id(message: Message) -> str:
    return f"tg:{message.from_user.id}"


def build_router(service: ChatService) -> Router:
    handlers = Handlers(service)
    router = Router()
    router.message.register(handlers.on_start, Command("start"))
    router.message.register(handlers.on_new, Command("new"))
    router.message.register(handlers.on_text, F.text)
    router.message.register(handlers.on_other)
    return router
