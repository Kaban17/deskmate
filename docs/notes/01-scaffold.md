# 01. Project scaffold and settings

## Why it is built this way

- **uv** manages Python 3.15, the lockfile and the virtualenv, so `uv sync` gives the same environment everywhere.
- **src layout** keeps the package out of the repo root, so tests import the installed package and not a stray local directory.
- **Settings from the environment** (pydantic-settings) follow twelve-factor config: one typed `Settings` class, defaults in code, secrets only in env or `.env`, which is git-ignored. `.env.example` documents every variable.
- **Fail-fast validation**: a missing `API_KEY`, or a bot mode without its token or webhook settings, raises at startup instead of surfacing on the first request. `get_settings()` is cached so the environment is read once.

## What an interviewer might ask

1. *Why validate the bot settings in a model validator and not per field?* The requirement depends on `bot_mode`, which is a cross-field rule that field validators cannot express.
2. *Why `lru_cache` on `get_settings`?* It parses the environment once and works as a FastAPI dependency; tests can call `cache_clear()` or build `Settings` directly.
3. *Why `Settings(_env_file=None)` in tests?* It stops a developer's local `.env` from changing test results.
4. *Why a src layout?* It avoids accidentally importing from the working directory, so tests exercise the installed package.
5. *Why is `ANTHROPIC_API_KEY` optional?* The app and tests run without it. Only the real LLM client needs it.
