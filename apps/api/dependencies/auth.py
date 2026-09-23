"""
Admin authentication.

Mutating and operational endpoints — triggering an ingest, terminating database
connections, importing a stock universe — must not be callable by anyone who
knows the URL. This module supplies the dependency that guards them.

The token is read from ``ADMIN_API_TOKEN``. When it is unset the guard **denies
every request** rather than allowing them: an operator who has not configured a
token has not decided that these endpoints should be public, and defaulting to
open is how an internal tool ends up writing to production from the internet.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException, status

from config import settings

log = logging.getLogger("auth")


async def require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """
    Allow the request only when it carries the configured admin token.

    Accepts either ``Authorization: Bearer <token>`` or ``X-Admin-Token:
    <token>``. Comparison is constant-time, so the endpoint cannot be used as an
    oracle to recover the token one character at a time.
    """
    expected = (getattr(settings, "ADMIN_API_TOKEN", "") or "").strip()

    if not expected:
        log.error(
            "Admin endpoint called but ADMIN_API_TOKEN is not configured; denying."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Administrative endpoints are disabled because ADMIN_API_TOKEN "
                "is not configured on this deployment."
            ),
        )

    presented = x_admin_token
    if not presented and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            presented = value

    if not presented or not hmac.compare_digest(presented.strip(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
