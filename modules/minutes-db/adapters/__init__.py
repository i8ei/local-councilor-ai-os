"""Adapter interfaces and implementations for the minutes database."""

from .base import (
    Adapter,
    FetchError,
    FetchResult,
    MinutesAdapter,
    RobotsDeniedError,
    polite_fetch,
)

__all__ = [
    "Adapter",
    "FetchError",
    "FetchResult",
    "MinutesAdapter",
    "RobotsDeniedError",
    "polite_fetch",
]
