# Deskmate Stage 1 (Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Russian-language chat assistant reachable through Telegram and `POST /chat`, with dialog history in Postgres, context and caching in Redis, started by `docker compose up`.

**Architecture:** One FastAPI process hosts the REST API and an aiogram dispatcher (webhook, polling or off). Both call a single `ChatService`, which owns dialog handling, context, caching and the LLM call. Postgres is the record; Redis is a fail-open cache.

**Tech Stack:** Python 3.15, uv, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.0 async + asyncpg, Alembic, redis-py (asyncio), aiogram 3, anthropic SDK, pytest, pytest-asyncio, testcontainers, fakeredis, httpx, ruff, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-12-deskmate-core-design.md`

## Global Constraints

- Python 3.15, pinned with `uv python pin 3.15`; `requires-python = ">=3.15"`; the Docker image uses a Python 3.15 base. If a dependency has no 3.15 wheel and fails to build, stop and report it to the owner instead of changing the Python version.
- All I/O is async. No sync database or Redis calls.
- No test calls the real LLM or Telegram.
- Redis is fail-open: a Redis error never fails a request; it logs a warning.
- User-facing bot text is Russian and lives only in `src/deskmate/bot/texts.py` and `prompts/`. Code, comments, commit messages and docs are English.
- Text limit for a user message: 4000 characters after trimming. Telegram message limit: 4096.
- Every task ends with a note at `docs/notes/NN-<topic>.md` with two headings: `## Why it is built this way` and `## What an interviewer might ask` (three to five questions with short answers). The note is committed with the task.
- Commit messages carry no `Co-Authored-By` or other attribution trailer.
- Before writing code that calls the Anthropic SDK (Task 3), load the `claude-api` skill and follow it for the client call and model id.

## Review Focus

1. Two messages from the same user arrive at the same moment: both are answered, in arrival order, inside one dialog. Test in Task 5.
2. Redis is unreachable: the user still gets an answer and history is still saved. Tests in Tasks 4 and 5.
3. Telegram delivers the same update twice: the user gets one reply. Test in Task 7.
4. The LLM answer is longer than 4096 characters: it arrives as several messages, none over the limit, nothing lost. Test in Task 7.
5. The message is whitespace-only, over 4000 characters, or not text (photo, sticker): the user gets a clear reply or a 422, and the LLM is not called. Tests in Tasks 6 and 7.

## File Structure

```
pyproject.toml  .python-version  .gitignore  .env.example
Dockerfile  docker-compose.yml  README.md
alembic.ini
alembic/env.py
alembic/versions/0001_initial.py
prompts/system/v1.md
src/deskmate/
  config.py            Settings
  main.py              create_app(), lifespan
  prompts.py           load_prompt()
  db/session.py        engine and session factory
  db/models.py         User, Dialog, Message
  db/repo.py           queries
  llm/base.py          ChatMessage, LLMResponse, LLMClient, LLMError
  llm/fake.py          FakeLLMClient
  llm/claude.py        ClaudeClient
  cache/context.py     ContextStore
  cache/llm_cache.py   LLMCache
  cache/dedupe.py      UpdateDeduper
  chat/service.py      ChatService, ChatResult
  api/deps.py          dependencies, API key check
  api/chat.py          POST /chat
  api/health.py        GET /health
  bot/texts.py         Russian reply strings
  bot/handlers.py      aiogram router, split_reply()
  bot/setup.py         build_bot(), webhook route, start/stop
tests/
  conftest.py          postgres, redis, settings, service fixtures
  test_config.py  test_repo.py  test_llm.py  test_cache.py
  test_chat_service.py  test_api.py  test_bot.py
docs/notes/
```

---

