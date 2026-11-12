# REST API and app factory

## Why it is built this way

- `create_app(settings, llm=, redis=, engine=)` is a factory, not a module-level `app`. Tests inject a fake LLM, a fake Redis and the shared test engine; production builds everything from settings in the lifespan. Run it with `uvicorn deskmate.main:create_app --factory`.
- The lifespan puts `settings`, `engine`, `redis` and `chat_service` on `app.state`; handlers reach them through dependencies (`get_chat_service`). Nothing is a global, so two apps in one process do not share state.
- The lifespan disposes only what it created. An injected engine or Redis client belongs to the caller (the test session), so closing it would break later tests.
- The Redis client is built with `decode_responses=True`, because the cache stores treat replies as `str`. Without it a cache hit would be `bytes`.
- Validation sits at the edge (pydantic on `ChatRequest`): empty, whitespace-only and over-long text return 422 before any database or LLM work. Text is trimmed once there, so the service sees clean input.
- The API key is compared with `secrets.compare_digest` on bytes, so response time does not leak how many leading characters matched, and non-ASCII headers cannot raise.
- Health: 200 `ok` means both dependencies work; 200 `degraded` means Redis is down, which is safe because the stores fail open, so the instance should keep receiving traffic; 503 means Postgres is down and the instance cannot serve, so an orchestrator should stop routing to it. Each probe has a short timeout and never raises.

## What an interviewer might ask

1. Why an app factory instead of `app = FastAPI()`? Dependencies are injected per app, so tests need no monkeypatching and no import-time side effects (no connections at import).
2. Why does Redis down give 200 and Postgres down 503? Redis is an optimisation with fail-open behaviour; Postgres is the source of truth. A readiness probe should fail only when the service cannot do its job.
3. Why `compare_digest`? A plain `==` can return early at the first differing byte, which makes a timing attack on the key possible.
4. Why trim and validate in the request model? Invalid input is rejected cheaply and consistently, and the service layer can assume valid text.
5. Why does `httpx.ASGITransport` need the lifespan entered by hand? It sends requests straight to the ASGI app and does not emit lifespan events, so `app.state` would be empty.
