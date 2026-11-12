import asyncio
import logging

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

router = APIRouter()
logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 2.0


async def _postgres_ok(engine) -> bool:
    async def probe() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(probe(), CHECK_TIMEOUT_S)
    except Exception:
        logger.warning("postgres health check failed", exc_info=True)
        return False
    return True


async def _redis_ok(redis) -> bool:
    try:
        await asyncio.wait_for(redis.ping(), CHECK_TIMEOUT_S)
    except Exception:
        logger.warning("redis health check failed", exc_info=True)
        return False
    return True


@router.get("/health")
async def health(request: Request, response: Response) -> dict:
    postgres, redis = await asyncio.gather(
        _postgres_ok(request.app.state.engine), _redis_ok(request.app.state.redis)
    )
    if not postgres:
        response.status_code = 503
    return {
        "status": "ok" if postgres and redis else "degraded",
        "postgres": postgres,
        "redis": redis,
    }
