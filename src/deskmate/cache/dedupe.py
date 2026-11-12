import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class UpdateDeduper:
    """Drops Telegram updates that were already delivered (webhook retries)."""

    def __init__(self, redis: Redis, ttl_s: int) -> None:
        self._redis = redis
        self._ttl_s = ttl_s

    async def first_seen(self, update_id: int) -> bool:
        try:
            created = await self._redis.set(f"tg:update:{update_id}", 1, nx=True, ex=self._ttl_s)
        except RedisError:
            logger.warning("update dedupe failed for update %s", update_id, exc_info=True)
            return True
        return created is not None
