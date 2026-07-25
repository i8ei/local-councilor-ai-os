"""Shared adapter contracts and unified HTTP client exports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from lcaios.http import (
    MINUTES_USER_AGENT,
    CacheTier,
    FetchError,
    FetchResult,
    HttpClient,
    RobotsDeniedError,
    RobotsUnavailableError,
)


class MinutesAdapter(ABC):
    """Minimal interface implemented by every minutes source adapter."""

    @abstractmethod
    def detect_capabilities(self) -> dict[str, Any]:
        """Describe supported discovery and extraction features."""

    @abstractmethod
    def list_meetings(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return source meeting descriptors, optionally capped by ``limit``."""

    @abstractmethod
    def fetch_meeting(self, meeting_id: str) -> dict[str, Any]:
        """Return one normalized meeting, its speeches, and provenance."""


# Keep the shorter name convenient for third-party adapters.
Adapter = MinutesAdapter


__all__ = [
    "Adapter",
    "CacheTier",
    "FetchError",
    "FetchResult",
    "HttpClient",
    "MINUTES_USER_AGENT",
    "MinutesAdapter",
    "RobotsDeniedError",
    "RobotsUnavailableError",
]
