import asyncio
from collections.abc import Callable

from deskmate.llm.base import ChatMessage, LLMError, LLMResponse


class FakeLLMClient:
    """Test double. `reply`, `fail` and `delay` are plain attributes tests may reassign."""

    def __init__(
        self,
        reply: str | Callable[[list[ChatMessage]], str] = "ответ",
        fail: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.reply = reply
        self.fail = fail
        self.delay = delay
        self.calls: list[tuple[str, list[ChatMessage]]] = []

    async def complete(self, system: str, messages: list[ChatMessage]) -> LLMResponse:
        self.calls.append((system, list(messages)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise LLMError("fake LLM failure")
        text = self.reply(messages) if callable(self.reply) else self.reply
        return LLMResponse(text, "fake", 1, 1)
