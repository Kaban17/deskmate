# Deskmate Stage 1 (Core) Design

Date: 2026-11-12

## Purpose

Deskmate is a portfolio project for a job application: an AI helpdesk agent for
internal company services. It is built in three stages, each with its own spec
and plan:

1. **Core** (this spec): FastAPI, Postgres, Redis, basic LLM chat, Telegram bot.
2. **Intelligence**: RAG on Qdrant, request router, tool-calling agent.
3. **Differentiators**: MCP server, evals, security tests, CI, README.

Stage 1 is done when a user can hold a multi-turn Russian conversation with the
bot in Telegram and through `POST /chat`, with history stored in Postgres, and
the whole stack starts with `docker compose up`.

## Decisions agreed with the owner

- The repo owner must be able to defend every design choice in an interview, so
  work lands in small commits, each with a note in `docs/notes/` explaining why
  it is built that way and what an interviewer might ask.
- LLM: Claude through a thin provider interface. Tests use a fake client.
- Language: the bot answers in Russian. Code, comments and docs are in English.
- The Telegram bot runs inside the FastAPI process on a webhook.

## Architecture

One `app` container runs FastAPI with an aiogram dispatcher inside it. Compose
also runs `postgres` and `redis`. The app is a single process; per-user
ordering relies on that (see Concurrency).

**Bot modes**, set by `BOT_MODE`:

| Mode | Behaviour |
|---|---|
| `webhook` | On startup the app registers `WEBHOOK_BASE_URL/telegram/webhook` with Telegram, passing `WEBHOOK_SECRET`. |
| `polling` | The dispatcher runs long polling as a background task in the FastAPI lifespan. For local development without a public URL. |
| `off` | No bot. For tests and API-only runs. |

Handlers are identical in all modes.

**Layers**

- Telegram handlers are thin: extract user and text, call the chat service, send the reply.
- `POST /chat` calls the same chat service.
- The chat service holds all logic. Stage 2 plugs the router, RAG and agent in here.
- The LLM client is an interface with a Claude implementation and a fake.
- Postgres is the permanent record. Redis caches the recent window and LLM answers.

**Message flow**

1. An update arrives by webhook or polling and reaches a handler.
2. The chat service finds or creates the user and the active dialog.
3. It reads the recent window from Redis, falling back to Postgres.
4. It saves the user message, then calls the LLM with the system prompt and the window.
5. It saves the assistant message, refreshes Redis and returns the answer.

**Webhook route**: `POST /telegram/webhook` rejects requests whose
`X-Telegram-Bot-Api-Secret-Token` header differs from `WEBHOOK_SECRET` with 403.
It drops updates whose `update_id` was already seen, starts processing in a
background task, and returns 200 immediately so Telegram does not retry while
the LLM is working.

## Data model

| Table | Columns | Notes |
|---|---|---|
| `users` | `id`, `external_id` (unique), `full_name` (nullable), `role`, `created_at` | `external_id` is `tg:<telegram id>` or `api:<name>`. `role` is `employee` or `admin`, default `employee`; unused until stage 2. |
| `dialogs` | `id`, `user_id`, `status`, `started_at`, `last_message_at` | `status` is `active` or `closed`. A partial unique index on `user_id` where `status = 'active'` enforces one active dialog per user. |
| `messages` | `id`, `dialog_id`, `role`, `content`, `created_at`, `model`, `prompt_version`, `input_tokens`, `output_tokens`, `latency_ms` | `role` is `user` or `assistant`. The last five are nullable and set only on assistant messages. Token counts are null for cached answers. |

A dialog closes after 30 minutes without messages or when the user sends
`/new`. The next message opens a new one.

One Alembic migration creates all three tables.

## Redis keys

| Key | Value | TTL |
|---|---|---|
| `ctx:<dialog_id>` | JSON list of the last 20 messages | 3600 s |
| `llmcache:<sha256>` | Cached answer text | 3600 s |
| `tg:update:<update_id>` | Marker written with `SET NX` | 3600 s |

The cache hash covers the model, the prompt version and the full message window.

