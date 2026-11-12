import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskmate.cache.context import ContextStore
from deskmate.cache.llm_cache import LLMCache
from deskmate.config import Settings
from deskmate.db import repo
from deskmate.llm.base import ChatMessage, LLMClient
from deskmate.prompts import load_prompt


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ChatResult:
    text: str
    dialog_id: int
    message_id: int
    cached: bool


class ChatService:
    """The chat logic shared by the REST API and the Telegram bot."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        context: ContextStore,
        cache: LLMCache,
        settings: Settings,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._llm = llm
        self._context = context
        self._cache = cache
        self._settings = settings
        self._clock = clock
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def handle_message(
        self, external_id: str, text: str, full_name: str | None = None
    ) -> ChatResult:
        async with self._locks[external_id]:
            return await self._handle(external_id, text, full_name)

    async def reset_dialog(self, external_id: str) -> None:
        async with self._locks[external_id]:
            async with self._session_factory() as session:
                user = await repo.get_or_create_user(session, external_id)
                dialog_id = await repo.close_active_dialog(session, user.id)
                await session.commit()
            if dialog_id is not None:
                await self._context.delete(dialog_id)

    async def _handle(self, external_id: str, text: str, full_name: str | None) -> ChatResult:
        settings = self._settings
        now = self._clock()

        # Transaction 1: the user message is kept even if the LLM call fails.
        async with self._session_factory() as session:
            user = await repo.get_or_create_user(session, external_id, full_name)
            dialog = await repo.get_or_create_active_dialog(
                session, user.id, now, timedelta(minutes=settings.dialog_idle_minutes)
            )
            await repo.add_message(session, dialog.id, "user", text, now=now)
            dialog_id = dialog.id
            await session.commit()

        window = await self._build_window(dialog_id, text)

        started = time.monotonic()
        key = LLMCache.key(settings.llm_model, settings.prompt_version, window)
        answer = await self._cache.get(key)
        cached = answer is not None
        model = settings.llm_model
        input_tokens = output_tokens = None
        if answer is None:
            response = await self._llm.complete(load_prompt(settings.prompt_version), window)
            answer = response.text
            model = response.model
            input_tokens, output_tokens = response.input_tokens, response.output_tokens
        latency_ms = int((time.monotonic() - started) * 1000)

        # Transaction 2: the assistant message.
        async with self._session_factory() as session:
            message = await repo.add_message(
                session,
                dialog_id,
                "assistant",
                answer,
                model=model,
                prompt_version=settings.prompt_version,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                now=self._clock(),
            )
            message_id = message.id
            await session.commit()

        await self._context.set(dialog_id, [*window, ChatMessage("assistant", answer)])
        if not cached:
            await self._cache.set(key, answer)
        return ChatResult(answer, dialog_id, message_id, cached)

    async def _build_window(self, dialog_id: int, text: str) -> list[ChatMessage]:
        limit = self._settings.context_window
        window = await self._context.get(dialog_id)
        if window is None:
            async with self._session_factory() as session:
                rows = await repo.recent_messages(session, dialog_id, limit)
            window = [ChatMessage(m.role, m.content) for m in rows]  # type: ignore[arg-type]
        else:
            window = [*window, ChatMessage("user", text)]
        window = window[-limit:]
        while window and window[0].role == "assistant":
            window = window[1:]
        return window
