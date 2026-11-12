from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx2 as httpx
import pytest

from deskmate.llm.base import ChatMessage, LLMError, LLMResponse
from deskmate.llm.claude import ClaudeClient
from deskmate.llm.fake import FakeLLMClient
from deskmate.prompts import load_prompt


def stub_sdk(text_blocks=(), input_tokens=0, output_tokens=0, model="m", raises=None):
    sdk = MagicMock()
    if raises is not None:
        sdk.messages.create = AsyncMock(side_effect=raises)
        return sdk
    content = []
    for text in text_blocks:
        block = MagicMock()
        block.type = "text"
        block.text = text
        content.append(block)
    response = MagicMock()
    response.content = content
    response.model = model
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    sdk.messages.create = AsyncMock(return_value=response)
    return sdk


async def test_fake_records_calls_and_replies():
    llm = FakeLLMClient(reply="привет")
    r = await llm.complete("sys", [ChatMessage("user", "hi")])
    assert r.text == "привет" and llm.calls == [("sys", [ChatMessage("user", "hi")])]


async def test_fake_can_fail():
    with pytest.raises(LLMError):
        await FakeLLMClient(fail=True).complete("s", [ChatMessage("user", "x")])


async def test_fake_callable_reply_and_mutable_attributes():
    llm = FakeLLMClient()
    llm.reply = lambda messages: f"n={len(messages)}"
    r = await llm.complete("s", [ChatMessage("user", "a"), ChatMessage("assistant", "b")])
    assert r.text == "n=2"
    llm.fail = True
    with pytest.raises(LLMError):
        await llm.complete("s", [ChatMessage("user", "x")])


async def test_claude_client_maps_request_and_response():
    sdk = stub_sdk(
        text_blocks=["Здрав", "ствуйте"],
        input_tokens=12,
        output_tokens=7,
        model="claude-sonnet-5-5",
    )
    r = await ClaudeClient("k", "claude-sonnet-5-5", 1024, client=sdk).complete(
        "sys", [ChatMessage("user", "hi")]
    )
    assert r == LLMResponse("Здравствуйте", "claude-sonnet-5-5", 12, 7)
    assert sdk.messages.create.await_args.kwargs == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 1024,
        "system": "sys",
        "messages": [{"role": "user", "content": "hi"}],
    }


async def test_claude_client_wraps_sdk_errors():
    sdk = stub_sdk(raises=anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")))
    with pytest.raises(LLMError):
        await ClaudeClient("k", "m", 10, client=sdk).complete("s", [ChatMessage("user", "x")])


def test_load_prompt_v1_is_the_spec_text():
    text = load_prompt("v1")
    assert text.startswith("Ты — Deskmate") and "не придумывай" in text


def test_load_prompt_unknown_version():
    with pytest.raises(FileNotFoundError):
        load_prompt("v999")
