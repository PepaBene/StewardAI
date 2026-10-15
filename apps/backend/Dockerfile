# StewardAI backend image: the same image runs the API and the worker (see deploy/compose.yaml).
# Its default command (start.sh) runs both in one container, for hosts that bill per service
# (Railway). deploy/compose.yaml overrides the command to run them as separate services.
#
# Build with the repository root as the context (`docker build -f apps/backend/Dockerfile .`):
# the image also carries supabase/migrations, which `python -m steward.migrate` applies as the
# pre-deploy step. See Dockerfile.dockerignore for what the context lets through.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/.python-version ./

# The headless-browser fallback (Playwright + Chromium) adds roughly 400 MB to the image and is
# used only when a job's browser_fallback setting is on. Build without it with
# `--build-arg WITH_BROWSER=0`: the fallback then switches itself off.
ARG WITH_BROWSER=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ "$WITH_BROWSER" = "1" ]; then EXTRA="--extra browser"; else EXTRA=""; fi \
    && uv sync --locked --no-dev --no-install-project $EXTRA \
    && if [ "$WITH_BROWSER" = "1" ]; then playwright install --with-deps chromium \
       && chmod -R a+rX /ms-playwright; fi

COPY apps/backend/src ./src
COPY supabase/migrations ./migrations
COPY apps/backend/README.md ./
COPY --chmod=755 apps/backend/start.sh /app/start.sh
RUN if [ "$WITH_BROWSER" = "1" ]; then EXTRA="--extra browser"; else EXTRA=""; fi \
    && uv sync --locked --no-dev $EXTRA

RUN useradd --system --uid 10001 steward
USER steward

EXPOSE 8000
CMD ["/app/start.sh"]
