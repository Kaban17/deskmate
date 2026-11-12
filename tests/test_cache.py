import logging

from deskmate.cache.context import ContextStore
from deskmate.cache.dedupe import UpdateDeduper
from deskmate.cache.llm_cache import LLMCache
from deskmate.llm.base import ChatMessage

MSGS = [ChatMessage("user", "вопрос"), ChatMessage("assistant", "ответ")]


async def test_context_roundtrip_and_ttl(redis):
    store = ContextStore(redis, window=20, ttl_s=3600)
    assert await store.get(1) is None
    await store.set(1, MSGS)
    assert await store.get(1) == MSGS
    assert 0 < await redis.ttl("ctx:1") <= 3600


async def test_context_keeps_only_the_window(redis):
    store = ContextStore(redis, window=2, ttl_s=60)
    await store.set(1, [ChatMessage("user", str(i)) for i in range(5)])
    assert [m.content for m in await store.get(1)] == ["3", "4"]


async def test_context_delete(redis):
    store = ContextStore(redis, 20, 60)
    await store.set(1, MSGS)
    await store.delete(1)
    assert await store.get(1) is None


def test_cache_key_depends_on_model_version_and_messages():
    k = LLMCache.key("m", "v1", MSGS)
    assert k.startswith("llmcache:") and k == LLMCache.key("m", "v1", list(MSGS))
    assert (
        len(
            {
                k,
                LLMCache.key("m2", "v1", MSGS),
                LLMCache.key("m", "v2", MSGS),
                LLMCache.key("m", "v1", MSGS[:1]),
            }
        )
        == 4
    )


async def test_cache_roundtrip(redis):
    cache = LLMCache(redis, 3600)
    k = LLMCache.key("m", "v1", MSGS)
    assert await cache.get(k) is None
    await cache.set(k, "ответ")
    assert await cache.get(k) == "ответ"


async def test_dedupe(redis):
    d = UpdateDeduper(redis, 3600)
    assert await d.first_seen(7) is True and await d.first_seen(7) is False


async def test_everything_fails_open(broken_redis, caplog):
    store, cache, d = (
        ContextStore(broken_redis, 20, 60),
        LLMCache(broken_redis, 60),
        UpdateDeduper(broken_redis, 60),
    )
    with caplog.at_level(logging.WARNING, logger="deskmate.cache"):
        await store.set(1, MSGS)
        await store.delete(1)
        await cache.set("k", "v")
        assert (
            await store.get(1) is None
            and await cache.get("k") is None
            and await d.first_seen(1) is True
        )
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name.startswith("deskmate.cache")
    ]
    assert len(warnings) == 6
