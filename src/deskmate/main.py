from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from deskmate.api import chat, health
from deskmate.bot.setup import build_bot, build_dispatcher, start_bot, stop_bot, webhook_router
from deskmate.cache.context import ContextStore
from deskmate.cache.dedupe import UpdateDeduper
from deskmate.cache.llm_cache import LLMCache
from deskmate.chat.service import ChatService
from deskmate.config import Settings, get_settings
from deskmate.db.session import make_engine, make_session_factory
from deskmate.llm.base import LLMClient
from deskmate.llm.claude import ClaudeClient


def build_redis(url: str) -> Redis:
    # The cache stores use the client's return values as str, so responses must be decoded.
    return Redis.from_url(url, decode_responses=True)


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    redis: Redis | None = None,
    engine: AsyncEngine | None = None,
    bot: Bot | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or get_settings()
        if llm is None and not cfg.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required when no LLM client is injected")

        # Only resources created here are disposed here; injected ones belong to the caller.
        own_engine = engine is None
        own_redis = redis is None
        db_engine = engine if engine is not None else make_engine(cfg.database_url)
        redis_client = redis if redis is not None else build_redis(cfg.redis_url)
        try:
            llm_client = (
                llm
                if llm is not None
                else ClaudeClient(cfg.anthropic_api_key, cfg.llm_model, cfg.llm_max_tokens)
            )
            app.state.settings = cfg
            app.state.engine = db_engine
            app.state.redis = redis_client
            app.state.chat_service = ChatService(
                make_session_factory(db_engine),
                llm_client,
                ContextStore(redis_client, cfg.context_window, cfg.context_ttl_s),
                LLMCache(redis_client, cfg.cache_ttl_s),
                cfg,
            )
            bot_started = False
            if cfg.bot_mode != "off":
                service = app.state.chat_service
                if bot is None:
                    app.state.bot, app.state.dp = build_bot(cfg, service)
                    app.state.bot_owned = True
                else:
                    app.state.bot, app.state.dp = bot, build_dispatcher(service)
                    app.state.bot_owned = False
                if cfg.bot_mode == "webhook":
                    deduper = UpdateDeduper(redis_client, cfg.dedupe_ttl_s)
                    app.include_router(
                        webhook_router(
                            app.state.bot, app.state.dp, deduper, cfg.webhook_secret or ""
                        )
                    )
                try:
                    await start_bot(app, cfg)
                except BaseException:
                    await stop_bot(app)
                    raise
                bot_started = True
            try:
                yield
            finally:
                if bot_started:
                    await stop_bot(app)
        finally:
            if own_redis:
                await redis_client.aclose()
            if own_engine:
                await db_engine.dispose()

    app = FastAPI(title="Deskmate", lifespan=lifespan)
    app.include_router(chat.router)
    app.include_router(health.router)
    return app