### Task 1: Project scaffold and settings

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`, `src/deskmate/__init__.py`, `src/deskmate/config.py`, `tests/test_config.py`, `docs/notes/01-scaffold.md`

**Interfaces:**
- Produces: `deskmate.config.Settings` (pydantic-settings `BaseSettings`, reads `.env`) with fields `database_url`, `redis_url`, `anthropic_api_key: str | None`, `llm_model`, `llm_max_tokens`, `prompt_version`, `api_key`, `bot_mode: Literal["off", "polling", "webhook"]`, `telegram_bot_token: str | None`, `webhook_base_url: str | None`, `webhook_secret: str | None`, `context_window`, `context_ttl_s`, `cache_ttl_s`, `dedupe_ttl_s`, `dialog_idle_minutes`. Defaults are the spec's Configuration table. `get_settings() -> Settings` is `lru_cache`d.

- [ ] **Step 1: Initialise the project**

```bash
git init && git checkout -b main
uv init --package --name deskmate --python 3.15 && uv python pin 3.15
uv add fastapi "uvicorn[standard]" pydantic-settings "sqlalchemy[asyncio]" asyncpg alembic redis aiogram anthropic
uv add --dev pytest pytest-asyncio httpx "testcontainers[postgres]" fakeredis ruff
```

In `pyproject.toml` set `[tool.pytest.ini_options] asyncio_mode = "auto"` and `[tool.ruff] line-length = 100`. `.gitignore` covers `.env`, `.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`.

- [ ] **Step 2: Write the failing tests** in `tests/test_config.py`

```python
def test_defaults(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    s = Settings(_env_file=None)
    assert s.llm_model == "claude-sonnet-5-5"
    assert s.bot_mode == "off"
    assert (s.context_window, s.context_ttl_s, s.dialog_idle_minutes) == (20, 3600, 30)

def test_api_key_required(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

@pytest.mark.parametrize("mode,missing", [("polling", "TELEGRAM_BOT_TOKEN"), ("webhook", "WEBHOOK_SECRET")])
def test_bot_mode_requires_its_settings(monkeypatch, mode, missing):
    for k, v in {"API_KEY": "k", "BOT_MODE": mode, "TELEGRAM_BOT_TOKEN": "t",
                 "WEBHOOK_BASE_URL": "https://x", "WEBHOOK_SECRET": "s"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 3: Run** `uv run pytest tests/test_config.py -v`. Expected: FAIL, `deskmate.config` not found.
- [ ] **Step 4: Implement `Settings` and `get_settings()`** in `src/deskmate/config.py`. The mode checks are a `model_validator(mode="after")`.
- [ ] **Step 5: Write `.env.example`** listing every variable from the spec's Configuration table with its default.
- [ ] **Step 6: Run** `uv run pytest -v && uv run ruff check .`. Expected: 4 passed, no lint errors.
- [ ] **Step 7: Write `docs/notes/01-scaffold.md`** (uv, src layout, settings from env, fail-fast validation) and commit everything, including `docs/superpowers/`, as `chore: project scaffold and settings`.

---

### Task 2: Database models, migration and repository

**Files:**
- Create: `src/deskmate/db/__init__.py`, `src/deskmate/db/session.py`, `src/deskmate/db/models.py`, `src/deskmate/db/repo.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `tests/conftest.py`, `tests/test_repo.py`, `docs/notes/02-database.md`

**Interfaces:**
- Consumes: `Settings.database_url`.
- Produces:
  - `db.session.make_engine(url: str) -> AsyncEngine`, `make_session_factory(engine) -> async_sessionmaker[AsyncSession]` (`expire_on_commit=False`).
  - `db.models`: `Base`, `User`, `Dialog`, `Message` with the columns in the spec's Data model table. `role` and `status` are plain strings with check constraints. Timestamps are timezone-aware.
  - `db.repo`:
    - `get_or_create_user(session, external_id: str, full_name: str | None = None) -> User`
    - `get_or_create_active_dialog(session, user_id: int, now: datetime, idle: timedelta) -> Dialog` closes the active dialog if `now - last_message_at > idle`, then creates a new one.
    - `close_active_dialog(session, user_id: int) -> int | None` returns the closed dialog id.
    - `add_message(session, dialog_id: int, role: str, content: str, *, model=None, prompt_version=None, input_tokens=None, output_tokens=None, latency_ms=None) -> Message` also sets the dialog's `last_message_at`.
    - `recent_messages(session, dialog_id: int, limit: int) -> list[Message]` returns the newest `limit` messages, oldest first.
  - Repo functions flush but do not commit; the caller owns the transaction.
  - `tests/conftest.py` fixtures: `engine` (session scope: starts a `PostgresContainer("postgres:17")`, rewrites its URL to the `asyncpg` driver, runs `alembic upgrade head` against it), `session_factory`, `session`, and an autouse fixture that truncates all three tables with `RESTART IDENTITY CASCADE` after each test.

- [ ] **Step 1: Write the failing tests** in `tests/test_repo.py`

```python
async def test_user_is_created_once(session):
    a = await repo.get_or_create_user(session, "tg:1", "Анна")
    b = await repo.get_or_create_user(session, "tg:1")
    assert a.id == b.id and a.role == "employee"

async def test_active_dialog_is_reused_within_idle_window(session, user):
    d1 = await repo.get_or_create_active_dialog(session, user.id, T0, IDLE)
    d2 = await repo.get_or_create_active_dialog(session, user.id, T0 + timedelta(minutes=29), IDLE)
    assert d1.id == d2.id

async def test_idle_dialog_is_closed_and_replaced(session, user):
    d1 = await repo.get_or_create_active_dialog(session, user.id, T0, IDLE)
    d2 = await repo.get_or_create_active_dialog(session, user.id, T0 + timedelta(minutes=31), IDLE)
    assert d2.id != d1.id and d1.status == "closed" and d2.status == "active"

async def test_second_active_dialog_is_rejected_by_the_database(session, user):
    session.add_all([Dialog(user_id=user.id, status="active"), Dialog(user_id=user.id, status="active")])
    with pytest.raises(IntegrityError):
        await session.flush()

async def test_close_active_dialog(session, user):
    d = await repo.get_or_create_active_dialog(session, user.id, T0, IDLE)
    assert await repo.close_active_dialog(session, user.id) == d.id
    assert await repo.close_active_dialog(session, user.id) is None

async def test_recent_messages_returns_newest_oldest_first(session, dialog):
    for i in range(5):
        await repo.add_message(session, dialog.id, "user", f"m{i}")
    assert [m.content for m in await repo.recent_messages(session, dialog.id, 3)] == ["m2", "m3", "m4"]

async def test_assistant_metadata_is_stored(session, dialog):
    m = await repo.add_message(session, dialog.id, "assistant", "ответ", model="m", prompt_version="v1",
                               input_tokens=10, output_tokens=5, latency_ms=120)
    assert (m.model, m.prompt_version, m.input_tokens, m.output_tokens, m.latency_ms) == ("m", "v1", 10, 5, 120)
```

`T0` is a fixed aware datetime, `IDLE = timedelta(minutes=30)`; `user` and `dialog` are local fixtures. `last_message_at` starts equal to `started_at`.

- [ ] **Step 2: Run** `uv run pytest tests/test_repo.py -v`. Expected: FAIL on import.
- [ ] **Step 3: Implement `db/session.py` and `db/models.py`.** The one-active-dialog rule is `Index("uq_dialogs_one_active", "user_id", unique=True, postgresql_where=text("status = 'active'"))`.
- [ ] **Step 4: Set up Alembic.** `uv run alembic init -t async alembic`; `env.py` takes the URL from `get_settings().database_url` unless `sqlalchemy.url` is already set in the config (the test fixture sets it) and uses `Base.metadata`. Write `0001_initial.py` by hand, not autogenerate, so the partial index and check constraints are explicit.
- [ ] **Step 5: Implement `db/repo.py`.**
- [ ] **Step 6: Run** `uv run pytest tests/test_repo.py -v`. Expected: 7 passed. Docker must be running.
- [ ] **Step 7: Write `docs/notes/02-database.md`** (why Postgres is the record, the partial unique index, hand-written migration, real Postgres in tests instead of SQLite) and commit as `feat: database models, migration and repository`.

---

### Task 3: LLM client and versioned prompts

**Files:**
- Create: `src/deskmate/llm/__init__.py`, `src/deskmate/llm/base.py`, `src/deskmate/llm/fake.py`, `src/deskmate/llm/claude.py`, `src/deskmate/prompts.py`, `prompts/system/v1.md`, `tests/test_llm.py`, `docs/notes/03-llm.md`

**Interfaces:**
- Consumes: `Settings.anthropic_api_key`, `llm_model`, `llm_max_tokens`.
- Produces:
  - `llm.base`: `@dataclass(frozen=True) ChatMessage(role: Literal["user", "assistant"], content: str)`; `@dataclass(frozen=True) LLMResponse(text: str, model: str, input_tokens: int, output_tokens: int)`; `class LLMError(Exception)`; `class LLMClient(Protocol)` with `async def complete(self, system: str, messages: list[ChatMessage]) -> LLMResponse`.
  - `llm.fake.FakeLLMClient(reply: str = "ответ", fail: bool = False, delay: float = 0.0)`: appends `(system, messages)` to `self.calls`, sleeps `delay`, raises `LLMError` if `fail`, otherwise returns `LLMResponse(reply, "fake", 1, 1)`. `reply` may also be a callable taking the messages.
  - `llm.claude.ClaudeClient(api_key: str, model: str, max_tokens: int, client: AsyncAnthropic | None = None)`: implements `LLMClient`; wraps every `anthropic.APIError` in `LLMError`; returns the concatenated text blocks.
  - `prompts.load_prompt(version: str, root: Path = PROMPTS_DIR) -> str`: reads `<root>/system/<version>.md`, stripped; raises `FileNotFoundError` for an unknown version. `PROMPTS_DIR` is the repo-level `prompts/` directory, overridable by the `PROMPTS_DIR` environment variable (the Docker image sets it).

- [ ] **Step 1: Load the `claude-api` skill** and confirm the async Messages call, the response shape and the model id against it.
- [ ] **Step 2: Write the failing tests** in `tests/test_llm.py`

```python
async def test_fake_records_calls_and_replies():
    llm = FakeLLMClient(reply="привет")
    r = await llm.complete("sys", [ChatMessage("user", "hi")])
    assert r.text == "привет" and llm.calls == [("sys", [ChatMessage("user", "hi")])]

async def test_fake_can_fail():
    with pytest.raises(LLMError):
        await FakeLLMClient(fail=True).complete("s", [ChatMessage("user", "x")])

async def test_claude_client_maps_request_and_response():
    sdk = stub_sdk(text_blocks=["Здрав", "ствуйте"], input_tokens=12, output_tokens=7, model="claude-sonnet-5-5")
    r = await ClaudeClient("k", "claude-sonnet-5-5", 1024, client=sdk).complete("sys", [ChatMessage("user", "hi")])
    assert r == LLMResponse("Здравствуйте", "claude-sonnet-5-5", 12, 7)
    assert sdk.messages.create.await_args.kwargs == {
        "model": "claude-sonnet-5-5", "max_tokens": 1024, "system": "sys",
        "messages": [{"role": "user", "content": "hi"}]}

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
```

`stub_sdk` is a test helper building a `MagicMock` whose `messages.create` is an `AsyncMock`. If the skill shows a different request shape than the kwargs above, change the test to match the skill.

- [ ] **Step 3: Run** `uv run pytest tests/test_llm.py -v`. Expected: FAIL on import.
- [ ] **Step 4: Write `prompts/system/v1.md`** with the exact text from the spec's System prompt section.
- [ ] **Step 5: Implement `llm/base.py`, `llm/fake.py`, `llm/claude.py`, `prompts.py`.**
- [ ] **Step 6: Run** `uv run pytest tests/test_llm.py -v`. Expected: 6 passed.
- [ ] **Step 7: Write `docs/notes/03-llm.md`** (why an interface, why a fake rather than mocking the SDK everywhere, why prompts are versioned files) and commit as `feat: LLM client interface and versioned prompts`.

---

### Task 4: Redis stores

**Files:**
- Create: `src/deskmate/cache/__init__.py`, `src/deskmate/cache/context.py`, `src/deskmate/cache/llm_cache.py`, `src/deskmate/cache/dedupe.py`, `tests/test_cache.py`, `docs/notes/04-redis.md`
- Modify: `tests/conftest.py` (add `redis` fixture returning `fakeredis.aioredis.FakeRedis(decode_responses=True)` and `broken_redis`, an object whose every method raises `redis.exceptions.ConnectionError`)

**Interfaces:**
- Consumes: `ChatMessage`.
- Produces (every method catches `redis.exceptions.RedisError`, logs a warning and returns the fail-open value):
  - `ContextStore(redis, window: int, ttl_s: int)`: `async get(dialog_id: int) -> list[ChatMessage] | None` (None on miss or error); `async set(dialog_id: int, messages: list[ChatMessage]) -> None` stores only the last `window` messages as one JSON string under `ctx:<dialog_id>` with the TTL; `async delete(dialog_id: int) -> None`.
  - `LLMCache(redis, ttl_s: int)`: `@staticmethod key(model: str, prompt_version: str, messages: list[ChatMessage]) -> str` returns `llmcache:<sha256 hex>` over a canonical JSON of all three; `async get(key: str) -> str | None`; `async set(key: str, text: str) -> None`.
  - `UpdateDeduper(redis, ttl_s: int)`: `async first_seen(update_id: int) -> bool` uses `SET tg:update:<id> 1 NX EX ttl`; returns True on error.

- [ ] **Step 1: Write the failing tests** in `tests/test_cache.py`

```python
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
    await store.set(1, MSGS); await store.delete(1)
    assert await store.get(1) is None

def test_cache_key_depends_on_model_version_and_messages():
    k = LLMCache.key("m", "v1", MSGS)
    assert k.startswith("llmcache:") and k == LLMCache.key("m", "v1", list(MSGS))
    assert len({k, LLMCache.key("m2", "v1", MSGS), LLMCache.key("m", "v2", MSGS), LLMCache.key("m", "v1", MSGS[:1])}) == 4

async def test_cache_roundtrip(redis):
    cache = LLMCache(redis, 3600); k = LLMCache.key("m", "v1", MSGS)
    assert await cache.get(k) is None
    await cache.set(k, "ответ")
    assert await cache.get(k) == "ответ"

async def test_dedupe(redis):
    d = UpdateDeduper(redis, 3600)
    assert await d.first_seen(7) is True and await d.first_seen(7) is False

async def test_everything_fails_open(broken_redis):
    store, cache, d = ContextStore(broken_redis, 20, 60), LLMCache(broken_redis, 60), UpdateDeduper(broken_redis, 60)
    await store.set(1, MSGS); await store.delete(1); await cache.set("k", "v")
    assert await store.get(1) is None and await cache.get("k") is None and await d.first_seen(1) is True
```

- [ ] **Step 2: Run** `uv run pytest tests/test_cache.py -v`. Expected: FAIL on import.
- [ ] **Step 3: Implement the three stores.**
- [ ] **Step 4: Run** `uv run pytest tests/test_cache.py -v`. Expected: 7 passed.
- [ ] **Step 5: Write `docs/notes/04-redis.md`** (cache versus record, why fail-open, what the response cache does and does not hit, `SET NX` for idempotency) and commit as `feat: Redis context, cache and dedupe stores`.

---

### Task 5: Chat service

**Files:**
- Create: `src/deskmate/chat/__init__.py`, `src/deskmate/chat/service.py`, `tests/test_chat_service.py`, `docs/notes/05-chat-service.md`
- Modify: `tests/conftest.py` (add `settings` and `make_service(llm=None, redis=None)` fixtures)

**Interfaces:**
- Consumes: `repo.*`, `LLMClient`, `LLMError`, `ChatMessage`, `ContextStore`, `LLMCache`, `load_prompt`, `Settings`.
- Produces:
  - `@dataclass(frozen=True) ChatResult(text: str, dialog_id: int, message_id: int, cached: bool)`
  - `ChatService(session_factory, llm: LLMClient, context: ContextStore, cache: LLMCache, settings: Settings, clock: Callable[[], datetime] = utcnow)`
    - `async handle_message(external_id: str, text: str, full_name: str | None = None) -> ChatResult`. Raises `LLMError` when the LLM fails.
    - `async reset_dialog(external_id: str) -> None` closes the active dialog and deletes its context key.
  - `text` is assumed already trimmed and within limits; callers validate.

**Behaviour of `handle_message`, in order, under a per-`external_id` `asyncio.Lock`:**
1. Transaction 1: get or create the user and active dialog, save the user message, commit. This is what keeps the user message when the LLM fails.
2. Build the window: `context.get`, or `recent_messages(limit=context_window)` on a miss; make sure it ends with the new user message and holds at most `context_window` messages. After capping, drop leading assistant messages so the window always starts with a user message (the Messages API requires it).
3. Look up `LLMCache.key(llm_model, prompt_version, window)`. On a hit, use it with `cached=True` and null token counts; otherwise call `llm.complete(load_prompt(prompt_version), window)` and time it.
4. Transaction 2: save the assistant message with model, prompt version, tokens and latency; commit.
5. `context.set` with the window plus the answer; `cache.set` on a miss.

- [ ] **Step 1: Write the failing tests** in `tests/test_chat_service.py`

```python
async def test_first_message_creates_user_dialog_and_both_messages(make_service, session):
    llm = FakeLLMClient(reply="Здравствуйте")
    r = await make_service(llm).handle_message("tg:1", "Привет", "Анна")
    assert r.text == "Здравствуйте" and r.cached is False
    rows = await repo.recent_messages(session, r.dialog_id, 10)
    assert [(m.role, m.content) for m in rows] == [("user", "Привет"), ("assistant", "Здравствуйте")]
    assert (rows[1].id, rows[1].model, rows[1].prompt_version) == (r.message_id, "fake", "v1")
    assert rows[1].latency_ms is not None

async def test_llm_receives_system_prompt_and_history(make_service):
    llm = FakeLLMClient(reply="ок"); svc = make_service(llm)
    await svc.handle_message("tg:1", "раз"); await svc.handle_message("tg:1", "два")
    system, messages = llm.calls[-1]
    assert system == load_prompt("v1")
    assert [(m.role, m.content) for m in messages] == [("user", "раз"), ("assistant", "ок"), ("user", "два")]

async def test_window_is_capped(make_service, settings):
    settings.context_window = 4
    llm = FakeLLMClient(reply=lambda msgs: "a" + msgs[-1].content[1:]); svc = make_service(llm)
    for i in range(5):
        await svc.handle_message("tg:1", f"q{i}")
    window = llm.calls[-1][1]
    assert [(m.role, m.content) for m in window] == [("user", "q3"), ("assistant", "a3"), ("user", "q4")]

async def test_history_survives_losing_redis_context(make_service, redis):
    llm = FakeLLMClient(reply="ок"); svc = make_service(llm, redis)
    r = await svc.handle_message("tg:1", "раз")
    await redis.delete(f"ctx:{r.dialog_id}")
    await svc.handle_message("tg:1", "два")
    assert [m.content for m in llm.calls[-1][1]] == ["раз", "ок", "два"]

async def test_works_with_redis_down(make_service, broken_redis, session):
    llm = FakeLLMClient(reply="ок"); svc = make_service(llm, broken_redis)
    await svc.handle_message("tg:1", "раз")
    r = await svc.handle_message("tg:1", "два")
    assert r.text == "ок" and [m.content for m in llm.calls[-1][1]] == ["раз", "ок", "два"]
    assert len(await repo.recent_messages(session, r.dialog_id, 10)) == 4

async def test_identical_first_question_is_served_from_cache(make_service, session):
    llm = FakeLLMClient(reply="ответ"); svc = make_service(llm)
    await svc.handle_message("tg:1", "Как оформить отпуск?")
    r = await svc.handle_message("tg:2", "Как оформить отпуск?")
    assert r.cached is True and r.text == "ответ" and len(llm.calls) == 1
    msg = (await repo.recent_messages(session, r.dialog_id, 10))[-1]
    assert msg.input_tokens is None and msg.output_tokens is None

async def test_llm_failure_keeps_user_message_and_raises(make_service, session_factory):
    svc = make_service(FakeLLMClient(fail=True))
    with pytest.raises(LLMError):
        await svc.handle_message("tg:1", "Привет")
    async with session_factory() as s:
        rows = (await s.scalars(select(Message))).all()
    assert [(m.role, m.content) for m in rows] == [("user", "Привет")]

async def test_reset_starts_a_new_dialog_with_empty_history(make_service):
    llm = FakeLLMClient(reply="ок"); svc = make_service(llm)
    r1 = await svc.handle_message("tg:1", "раз")
    await svc.reset_dialog("tg:1")
    r2 = await svc.handle_message("tg:1", "два")
    assert r2.dialog_id != r1.dialog_id and [m.content for m in llm.calls[-1][1]] == ["два"]

async def test_idle_dialog_is_replaced(make_service):
    now = [T0]; llm = FakeLLMClient(reply="ок"); svc = make_service(llm, clock=lambda: now[0])
    r1 = await svc.handle_message("tg:1", "раз")
    now[0] = T0 + timedelta(minutes=31)
    r2 = await svc.handle_message("tg:1", "два")
    assert r2.dialog_id != r1.dialog_id and len(llm.calls[-1][1]) == 1

async def test_concurrent_messages_from_one_user_are_serialised(make_service, session):
    llm = FakeLLMClient(reply=lambda msgs: f"ответ на {msgs[-1].content}", delay=0.05); svc = make_service(llm)
    a, b = await asyncio.gather(svc.handle_message("tg:1", "первый"), svc.handle_message("tg:1", "второй"))
    assert a.dialog_id == b.dialog_id
    rows = await repo.recent_messages(session, a.dialog_id, 10)
    assert [m.content for m in rows] == ["первый", "ответ на первый", "второй", "ответ на второй"]

async def test_different_users_are_not_blocked_by_each_other(make_service):
    svc = make_service(FakeLLMClient(reply="ок", delay=0.2))
    start = time.monotonic()
    await asyncio.gather(svc.handle_message("tg:1", "a"), svc.handle_message("tg:2", "b"))
    assert time.monotonic() - start < 0.35
```

`make_service` accepts `clock` too. The cache test uses two users so both windows are the single identical question.

- [ ] **Step 2: Run** `uv run pytest tests/test_chat_service.py -v`. Expected: FAIL on import.
- [ ] **Step 3: Implement `ChatService`** following the behaviour list above. Locks live in a `defaultdict(asyncio.Lock)` on the instance.
- [ ] **Step 4: Run** `uv run pytest tests/test_chat_service.py -v`. Expected: 11 passed.
- [ ] **Step 5: Write `docs/notes/05-chat-service.md`** (why one service behind both channels, two transactions around the LLM call, the in-process lock and when it stops being enough, where stage 2 plugs in) and commit as `feat: chat service`.

---

### Task 6: REST API and app factory

**Files:**
- Create: `src/deskmate/main.py`, `src/deskmate/api/__init__.py`, `src/deskmate/api/deps.py`, `src/deskmate/api/chat.py`, `src/deskmate/api/health.py`, `tests/test_api.py`, `docs/notes/06-api.md`
- Modify: `tests/conftest.py` (add `client` fixture: `httpx.AsyncClient` over `ASGITransport(create_app(...))` with test overrides)

**Interfaces:**
- Consumes: `ChatService`, `Settings`, `make_engine`, `make_session_factory`, stores, `ClaudeClient`.
- Produces:
  - `main.create_app(settings: Settings | None = None, *, llm: LLMClient | None = None, redis=None, engine=None) -> FastAPI`. The lifespan builds whatever was not injected, stores `settings`, `engine`, `redis`, `chat_service` on `app.state`, and disposes what it created on shutdown. With no injected `llm` it builds `ClaudeClient` and fails at startup if `anthropic_api_key` is missing. Module-level `app` is not created at import; uvicorn runs `deskmate.main:create_app` with `--factory`.
  - `api.deps.get_chat_service(request) -> ChatService`; `require_api_key` compares `X-API-Key` with `settings.api_key` using `secrets.compare_digest`, raising 401.
  - `POST /chat`: request model `ChatRequest(user_id: str 1..64, text: str)` where a validator strips `text` and requires 1..4000 characters; response model `ChatResponse(answer: str, dialog_id: int, message_id: int, cached: bool)`. Calls the service with `external_id=f"api:{user_id}"`. `LLMError` becomes 502 `{"detail": "LLM unavailable"}`.
  - `GET /health`: runs `SELECT 1` and `PING`; body `{"status", "postgres", "redis"}` with the status codes from the spec.

- [ ] **Step 1: Write the failing tests** in `tests/test_api.py`

```python
H = {"X-API-Key": "test-key"}

async def test_chat_happy_path(client):
    r = await client.post("/chat", json={"user_id": "alice", "text": "Привет"}, headers=H)
    assert r.status_code == 200
    assert r.json().keys() == {"answer", "dialog_id", "message_id", "cached"} and r.json()["answer"] == "ответ"

async def test_chat_user_is_namespaced(client, session):
    await client.post("/chat", json={"user_id": "alice", "text": "Привет"}, headers=H)
    assert (await session.scalars(select(User.external_id))).all() == ["api:alice"]

@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
async def test_chat_requires_api_key(client, headers):
    assert (await client.post("/chat", json={"user_id": "a", "text": "x"}, headers=headers)).status_code == 401

@pytest.mark.parametrize("body", [
    {"user_id": "a", "text": ""}, {"user_id": "a", "text": "   \n"}, {"user_id": "a", "text": "я" * 4001},
    {"user_id": "", "text": "x"}, {"user_id": "u" * 65, "text": "x"}, {"text": "x"}])
async def test_chat_rejects_invalid_input_without_calling_llm(client, fake_llm, body):
    assert (await client.post("/chat", json=body, headers=H)).status_code == 422
    assert fake_llm.calls == []

async def test_chat_accepts_exactly_4000_characters_and_trims(client, fake_llm):
    r = await client.post("/chat", json={"user_id": "a", "text": "  " + "я" * 4000 + "  "}, headers=H)
    assert r.status_code == 200 and len(fake_llm.calls[0][1][-1].content) == 4000

async def test_chat_llm_failure_is_502(client, fake_llm):
    fake_llm.fail = True
    r = await client.post("/chat", json={"user_id": "a", "text": "x"}, headers=H)
    assert r.status_code == 502 and r.json() == {"detail": "LLM unavailable"}

async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "postgres": True, "redis": True}

async def test_health_degraded_when_redis_down(client_with_broken_redis):
    r = await client_with_broken_redis.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "degraded", "postgres": True, "redis": False}

async def test_health_503_when_postgres_down(client_with_dead_postgres):
    r = await client_with_dead_postgres.get("/health")
    assert r.status_code == 503 and r.json()["postgres"] is False
```

`fake_llm` is the `FakeLLMClient` injected into the app. `client_with_dead_postgres` injects an engine pointing at a closed port.

- [ ] **Step 2: Run** `uv run pytest tests/test_api.py -v`. Expected: FAIL on import.
- [ ] **Step 3: Implement `main.py`, `api/deps.py`, `api/chat.py`, `api/health.py`.**
- [ ] **Step 4: Run** `uv run pytest tests/test_api.py -v`. Expected: 15 passed.
- [ ] **Step 5: Write `docs/notes/06-api.md`** (app factory and dependency injection, why validation sits at the edge, constant-time key comparison, what the health codes mean to an orchestrator) and commit as `feat: REST chat and health endpoints`.

---

### Task 7: Telegram bot

**Files:**
- Create: `src/deskmate/bot/__init__.py`, `src/deskmate/bot/texts.py`, `src/deskmate/bot/handlers.py`, `src/deskmate/bot/setup.py`, `tests/test_bot.py`, `docs/notes/07-telegram.md`
- Modify: `src/deskmate/main.py` (lifespan starts and stops the bot; include the webhook router unless `bot_mode == "off"`)

**Interfaces:**
- Consumes: `ChatService`, `LLMError`, `UpdateDeduper`, `Settings`.
- Produces:
  - `bot.texts`: constants `START`, `NEW_DIALOG`, `NON_TEXT`, `TOO_LONG`, `UNAVAILABLE` with the exact strings from the spec's Telegram behaviour table; `MAX_INPUT = 4000`, `MAX_REPLY = 4096`.
  - `bot.handlers.split_reply(text: str, limit: int = MAX_REPLY) -> list[str]`: returns chunks of at most `limit` characters whose concatenation equals `text`; prefers to cut after the last newline in a chunk, then after the last space, otherwise at `limit`.
  - `bot.handlers.build_router(service: ChatService) -> aiogram.Router` with handlers `on_start(message)`, `on_new(message)`, `on_text(message)`, `on_other(message)`. The user is `external_id=f"tg:{message.from_user.id}"`, `full_name=message.from_user.full_name`. `on_text` strips the text; whitespace-only text is ignored without a reply; it sends the `typing` chat action before calling the service; any exception from the service is logged and answered with `UNAVAILABLE`.
  - `bot.setup.build_bot(settings, service) -> tuple[Bot, Dispatcher]`.
  - `bot.setup.webhook_router(bot, dp, deduper: UpdateDeduper, secret: str) -> APIRouter` exposing `POST /telegram/webhook` as the spec describes; background tasks are kept in a set so they are not garbage-collected.
  - `bot.setup.start_bot(app, settings)` / `stop_bot(app)`: in `webhook` mode call `bot.set_webhook(f"{webhook_base_url}/telegram/webhook", secret_token=webhook_secret)`; in `polling` mode call `bot.delete_webhook()` and run `dp.start_polling(bot, handle_signals=False)` as a task; on stop cancel polling and close the bot session. The webhook is not deleted on stop.

- [ ] **Step 1: Write the failing tests** in `tests/test_bot.py`

```python
def test_split_reply_short_text_is_one_chunk():
    assert split_reply("привет") == ["привет"]

@pytest.mark.parametrize("text", ["я" * 10000, ("слово " * 2000), ("строка\n" * 1500), "a" * 4096, "a" * 4097])
def test_split_reply_chunks_fit_and_lose_nothing(text):
    chunks = split_reply(text)
    assert all(0 < len(c) <= 4096 for c in chunks) and "".join(chunks) == text

def test_split_reply_prefers_newline():
    assert split_reply("a" * 10 + "\n" + "b" * 10, limit=15) == ["a" * 10 + "\n", "b" * 10]

async def test_start(handlers):
    msg = fake_message(text="/start"); await handlers.on_start(msg)
    msg.answer.assert_awaited_once_with(texts.START)

async def test_text_goes_to_service_and_reply_is_sent(handlers, fake_llm, session):
    msg = fake_message(text="  Привет  ", user_id=42, full_name="Анна"); await handlers.on_text(msg)
    msg.answer.assert_awaited_once_with("ответ")
    assert fake_llm.calls[0][1][-1].content == "Привет"
    assert (await session.scalars(select(User.external_id))).all() == ["tg:42"]

async def test_long_answer_is_sent_in_chunks(handlers, fake_llm):
    fake_llm.reply = "я" * 9000
    msg = fake_message(text="вопрос"); await handlers.on_text(msg)
    sent = [c.args[0] for c in msg.answer.await_args_list]
    assert len(sent) == 3 and "".join(sent) == "я" * 9000

async def test_too_long_input_is_refused_without_llm(handlers, fake_llm):
    msg = fake_message(text="я" * 4001); await handlers.on_text(msg)
    msg.answer.assert_awaited_once_with(texts.TOO_LONG); assert fake_llm.calls == []

async def test_whitespace_only_text_is_ignored(handlers, fake_llm):
    msg = fake_message(text="   "); await handlers.on_text(msg)
    msg.answer.assert_not_awaited(); assert fake_llm.calls == []

async def test_non_text_message(handlers, fake_llm):
    msg = fake_message(text=None); await handlers.on_other(msg)
    msg.answer.assert_awaited_once_with(texts.NON_TEXT); assert fake_llm.calls == []

async def test_llm_failure_sends_apology(handlers, fake_llm):
    fake_llm.fail = True
    msg = fake_message(text="вопрос"); await handlers.on_text(msg)
    msg.answer.assert_awaited_once_with(texts.UNAVAILABLE)

async def test_new_resets_dialog(handlers, fake_llm):
    await handlers.on_text(fake_message(text="раз"))
    msg = fake_message(text="/new"); await handlers.on_new(msg)
    msg.answer.assert_awaited_once_with(texts.NEW_DIALOG)
    await handlers.on_text(fake_message(text="два"))
    assert [m.content for m in fake_llm.calls[-1][1]] == ["два"]

async def test_webhook_rejects_wrong_secret(webhook_client, dp):
    r = await webhook_client.post("/telegram/webhook", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "bad"})
    assert r.status_code == 403; dp.feed_update.assert_not_awaited()

