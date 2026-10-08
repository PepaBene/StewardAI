"""FastAPI app: serves the frontend's action calls. Run with `uv run uvicorn api.main:app`."""

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.alert_trail_routes import router as alert_trail_router
from api.auth import Owner
from api.budget_routes import router as budget_router
from api.chat_routes import router as chat_router
from api.company_routes import router as company_router
from api.config_history_routes import router as config_history_router
from api.control_stake_routes import router as control_stake_router
from api.data_context_routes import router as data_context_router
from api.db import close_pool, get_pool_or_error
from api.decision_routes import router as decision_router
from api.discover_routes import router as discover_router
from api.escalation_routes import router as escalation_router
from api.free_sources_routes import router as free_sources_router
from api.fund_research_routes import router as fund_research_router
from api.fund_routes import router as fund_router
from api.fundamentals_routes import router as fundamentals_router
from api.gate_routes import router as gate_router
from api.graph_routes import router as graph_router
from api.insider_activity_routes import router as insider_activity_router
from api.insider_routes import router as insider_router
from api.investor_routes import router as investor_router
from api.lookthrough_routes import router as lookthrough_router
from api.macro_routes import router as macro_router
from api.market_routes import router as market_router
from api.news_routes import router as news_router
from api.nport_routes import router as nport_router
from api.pdmr_routes import router as pdmr_router
from api.performance_routes import router as performance_router
from api.prompts_routes import router as prompts_router
from api.reference_routes import router as reference_router
from api.research_context_routes import router as research_context_router
from api.research_routes import router as research_router
from api.routes import router
from api.screen_routes import router as screen_router
from api.source_routes import router as source_router
from api.valuation_routes import router as valuation_router
from api.verify_routes import router as verify_router
from api.watchlist_routes import router as watchlist_router
from api.web_fact_routes import router as web_fact_router
from api.xray_routes import router as xray_router
from steward import __version__
from steward.config import StrategyConfig, ThesesConfig
from steward.heartbeat import worker_status
from steward.logging import configure_logging
from steward.settings import get_settings


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: bool
    worker_alive: bool
    worker_last_beat: dt.datetime | None
    detail: str | None = None


# Request bodies are small JSON documents. The limit is checked before anything reads the
# body (and so before auth), so an anonymous client cannot make the API buffer and parse an
# arbitrarily large upload. A config import carries every domain at once, so it gets more.
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_BODY_BYTES_BY_PATH = {"/config/import": 10 * 1024 * 1024}


class _BodyTooLarge(HTTPException):
    def __init__(self, limit: int) -> None:
        super().__init__(*_too_large(limit))


class BodySizeLimitMiddleware:
    """Reject request bodies over the limit with 413: at once when Content-Length says so,
    otherwise as soon as a streamed (chunked) body passes it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = MAX_BODY_BYTES_BY_PATH.get(scope["path"], MAX_BODY_BYTES)
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _reject(
                        scope, send, status.HTTP_400_BAD_REQUEST, "Invalid Content-Length"
                    )
                    return
                if declared > limit:
                    await _reject(scope, send, *_too_large(limit))
                    return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge(limit)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            # Normally FastAPI turns this into the 413 itself; this covers a body read
            # outside a route's request parsing.
            if started:
                raise
            await _reject(scope, send, *_too_large(limit))


def _too_large(limit: int) -> tuple[int, str]:
    return status.HTTP_413_CONTENT_TOO_LARGE, f"Request body is larger than {limit} bytes"


async def _reject(scope: Scope, send: Send, code: int, detail: str) -> None:
    await JSONResponse({"detail": detail}, status_code=code)(scope, _no_body, send)


async def _no_body() -> Message:
    return {"type": "http.disconnect"}


def _operation_id(route: APIRoute) -> str:
    # Stable, readable operation ids for the generated TypeScript client.
    return route.name


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.secret_values())

    app = FastAPI(
        title="StewardAI API",
        version=__version__,
        generate_unique_id_function=_operation_id,
        # One schema per model (no "-Input"/"-Output" split) so the frontend gets
        # exactly one StrategyConfig type.
        separate_input_output_schemas=False,
        lifespan=_lifespan,
    )
    # Added first so it sits inside CORS: a 413 still carries the CORS headers.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", tags=["system"])
    async def health() -> HealthResponse:
        """Liveness check for the frontend and the uptime monitor. No auth."""
        return HealthResponse(status="ok", version=__version__)

    @app.get(
        "/health/ready",
        tags=["system"],
        responses={503: {"model": ReadinessResponse, "description": "Not ready"}},
    )
    async def ready(
        pool: Annotated[asyncpg.Pool | str, Depends(get_pool_or_error)],
    ) -> JSONResponse:
        """Readiness for the uptime monitor: the database answers and the worker's heartbeat
        is recent. 503 otherwise, with what is wrong. No auth; reveals no data."""
        body = ReadinessResponse(
            status="degraded",
            version=__version__,
            database=False,
            worker_alive=False,
            worker_last_beat=None,
        )
        try:
            if isinstance(pool, str):
                raise LookupError(pool)
            status_ = await worker_status(pool)
        except LookupError as exc:
            body.detail = str(exc)
        except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
            body.detail = f"database error ({type(exc).__name__})"
        else:
            body.database = True
            body.worker_alive = status_.alive
            body.worker_last_beat = status_.last_beat
            if status_.alive:
                body.status = "ok"
            else:
                body.detail = "no worker heartbeat in the last 3 minutes"
        return JSONResponse(
            body.model_dump(mode="json"), status_code=200 if body.status == "ok" else 503
        )

    @app.post("/config/strategy/validate", tags=["config"])
    async def validate_strategy(body: StrategyConfig, _owner: Owner) -> StrategyConfig:
        """Validate a strategy config without saving it. Returns it with defaults applied;
        an invalid body gets a 422 listing every error."""
        return body

    @app.post("/config/theses/validate", tags=["config"])
    async def validate_theses(body: ThesesConfig, _owner: Owner) -> ThesesConfig:
        """Validate the theses config without saving it. Returns it with defaults applied;
        an invalid body gets a 422 listing every error."""
        return body

    app.include_router(config_history_router)
    app.include_router(market_router)
    app.include_router(lookthrough_router)
    app.include_router(macro_router)
    app.include_router(source_router)
    app.include_router(valuation_router)
    app.include_router(router)
    app.include_router(prompts_router)
    app.include_router(budget_router)
    app.include_router(research_router)  # before news: /reports/search vs /reports/{id}
    app.include_router(escalation_router)  # before news: /escalations/stats vs /{id}
    app.include_router(news_router)
    app.include_router(reference_router)
    app.include_router(web_fact_router)
    app.include_router(verify_router)
    app.include_router(chat_router)
    app.include_router(fundamentals_router)
    app.include_router(free_sources_router)
    app.include_router(watchlist_router)
    app.include_router(screen_router)
    app.include_router(discover_router)
    app.include_router(company_router)
    app.include_router(fund_research_router)
    app.include_router(insider_router)
    app.include_router(investor_router)
    app.include_router(control_stake_router)
    app.include_router(insider_activity_router)
    app.include_router(nport_router)
    app.include_router(fund_router)
    app.include_router(performance_router)
    app.include_router(xray_router)
    app.include_router(decision_router)
    app.include_router(pdmr_router)
    app.include_router(graph_router)
    app.include_router(data_context_router)
    app.include_router(research_context_router)
    app.include_router(alert_trail_router)
    app.include_router(gate_router)
    return app


app = create_app()
