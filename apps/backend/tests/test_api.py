import json
import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from api import auth
from api.auth import AuthError, TokenVerifier
from api.export_openapi import DEFAULT_PATH, build_spec, render
from api.main import create_app

ISSUER = "https://example.supabase.co/auth/v1"
OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


def _token(key: Any = PRIVATE_KEY, alg: str = "ES256", **claims: Any) -> str:
    payload = {
        "iss": ISSUER,
        "aud": "authenticated",
        "sub": OWNER,
        "exp": int(time.time()) + 300,
        "aal": "aal2",
        "email": "owner@example.com",
        **claims,
    }
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": "k1"})


def _verifier(**kwargs: Any) -> TokenVerifier:
    defaults: dict[str, Any] = {
        "issuer": ISSUER,
        "owner_user_id": OWNER,
        "require_mfa": True,
        "key_resolver": lambda _token: PRIVATE_KEY.public_key(),
    }
    return TokenVerifier(**{**defaults, **kwargs})


def test_valid_owner_token_is_accepted() -> None:
    user = _verifier().verify(_token())
    assert user.user_id == OWNER and user.aal == "aal2"


@pytest.mark.parametrize(
    ("token_kwargs", "verifier_kwargs", "message"),
    [
        ({"key": OTHER_KEY}, {}, "Invalid token"),
        ({"exp": int(time.time()) - 10}, {}, "expired"),
        ({"iss": "https://evil.example/auth/v1"}, {}, "(?i)issuer"),
        ({"aud": "anon"}, {}, "(?i)audience"),
        ({"sub": "someone-else"}, {}, "Not the owner"),
        ({"aal": "aal1"}, {}, "MFA verification required"),
        ({}, {"owner_user_id": None}, "OWNER_USER_ID is not configured"),
        (
            {"key": "shared-secret-value-long-enough-for-hs256", "alg": "HS256"},
            {},
            "HS256 tokens are not accepted",
        ),
    ],
)
def test_bad_tokens_are_rejected(
    token_kwargs: dict[str, Any], verifier_kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(AuthError, match=message):
        _verifier(**verifier_kwargs).verify(_token(**token_kwargs))


def test_mfa_can_be_disabled_explicitly() -> None:
    assert _verifier(require_mfa=False).verify(_token(aal="aal1")).aal == "aal1"


def test_legacy_hs256_secret_when_configured() -> None:
    secret = "shared-secret-value-long-enough-for-hs256"
    verifier = _verifier(hs256_secret=secret)
    assert verifier.verify(_token(key=secret, alg="HS256")).user_id == OWNER


def test_alg_none_is_rejected() -> None:
    unsigned = jwt.encode({"sub": OWNER}, None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(AuthError):
        _verifier().verify(unsigned)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[auth.get_verifier] = lambda: _verifier()
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_config_validation_requires_a_token(client: TestClient) -> None:
    response = client.post("/config/strategy/validate", json={"base_currency": "CZK"})
    assert response.status_code == 401


def test_config_validation_rejects_non_mfa_session(client: TestClient) -> None:
    response = client.post(
        "/config/strategy/validate",
        json={"base_currency": "CZK"},
        headers={"Authorization": f"Bearer {_token(aal='aal1')}"},
    )
    assert response.status_code == 403


def test_config_validation_returns_normalised_config(client: TestClient) -> None:
    response = client.post(
        "/config/strategy/validate",
        json={"base_currency": "EUR"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert response.status_code == 200
    assert response.json()["fx_source"] == "ecb"


def test_invalid_config_gets_422_with_every_error(client: TestClient) -> None:
    response = client.post(
        "/config/theses/validate",
        json={"theses": [{"ticker": "bad ticker!", "why_owned": ""}]},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert response.status_code == 422
    locations = {tuple(err["loc"][-1:]) for err in response.json()["detail"]}
    assert {("ticker",), ("why_owned",)} <= locations


def test_openapi_is_valid_3_1_with_config_schemas() -> None:
    spec = build_spec()
    validate(spec)
    assert spec["openapi"].startswith("3.1")
    schemas = spec["components"]["schemas"]
    assert {"StrategyConfig", "ThesisConfig", "ThesesConfig", "HealthResponse"} <= set(schemas)
    assert not any(name.endswith(("-Input", "-Output")) for name in schemas)


def test_committed_openapi_matches_code() -> None:
    committed = DEFAULT_PATH.read_text(encoding="utf-8")
    assert json.loads(committed) == build_spec() and committed == render(build_spec()), (
        "openapi.json is stale: run `uv run python -m api.export_openapi`"
    )
