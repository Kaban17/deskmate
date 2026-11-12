import hashlib
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from deskmate.llm.base import ChatMessage

logger = logging.getLogger(__name__)


class LLMCache:
    """Response cache keyed by model, prompt version and the full message list."""

    def __init__(self, redis: Redis, ttl_s: int) -> None:
        self._redis = redis
        self._ttl_s = ttl_s

    @staticmethod
    def key(model: str, prompt_version: str, messages: list[ChatMessage]) -> str:
        canonical = json.dumps(
            {
                "model": model,
                "prompt_version": prompt_version,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "llmcache:" + hashlib.sha256(canonical.encode()).hexdigest()

    async def get(self, key: str) -> str | None:
        try:
            return await self._redis.get(key)
        except RedisError:
            logger.warning("llm cache get failed", exc_info=True)
            return None

    async def set(self, key: str, text: str) -> None:
        try:
            await self._redis.set(key, text, ex=self._ttl_s)
        except RedisError:
            logger.warning("llm cache set failed", exc_info=True)
