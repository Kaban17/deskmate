# Deskmate

Deskmate is an AI helpdesk assistant for internal company services. Employees
ask questions in Telegram or through a REST API, and Deskmate answers in
Russian using Claude. Dialog history is stored in Postgres; Redis keeps the
recent context and caches answers.

## Roadmap

1. **Core** (this stage): FastAPI, Postgres, Redis, a basic LLM chat and a Telegram bot, all started with `docker compose up`.
2. **Intelligence**: RAG on Qdrant, a request router and a tool-calling agent.
3. **Differentiators**: an MCP server, evals, security tests and CI.

At this stage the assistant has no access to company documents. The system
prompt tells it to say so instead of inventing rules or numbers.

## Architecture (stage 1)

```mermaid
flowchart LR
    TG[Telegram] -->|webhook or polling| BOT[aiogram handlers]
    CLIENT[HTTP client] -->|POST /chat, X-API-Key| API[FastAPI routes]
    BOT --> SVC[ChatService]
    API --> SVC
    SVC --> PG[(Postgres: users, dialogs, messages)]
    SVC --> RD[(Redis: context window, LLM cache, update dedupe)]
    SVC --> LLM[LLMClient interface]
    LLM --> CL[Claude API]
    subgraph app container
        BOT
        API
        SVC
        LLM
    end
```

The bot runs inside the FastAPI process. Handlers are thin; all logic lives in
`ChatService`, which `POST /chat` shares. Redis is fail-open: if it is down,
context is rebuilt from Postgres and the request still succeeds. Messages from
one user are serialised by an in-process lock, so the app must run as a single
process.

## Quick start

Requires Docker with Compose.

```bash
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY and change API_KEY
docker compose up --build -d
docker compose ps        # postgres, redis and app should become healthy

curl -s localhost:8000/health
curl -s -X POST localhost:8000/chat \
  -H 'X-API-Key: <your API_KEY>' -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","text":"Привет! Кто ты?"}'
```

`docker compose` overrides `DATABASE_URL` and `REDIS_URL` with the service
hostnames, applies Alembic migrations, then starts uvicorn. Only port 8000 is
published. Data lives in the `pgdata` volume; `docker compose down -v` removes it.

### Telegram

Create a bot with @BotFather and put the token in `.env`.

- Local development: `BOT_MODE=polling` and `TELEGRAM_BOT_TOKEN=...`. No public URL needed.
- Production: `BOT_MODE=webhook`, `TELEGRAM_BOT_TOKEN`, `WEBHOOK_BASE_URL` (public HTTPS) and `WEBHOOK_SECRET`.

Restart with `docker compose up -d` after editing `.env`.

### First run checklist

These checks need real credentials and have not been run against a live LLM or
Telegram yet.

1. With a real `ANTHROPIC_API_KEY`: `POST /chat` with "Привет! Кто ты?" answers in Russian and names Deskmate.
2. A second call from the same `user_id`, "Сколько дней отпуска мне положено?", says the assistant has no access to company data and does not invent a number. Both responses carry the same `dialog_id`.
3. With `BOT_MODE=polling` and a bot token: `/start` shows the greeting; a two-turn conversation where the second question depends on the first keeps context; `/new` starts a new dialog; a sticker gets "Я пока понимаю только текстовые сообщения."

## API

- `POST /chat`: header `X-API-Key`, body `{"user_id": str, "text": str}`. Returns `{"answer", "dialog_id", "message_id", "cached"}`. 401 for a bad key, 422 for invalid input, 502 `{"detail": "LLM unavailable"}` when the LLM call fails (the user message stays saved).
- `GET /health`: `{"status", "postgres", "redis"}`. `ok`, `degraded` (Redis down) or 503 (Postgres down).

## Configuration

Set in `.env` (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://deskmate:deskmate@localhost:5432/deskmate` | Overridden by Compose |
| `REDIS_URL` | `redis://localhost:6379/0` | Overridden by Compose |
| `ANTHROPIC_API_KEY` | none | Required unless an LLM client is injected |
| `LLM_MODEL` | `claude-sonnet-5-5` | |
| `LLM_MAX_TOKENS` | `1024` | |
| `PROMPT_VERSION` | `v1` | Selects `prompts/system/<version>.md` |
| `API_KEY` | none | Required; a blank value is rejected at startup |
| `BOT_MODE` | `off` | `off`, `polling` or `webhook` |
| `TELEGRAM_BOT_TOKEN` | none | Required unless `BOT_MODE=off` |
| `WEBHOOK_BASE_URL`, `WEBHOOK_SECRET` | none | Required when `BOT_MODE=webhook` |
| `CONTEXT_WINDOW` | `20` | Messages sent to the LLM |
| `CONTEXT_TTL_S`, `CACHE_TTL_S`, `DEDUPE_TTL_S` | `3600` | Redis TTLs in seconds |
| `DIALOG_IDLE_MINUTES` | `30` | Idle time before a dialog closes |
| `PROMPTS_DIR` | repo `prompts/` | Set to `/app/prompts` in the image |

## Development

Python 3.15 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest -v
uv run ruff check . && uv run ruff format --check .
```

Tests never call the real LLM or Telegram. Postgres tests start a real
instance with testcontainers, so Docker must be running; Redis is faked.

The image is multi-stage: `pydantic-core` has no prebuilt wheel for Python 3.15,
so the build stage installs a Rust toolchain and compiles it, and the runtime
stage is `python:3.15-slim` without compilers, running as a non-root user.

## Design decisions

Each stage has a note with the reasoning and likely interview questions.

1. [Scaffold](docs/notes/01-scaffold.md)
2. [Database](docs/notes/02-database.md)
3. [LLM client](docs/notes/03-llm.md)
4. [Redis](docs/notes/04-redis.md)
5. [Chat service](docs/notes/05-chat-service.md)
6. [REST API](docs/notes/06-api.md)
7. [Telegram bot](docs/notes/07-telegram.md)
8. [Docker](docs/notes/08-docker.md)
