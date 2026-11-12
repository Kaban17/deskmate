# Build stage: full Python image (has gcc) plus a Rust toolchain, because
# pydantic-core has no prebuilt wheel for Python 3.15 and is compiled here.
FROM python:3.15 AS build

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo \
    PATH=/opt/cargo/bin:$PATH

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain stable \
    && pip install --no-cache-dir uv

WORKDIR /app

# Dependencies first, so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Runtime stage: no compilers, no Rust, no uv.
FROM python:3.15-slim

RUN useradd --system --create-home --uid 10001 deskmate

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY prompts ./prompts

ENV PATH=/app/.venv/bin:$PATH \
    PROMPTS_DIR=/app/prompts \
    PYTHONUNBUFFERED=1

USER deskmate
EXPOSE 8000
CMD ["uvicorn", "deskmate.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