The window sent to the LLM holds at most 20 messages and always starts with a
user message: after trimming to 20, leading assistant messages are dropped.

Redis is fail-open everywhere: if it is unreachable, context is rebuilt from
Postgres, the cache is skipped, deduplication treats every update as new, and a
warning is logged. No request fails because Redis is down.

## REST API

- `POST /chat`, header `X-API-Key`. Body `{"user_id": str, "text": str}`;
  `user_id` is 1 to 64 characters, `text` is 1 to 4000 characters after
  trimming. Response `{"answer", "dialog_id", "message_id", "cached"}`.
  - 401 for a missing or wrong key.
  - 422 for invalid input, including whitespace-only text.
  - 502 with `{"detail": "LLM unavailable"}` when the LLM call fails.
- `GET /health` returns `{"status", "postgres", "redis"}`. 200 with
  `status: "ok"` when both are up; 200 with `status: "degraded"` when only
  Redis is down; 503 when Postgres is down.

## Telegram behaviour

| Input | Reply |
|---|---|
| `/start` | `Здравствуйте! Я Deskmate — помощник по внутренним сервисам компании. Задайте вопрос, и я постараюсь помочь. Команда /new начинает новый диалог.` |
| `/new` | Closes the active dialog. `Начинаем новый диалог. О чём хотите спросить?` |
| Text up to 4000 characters | The LLM answer, split into messages of at most 4096 characters. |
| Text over 4000 characters | `Сообщение слишком длинное. Сократите его до 4000 символов, пожалуйста.` |
| Anything that is not text | `Я пока понимаю только текстовые сообщения.` |
| LLM or database failure | `Сервис временно недоступен. Попробуйте ещё раз через минуту.` |

## Error handling

- **LLM failure**: the SDK's built-in retries apply. If the call still fails,
  the user message stays saved, no assistant message is written, and the caller
  gets the 502 or the apology text.
- **Concurrency**: messages from the same user are processed one at a time
  through an in-process lock keyed by `external_id`. This is correct only while
  the app runs as one process, which this stage guarantees.
- **Duplicate webhook deliveries** are dropped by `update_id`.

## System prompt

Prompts are versioned files at `prompts/system/<version>.md`; `PROMPT_VERSION`
selects one and each assistant message records the version used. Version `v1`:

```
Ты — Deskmate, помощник сотрудников по внутренним сервисам компании.
Отвечай по-русски, кратко и по делу.
У тебя пока нет доступа к внутренним документам и системам компании.
Если вопрос требует знания правил, регламентов или данных компании, прямо скажи, что у тебя нет этой информации, и не придумывай её.
На общие вопросы отвечай как обычно.
```

## Configuration

| Variable | Default |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://deskmate:deskmate@localhost:5432/deskmate` |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `ANTHROPIC_API_KEY` | none |
| `LLM_MODEL` | `claude-sonnet-5-5` |
| `LLM_MAX_TOKENS` | `1024` |
| `PROMPT_VERSION` | `v1` |
| `API_KEY` | none, required |
| `BOT_MODE` | `off` |
| `TELEGRAM_BOT_TOKEN` | none, required unless `BOT_MODE=off` |
| `WEBHOOK_BASE_URL`, `WEBHOOK_SECRET` | none, required when `BOT_MODE=webhook` |
| `CONTEXT_WINDOW` | `20` |
| `CONTEXT_TTL_S`, `CACHE_TTL_S`, `DEDUPE_TTL_S` | `3600` |
| `DIALOG_IDLE_MINUTES` | `30` |

## Testing

- `pytest` with `pytest-asyncio`. No test calls the real LLM or Telegram.
- Postgres: a real instance from `testcontainers`, migrated with Alembic, so
  the migration is tested too.
- Redis: `fakeredis`. A stub that raises `ConnectionError` covers the fail-open paths.
- The chat service and REST API are tested through `FakeLLMClient`.
- Telegram handlers are tested as functions with mocked message objects.

## Tooling

Python 3.15, `uv`, `ruff`, `pytest`, Docker Compose.

## Out of scope for stage 1

RAG, routing, tools, tickets, MCP, evals, PII masking, tracing, CI, deployment.
