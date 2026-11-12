# 03 LLM client and prompts

## Why it is built this way

- The chat service depends on a small `LLMClient` protocol (one `complete` call), not on the Anthropic SDK. Swapping providers or models touches one class, and SDK exceptions never leak: `ClaudeClient` wraps every `anthropic.APIError` in `LLMError`, so callers handle one failure type.
- A hand-written `FakeLLMClient` is used by the service, API and bot tests instead of patching the SDK in each test. It records calls, can fail, can be slow, and can compute replies; tests change `reply`/`fail`/`delay` after construction. Only one test file knows the SDK's response shape.
- Prompts are versioned files (`prompts/system/v1.md`) selected by `PROMPT_VERSION`, and each assistant message records the version used. Changing wording means adding `v2`, so old answers stay attributable and rollback is a config change. The directory can be overridden with `PROMPTS_DIR` (the Docker image sets it).
- Settings supply the model id and `max_tokens`; the client has no defaults of its own.

## What an interviewer might ask

1. Why a Protocol rather than an abstract base class? Structural typing: the fake and the real client both satisfy it without inheriting, and tests need no registration.
2. Why wrap SDK errors? The service decides what to tell the user on failure; it should not know provider exception hierarchies.
3. Why not mock the SDK everywhere? Mock-shaped tests break on SDK changes in many places and say little about our logic; one fake keeps the seam in one spot.
4. Why version prompts as files instead of constants or DB rows? They are reviewed in git, diffable, and tied to a recorded version per message without a migration.
5. Why join only `text` blocks? Responses can contain other block types; the user-visible answer is the concatenated text.
