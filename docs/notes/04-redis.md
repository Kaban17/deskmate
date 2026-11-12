# 04. Redis stores

## Why it is built this way

**Cache versus record.** Postgres is the record: every message is stored there and nothing is lost if Redis is wiped. Redis only holds things that are cheap to rebuild: the recent context window per dialog (`ctx:<id>`, one JSON string, last N messages, with a TTL), a response cache, and update ids. The context store saves a database read on every turn, and when it misses the service reloads from Postgres.

**Fail-open.** Every store catches `RedisError`, logs a warning and returns a neutral value: `None` for a miss, nothing for a write, `True` for "first seen". A Redis outage makes the bot slower and costlier (no cache, a database read per turn), but it never makes a request fail. The one risk is that dedupe stops working during an outage, so a retried webhook can be answered twice. That is better than dropping all traffic.

**What the response cache does and does not hit.** The key is a SHA-256 over canonical JSON of the model, the prompt version and the full message list. It hits only when the whole conversation so far is identical, so in practice it catches the first message of a dialog ("how do I reset my password") and repeated identical questions. It does not hit mid-conversation, because the history differs. Bumping the prompt version or changing the model invalidates old entries without a flush.

**`SET NX` for idempotency.** Telegram retries a webhook when it does not get a 200 quickly. `SET tg:update:<id> 1 NX EX ttl` is a single atomic command: the first caller gets OK, every later one gets nil. A separate GET then SET would race between two concurrent deliveries. The TTL keeps the key space bounded.

## What an interviewer might ask

1. **Why not keep the context only in Redis?** Redis is not durable here. Postgres is the source of truth, and Redis is a rebuildable cache.
2. **What happens when Redis is down?** Requests still succeed. Each store logs a warning and falls back: context is reloaded from Postgres, the LLM is called without the cache, and the update is treated as new.
3. **Why `SET NX EX` and not GET then SET?** The check and the write must be atomic. Two concurrent deliveries of the same update would both see "not present" with GET then SET.
4. **Why hash the messages into the cache key?** Keys stay a fixed size and contain no user text. Canonical JSON (sorted keys, fixed separators) makes the hash stable.
5. **How would you invalidate cached answers after a prompt change?** The prompt version is part of the key, so bumping it makes old entries unreachable, and they expire by TTL.
