import anthropic
from anthropic import AsyncAnthropic

from deskmate.llm.base import ChatMessage, LLMError, LLMResponse


class ClaudeClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._client = client or AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(self, system: str, messages: list[ChatMessage]) -> LLMResponse:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
        except anthropic.APIError as exc:
            raise LLMError(str(exc)) from exc
        text = "".join(b.text for b in response.content if b.type == "text")
        if not text.strip():
            # An empty assistant turn cannot be sent to Telegram or back to the Messages API.
            raise LLMError("the model returned no text")
        return LLMResponse(
            text, response.model, response.usage.input_tokens, response.usage.output_tokens
        )
