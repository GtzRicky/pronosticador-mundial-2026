"""API-Football outbound adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import APIFootballProvider

__all__ = ["APIFootballProvider"]


def __getattr__(name: str):
    if name == "APIFootballProvider":
        from .provider import APIFootballProvider

        return APIFootballProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
