# 05 Chat service

## Why it is built this way

One `ChatService` sits behind both channels. The REST API and the Telegram bot only translate their transport into `handle_message(external_id, text, full_name)`, so dialog rules, context windowing, caching and persistence exist once and behave the same everywhere.

The LLM call is wrapped by two short transactions instead of one long one. Transaction 1 saves the user message and commits before the call, so a provider failure never loses what the user wrote, and no database connection is held open during a multi-second request. Transaction 2 saves the assistant message with model, prompt version, tokens and latency.

The context window comes from Redis, with Postgres as the source of truth: on a miss (or when Redis is down) the window is rebuilt from `recent_messages`. The window is capped and leading assistant messages are dropped, because the Messages API requires the first message to be from the user. Cached answers are keyed by model, prompt version and the whole window, and are stored with null token counts so cost reports do not count them.

Concurrent messages from one user are serialised with a per-`external_id` `asyncio.Lock`, so history order and the context key stay consistent. Different users never wait for each other. The lock is in-process: with several workers or replicas two requests for the same user can run in parallel. Then a Postgres advisory lock (`pg_advisory_xact_lock` on the user id) or a Redis lock would be needed.

Stage 2 plugs in at the point where the window is passed to the LLM: retrieval or tools would extend the system prompt or the message list there, without touching the API or the bot.

## What an interviewer might ask

- Why commit the user message before calling the LLM? So a failure keeps the input and no connection is held during the slow call; the caller still gets `LLMError`.
- What happens when Redis is down? The stores fail open and log a warning; the window is rebuilt from Postgres and the LLM is called every time.
- Why drop leading assistant messages? Capping can cut the window in the middle of a pair, and the Messages API needs a user message first.
- Is the lock enough for multiple workers? No, it only covers one process. Use a database advisory lock or a Redis lock keyed by user.
- Why store cached answers with null tokens? No tokens were spent, so usage statistics stay honest while latency still shows the real response time.
