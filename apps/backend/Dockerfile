# StewardAI backend image: the same image runs the API and the worker (see deploy/compose.yaml).
# Its default command (start.sh) runs both in one container, for hosts that bill per service
# (Railway). deploy/compose.yaml overrides the command to run them as separate services.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY README.md ./
COPY --chmod=755 start.sh /app/start.sh
RUN uv sync --locked --no-dev

RUN useradd --system --uid 10001 steward
USER steward

EXPOSE 8000
CMD ["/app/start.sh"]
