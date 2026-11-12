import contextlib
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from deskmate.llm.base import ChatMessage

logger = logging.getLogger(__name__)


class ContextStore:
    """Recent dialog messages per dialog, so the LLM prompt needs no database read."""

    def __init__(self, redis: Redis, window: int, ttl_s: int) -> None:
        self._redis = redis
        self._window = window
        self._ttl_s = ttl_s

    @staticmethod
    def _key(dialog_id: int) -> str:
        return f"ctx:{dialog_id}"

    async def get(self, dialog_id: int) -> list[ChatMessage] | None:
        try:
            raw = await self._redis.get(self._key(dialog_id))
        except RedisError:
            logger.warning("context get failed for dialog %s", dialog_id, exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return [ChatMessage(**item) for item in json.loads(raw)]
        except (ValueError, TypeError):
            # A corrupt or old-shape value is a miss: the window is rebuilt from Postgres.
            logger.warning("context value unreadable for dialog %s", dialog_id, exc_info=True)
            return None

    async def set(self, dialog_id: int, messages: list[ChatMessage]) -> None:
        recent = messages[-self._window :]
        payload = json.dumps(
            [{"role": m.role, "content": m.content} for m in recent], ensure_ascii=False
        )
        try:
            await self._redis.set(self._key(dialog_id), payload, ex=self._ttl_s)
        except RedisError:
            logger.warning("context set failed for dialog %s", dialog_id, exc_info=True)
            # An old window left in place would be one exchange behind Postgres, so drop it.
            # If Redis is down this fails too; the warning above already covers it.
            with contextlib.suppress(RedisError):
                await self._redis.delete(self._key(dialog_id))

    async def delete(self, dialog_id: int) -> None:
        try:
            await self._redis.delete(self._key(dialog_id))
        except RedisError:
            logger.warning("context delete failed for dialog %s", dialog_id, exc_info=True)
