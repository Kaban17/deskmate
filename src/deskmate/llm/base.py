from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMError(Exception):
    """Raised when the LLM provider fails; callers never see SDK exceptions."""


class LLMClient(Protocol):
    async def complete(self, system: str, messages: list[ChatMessage]) -> LLMResponse: ...
