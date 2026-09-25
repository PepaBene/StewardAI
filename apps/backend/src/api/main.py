"""FastAPI app: serves the frontend's action calls. Run with `uv run uvicorn api.main:app`."""

from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from pydantic import BaseModel

from api.auth import Owner
from steward import __version__
from steward.config import StrategyConfig, ThesesConfig
from steward.logging import configure_logging
from steward.settings import get_settings


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


def _operation_id(route: APIRoute) -> str:
    # Stable, readable operation ids for the generated TypeScript client.
    return route.name


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

    return app


app = create_app()
