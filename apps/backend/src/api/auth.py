"""Supabase Auth JWT verification.

The API does not implement auth; it checks the same token the frontend got from
Supabase Auth. Asymmetric tokens (the Supabase default for new projects) are verified
against the project's JWKS; projects still on the legacy shared secret can set
SUPABASE_JWT_SECRET for HS256. On top of signature, expiry, issuer and audience, the
token must belong to the single configured owner and, unless disabled, carry an
MFA-verified assurance level (aal2).
"""

from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from steward.settings import Settings, get_settings

ASYMMETRIC_ALGORITHMS = ["ES256", "RS256", "EdDSA"]


class AuthError(Exception):
    def __init__(self, detail: str, status_code: int = status.HTTP_401_UNAUTHORIZED) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None
    aal: str | None


class KeyResolver:
    """Resolves the verification key for a token from the project's JWKS (cached)."""

    def __init__(self, jwks_url: str) -> None:
        self._client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600)

    def __call__(self, token: str) -> Any:
        return self._client.get_signing_key_from_jwt(token).key


class TokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        owner_user_id: str | None,
        require_mfa: bool,
        key_resolver: Any | None = None,
        hs256_secret: str | None = None,
        audience: str = "authenticated",
    ) -> None:
        self._issuer = issuer
        self._owner_user_id = owner_user_id
        self._require_mfa = require_mfa
        self._key_resolver = key_resolver
        self._hs256_secret = hs256_secret
        self._audience = audience

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
        except jwt.PyJWTError as exc:
            raise AuthError("Malformed token") from exc

        if algorithm == "HS256":
            if not self._hs256_secret:
                raise AuthError("HS256 tokens are not accepted")
            key: Any = self._hs256_secret
            algorithms = ["HS256"]
        elif algorithm in ASYMMETRIC_ALGORITHMS and self._key_resolver is not None:
            try:
                key = self._key_resolver(token)
            except jwt.PyJWTError as exc:
                raise AuthError("Unknown signing key") from exc
            algorithms = ASYMMETRIC_ALGORITHMS
        else:
            raise AuthError("Unsupported token algorithm")

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=algorithms,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"Invalid token: {exc}") from exc

        if not self._owner_user_id:
            raise AuthError("OWNER_USER_ID is not configured", status.HTTP_403_FORBIDDEN)
        if claims["sub"] != self._owner_user_id:
            raise AuthError("Not the owner", status.HTTP_403_FORBIDDEN)
        aal = claims.get("aal")
        if self._require_mfa and aal != "aal2":
            raise AuthError("MFA verification required", status.HTTP_403_FORBIDDEN)
        return AuthenticatedUser(user_id=claims["sub"], email=claims.get("email"), aal=aal)


_verifier: TokenVerifier | None = None


def get_verifier(settings: Annotated[Settings, Depends(get_settings)]) -> TokenVerifier:
    global _verifier
    if _verifier is None:
        if not settings.supabase_url:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth is not configured")
        secret = settings.supabase_jwt_secret
        _verifier = TokenVerifier(
            issuer=f"{settings.supabase_url}/auth/v1",
            owner_user_id=settings.owner_user_id,
            require_mfa=settings.require_mfa,
            key_resolver=KeyResolver(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"),
            hs256_secret=secret.get_secret_value() if secret else None,
        )
    return _verifier


_bearer = HTTPBearer(auto_error=False)


def require_owner(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[TokenVerifier, Depends(get_verifier)],
) -> AuthenticatedUser:
    # A sync dependency on purpose: a JWKS cache miss does blocking I/O, so FastAPI
    # runs this in its threadpool instead of on the event loop.
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verifier.verify(credentials.credentials)
    except AuthError as exc:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        raise HTTPException(exc.status_code, exc.detail, headers=headers) from exc


Owner = Annotated[AuthenticatedUser, Depends(require_owner)]