async def test_webhook_rejects_missing_secret(webhook_client):
    assert (await webhook_client.post("/telegram/webhook", json=UPDATE)).status_code == 403

async def test_webhook_feeds_update_once_for_duplicates(webhook_client, dp):
    for _ in range(2):
        r = await webhook_client.post("/telegram/webhook", json=UPDATE, headers=SECRET)
        assert r.status_code == 200
    await asyncio.sleep(0)
    assert dp.feed_update.await_count == 1

async def test_webhook_returns_before_processing_finishes(webhook_client, dp):
    dp.feed_update.side_effect = lambda *a, **k: asyncio.sleep(0.5)
    start = time.monotonic()
    assert (await webhook_client.post("/telegram/webhook", json=UPDATE, headers=SECRET)).status_code == 200
    assert time.monotonic() - start < 0.2
```

`handlers` exposes the handler functions bound to a service built with `fake_llm`. `fake_message` builds a `MagicMock` with `answer` and `bot.send_chat_action` as `AsyncMock`s and `from_user.id` defaulting to 1. `UPDATE` is a minimal valid Telegram update dict with `update_id: 1001`. `dp` is a `MagicMock` with `feed_update` as an `AsyncMock`; `webhook_client` mounts `webhook_router` on a bare FastAPI app with secret `s3cret`.

- [ ] **Step 2: Run** `uv run pytest tests/test_bot.py -v`. Expected: FAIL on import.
- [ ] **Step 3: Implement `bot/texts.py` and `bot/handlers.py`.** Register `on_other` last so it catches only what the others did not.
- [ ] **Step 4: Implement `bot/setup.py`** and wire `start_bot`/`stop_bot` and the webhook router into `main.py`'s lifespan.
- [ ] **Step 5: Run** `uv run pytest -v`. Expected: all tests in the suite pass, 19 of them in `tests/test_bot.py`.
- [ ] **Step 6: Write `docs/notes/07-telegram.md`** (webhook versus polling and why both exist, why the route answers 200 before the work is done, the secret header, idempotency by `update_id`, thin handlers) and commit as `feat: Telegram bot with webhook and polling modes`.

---

### Task 8: Docker Compose, README and live smoke test

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `README.md`, `docs/notes/08-docker.md`

**Interfaces:**
- Consumes: `deskmate.main:create_app`, Alembic config, `.env`.
- Produces: `docker compose up --build` starts `postgres` (postgres:17, named volume, `pg_isready` healthcheck), `redis` (redis:7, `redis-cli ping` healthcheck) and `app`. The `app` service waits for both to be healthy, reads `.env`, overrides `DATABASE_URL` and `REDIS_URL` to the service hostnames, runs `alembic upgrade head` then `uvicorn deskmate.main:create_app --factory --host 0.0.0.0 --port 8000`, publishes port 8000, and has a healthcheck on `/health`. The image is built with uv from the lockfile, runs as a non-root user, copies `prompts/` and sets `PROMPTS_DIR`.

- [ ] **Step 1: Write `Dockerfile`, `.dockerignore`, `docker-compose.yml`.**
- [ ] **Step 2: Start the stack without the bot.** Create `.env` from `.env.example` with `API_KEY=dev-key`, `BOT_MODE=off` and a real `ANTHROPIC_API_KEY` (ask the owner for it; never commit it). Run `docker compose up --build -d` then `docker compose ps`. Expected: three services, all `healthy`.
- [ ] **Step 3: Smoke-test the API against the real LLM.**

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/chat -H 'X-API-Key: dev-key' -H 'Content-Type: application/json' \
  -d '{"user_id":"smoke","text":"Привет! Кто ты?"}'
curl -s -X POST localhost:8000/chat -H 'X-API-Key: dev-key' -H 'Content-Type: application/json' \
  -d '{"user_id":"smoke","text":"Сколько дней отпуска мне положено?"}'
```

Expected: health is `{"status":"ok",...}`; the first answer is in Russian and names Deskmate; the second says it has no access to company data and does not invent a number; both share one `dialog_id`.

- [ ] **Step 4: Smoke-test the bot in polling mode.** The owner creates a bot with @BotFather and puts the token in `.env` with `BOT_MODE=polling`; restart `app`. In Telegram check `/start`, a two-turn conversation where the second question depends on the first, `/new`, and a sticker. Expected: the replies in the spec's Telegram behaviour table. Record any deviation as a bug and fix it under TDD before continuing.
- [ ] **Step 5: Write `README.md`**: what Deskmate is and the three-stage roadmap, an architecture diagram (Mermaid) of stage 1, quick start (`cp .env.example .env`, compose up, the curl example), the configuration table, how to run tests, and a "Design decisions" section linking to `docs/notes/`.
- [ ] **Step 6: Run** `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`. Expected: everything passes.
- [ ] **Step 7: Write `docs/notes/08-docker.md`** (healthchecks and start order, migrations at start-up and why that is acceptable for one instance, non-root image, secrets in `.env`) and commit as `feat: Docker Compose stack and README`.
