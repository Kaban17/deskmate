# 02 Database

## Why it is built this way

- Postgres is the record. Redis holds only rebuildable copies (context window, answer cache, update dedupe), so losing Redis loses nothing.
- One active dialog per user is enforced by a partial unique index (`user_id` where `status = 'active'`). Application checks race under concurrent requests; the database does not. The repo closes the idle dialog and flushes before inserting the new one so the index is never violated.
- The migration is written by hand. Autogenerate can miss partial indexes and check constraints, and a reviewable explicit migration is the schema's source of truth.
- Tests run against real Postgres (testcontainers), not SQLite. Partial indexes, timezone-aware timestamps and `RETURNING` behave differently in SQLite, so SQLite would pass tests the production database fails.
- Repo functions flush but never commit; the caller owns the transaction, so one request is one atomic unit.
- `add_message` and `get_or_create_active_dialog` take time as an argument so callers can inject a clock.

## What an interviewer might ask

1. Why a partial unique index instead of checking in code? Because two concurrent requests can both see "no active dialog" and both insert; the index makes the second fail.
2. Why not autogenerate migrations? Autogenerate misses or mangles partial indexes and check constraints; hand-written output is explicit and reviewable.
3. Why real Postgres in tests? Behaviour parity: partial indexes, timestamptz and constraint errors differ in SQLite.
4. Why `expire_on_commit=False`? After commit, async sessions cannot lazily reload expired attributes, so access would raise.
5. Why are roles and statuses strings with check constraints rather than enums? Adding a value is a simple constraint change, not an `ALTER TYPE` migration.
