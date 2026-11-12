# Docker

## Why it is built this way

- Start order uses healthchecks, not sleeps. `postgres` has `pg_isready`, `redis` has `redis-cli ping`, and `app` uses `depends_on` with `condition: service_healthy`, so it never starts against a database that is still initialising. The app has its own healthcheck on `/health`, run with the Python already in the image so no curl is needed.
- Migrations run at container start (`alembic upgrade head && exec uvicorn`). That is acceptable because the app runs as one instance, so there is no race between replicas running the same migration. With several replicas this moves to a separate one-off job. `exec` makes uvicorn PID 1 so it receives SIGTERM.
- The build is multi-stage. `pydantic-core` has no prebuilt wheel for Python 3.15, so the build stage carries a Rust toolchain and a C compiler and compiles it. Only the finished virtualenv is copied into `python:3.15-slim`, which keeps compilers out of the runtime image. Dependencies are installed from `uv.lock` (`--frozen`) before the source is copied, so that layer is cached.
- The container runs as an unprivileged user (uid 10001). A compromised process cannot write to the image or act as root.
- Secrets live in `.env`, which is git-ignored and excluded by `.dockerignore`, and are passed at run time through `env_file`, never baked into an image layer. Only `DATABASE_URL` and `REDIS_URL` are overridden in Compose, because `localhost` inside the container is not the database.
- Postgres and Redis ports are not published; only the app is reachable from the host. Data survives `docker compose down` in the named `pgdata` volume.
- `PROMPTS_DIR` is set because the code locates `prompts/` relative to the source tree, which is not the case once the package is installed into a virtualenv.

## What an interviewer might ask

1. Why healthchecks with `service_healthy` instead of `depends_on` alone? Plain `depends_on` waits only for the container to start, not for the database to accept connections.
2. Is running migrations at start-up safe? For one instance yes; with replicas two could migrate at once, so you would run migrations as a separate step before rolling out.
3. Why a multi-stage build? The Rust and C toolchains are needed only to compile one dependency; leaving them out of the runtime image makes it smaller and shrinks the attack surface.
4. Why not run as root? Least privilege: if the app is exploited, the attacker has no root in the container.
5. How are secrets handled? Through an untracked `.env` read at run time; they are not in the repository or in image layers. A real deployment would use the platform's secret store.
