# Telegram bot

## Why it is built this way

- Two modes because they solve different problems. Webhook is the production mode: Telegram pushes updates to a public HTTPS URL, nothing polls, and the bot lives in the same FastAPI process. Polling needs no public URL, so it is the mode for local development. Handlers are identical in both, because both feed the same `Dispatcher`.
- The webhook route answers 200 before the work is done. Telegram retries a request that does not finish quickly, and an LLM call can take many seconds. The route validates, dedupes, starts a background task and returns. The tasks are kept in a set because the event loop holds only weak references to tasks, and a done-callback logs any exception so a failed update is not lost silently.
- The secret header (`X-Telegram-Bot-Api-Secret-Token`) is what proves a request comes from Telegram, since the URL alone is guessable. It is compared with `secrets.compare_digest` on bytes; a missing header is a 403.
- Idempotency is by `update_id`: a `SET NX` marker in Redis drops redeliveries. Redis failure fails open (the update is processed), so a retry can in the worst case be answered twice but is never lost.
- Handlers are thin: extract user and text, call `ChatService`, send chunks. All logic stays in the service, shared with `POST /chat`. `Handlers` is a class holding the service, so tests call its methods with mocked messages and no aiogram plumbing.
- The bot, dispatcher and deduper are built in the lifespan because `ChatService` and Redis exist only there; the webhook router is mounted from the lifespan and only when the mode is `webhook`. An injected bot (tests) is not closed by the app. The webhook is not deleted on shutdown so a restart does not drop pending updates.

## What an interviewer might ask

1. Why return 200 immediately? Telegram retries slow or failing requests; returning early avoids duplicate deliveries while the LLM works, and dedupe by `update_id` covers the retries that still happen.
2. How do you know a webhook call is really from Telegram? The secret token set in `setWebhook` is echoed in a header; it is compared in constant time.
3. Why keep references to background tasks? `asyncio` keeps only weak references to tasks, so an unreferenced task can be garbage-collected mid-run.
4. Why `handle_signals=False` in polling? aiogram would install its own signal handlers and fight uvicorn's; the lifespan cancels the task on shutdown instead.
5. How are long answers handled? `split_reply` cuts at the last newline, else the last space, else the hard limit of 4096, and the chunks concatenate back to the original text.
