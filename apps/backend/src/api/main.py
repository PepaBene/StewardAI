"""FastAPI app: serves the frontend's action calls. Run with `uv run uvicorn api.main:app`."""

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import asyncpg
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from api.auth import Owner
from api.config_history_routes import router as config_history_router
from api.db import close_pool, get_pool_or_error
from api.news_routes import router as news_router
from api.reference_routes import router as reference_router
from api.routes import router
from api.verify_routes import router as verify_router
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
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
    app.include_router(router)
    app.include_router(news_router)
    app.include_router(reference_router)
    app.include_router(verify_router)
    return app


app = create_app()
