"""API-key authentication with three roles.

Roles rather than a single password, because the people using this dashboard
need different things: a CX lead reads transcripts, which are personal data; an
operator queues and cancels calls; only an admin should be able to rewrite what
an agent says to customers or clear the do-not-call list.

Keys are configured as ``API_KEYS=<key>:<role>,<key>:<role>``. With none set,
authentication is disabled - which keeps local development and the test suite
free of ceremony, and is reported by ``/health`` so an unprotected deployment is
visible rather than silent.
"""

from __future__ import annotations

import logging
import secrets
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Role(StrEnum):
    VIEWER = "viewer"  # read only
    OPERATOR = "operator"  # + queue, cancel and retry calls
    ADMIN = "admin"  # + edit agents, suppression, purge the dead letter queue

    @property
    def rank(self) -> int:
        return {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}[self]

    def can(self, required: Role) -> bool:
        return self.rank >= required.rank


def parse_api_keys(raw: str | None) -> dict[str, Role]:
    """``"abc:admin,def:viewer"`` -> ``{"abc": Role.ADMIN, "def": Role.VIEWER}``."""
    keys: dict[str, Role] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        key, _, role = entry.partition(":")
        key = key.strip()
        if not key:
            continue
        try:
            keys[key] = Role(role.strip().lower() or Role.VIEWER)
        except ValueError:
            logger.warning("ignoring API key with an unknown role", extra={"role": role})
    return keys


def resolve_role(presented: str | None, settings: Settings) -> Role | None:
    """The role for a presented key, or ``None`` when it is not recognised."""
    configured = parse_api_keys(settings.api_keys)
    if not configured:
        return Role.ADMIN  # authentication disabled
    if not presented:
        return None
    # Compare against every key so the time taken does not reveal which
    # prefix matched.
    matched: Role | None = None
    for key, role in configured.items():
        if secrets.compare_digest(presented, key):
            matched = role
    return matched


def require(minimum: Role):
    """Dependency factory: rejects anything below ``minimum``."""

    async def guard(
        settings: Annotated[Settings, Depends(get_settings)],
        x_api_key: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Role:
        presented = x_api_key
        if not presented and authorization and authorization.lower().startswith("bearer "):
            presented = authorization[7:].strip()

        role = resolve_role(presented, settings)
        if role is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "a valid API key is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not role.can(minimum):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"this endpoint requires the {minimum} role; your key is {role}",
            )
        return role

    return guard


RequireViewer = Depends(require(Role.VIEWER))
RequireOperator = Depends(require(Role.OPERATOR))
RequireAdmin = Depends(require(Role.ADMIN))
