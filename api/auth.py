"""Basic-auth middleware — Phase 5 Task 1.5.

Single shared credential gates the whole instance per spec AC-5.
The credential lives in the ``STREETSENSE_BASIC_AUTH`` env var
(format: ``"user:bcrypt-hash"``). When unset (or empty / malformed),
the middleware is a no-op — dev workflow stays frictionless. When
set, every request except those on the :data:`_EXEMPT_PATHS`
allowlist must carry a valid ``Authorization: Basic ...`` header.

Per ADR 0008, the auth gate lives inside the FastAPI app (not
Caddy / Fly's edge) so the same code path runs on both deploy
shapes. That keeps the auth contract testable in-process and
portable if we ever migrate hosts.

Comparison uses ``bcrypt.checkpw`` (constant-time within the
algorithm) and ``secrets.compare_digest`` for the username so
neither half leaks length / character information through timing.
"""

from __future__ import annotations

import base64
import os
import secrets

import bcrypt
import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

_ENV = "STREETSENSE_BASIC_AUTH"
_log = structlog.get_logger(__name__)

# Paths exempt from auth. /health is on the list because Fly's edge
# healthcheck (fly.toml -> http_service.checks) hits it without
# credentials; requiring auth there would mark the Machine
# unhealthy and starve it of traffic.
_EXEMPT_PATHS: frozenset[str] = frozenset({"/health"})

_REALM = "StreetSense"
_WWW_AUTHENTICATE = f'Basic realm="{_REALM}"'


def _parse_credential_env(raw: str | None) -> tuple[str, bytes] | None:
    """Parse the ``STREETSENSE_BASIC_AUTH`` value into (username, bcrypt-hash).

    Returns ``None`` when the env var is unset, empty, or malformed —
    we deliberately fail *open* on a broken config rather than half-
    enabling a gate that nobody can pass. The middleware logs at
    warning level so an operator notices.
    """
    if not raw:
        return None
    # Split on the first ``:`` only; bcrypt hashes never contain ``:``,
    # but usernames legitimately can carry it in some setups.
    sep = raw.find(":")
    if sep <= 0 or sep == len(raw) - 1:
        _log.warning("auth.malformed_env_var", reason="missing or empty user/hash half")
        return None
    username = raw[:sep]
    hashed = raw[sep + 1 :].encode("utf-8")
    return username, hashed


def _decode_basic_header(header_value: str) -> tuple[str, str] | None:
    """Pull ``(user, password)`` out of an ``Authorization: Basic ...`` header.

    Returns ``None`` on any shape mismatch — the middleware then
    returns 401 with the WWW-Authenticate prompt.
    """
    if not header_value.lower().startswith("basic "):
        return None
    encoded = header_value[6:].strip()
    if not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    sep = decoded.find(":")
    if sep < 0:
        return None
    return decoded[:sep], decoded[sep + 1 :]


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Gate the app behind a single shared basic-auth credential.

    Reads the credential from the env once at middleware
    construction so a deploy that mutates the env mid-flight
    doesn't see partial state. To rotate the password, restart the
    process (or redeploy).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._credentials = _parse_credential_env(os.environ.get(_ENV))
        if self._credentials is None:
            _log.info("auth.disabled", reason="STREETSENSE_BASIC_AUTH unset or malformed")
        else:
            _log.info("auth.enabled", username=self._credentials[0])

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if self._credentials is None:
            return await call_next(request)
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        decoded = _decode_basic_header(header)
        if decoded is None:
            return _unauthorized()
        username, password = decoded
        expected_user, expected_hash = self._credentials
        # Constant-time username compare. bcrypt.checkpw is
        # constant-time within the bcrypt algorithm.
        user_ok = secrets.compare_digest(username, expected_user)
        pass_ok = bcrypt.checkpw(password.encode("utf-8"), expected_hash)
        if not (user_ok and pass_ok):
            return _unauthorized()
        return await call_next(request)


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        content="Unauthorized",
        headers={"WWW-Authenticate": _WWW_AUTHENTICATE},
        media_type="text/plain",
    )
