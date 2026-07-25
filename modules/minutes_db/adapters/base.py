"""Shared adapter contracts and unified HTTP client exports."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
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


def _regulations_fetch_compat(
    url: str,
    cache_dir: str | os.PathLike[str] | None = None,
    timeout: float = 30,
) -> FetchResult:
    """Compatibility bridge for regulations until its later migration step."""

    client = HttpClient(
        cache_dir or Path(__file__).resolve().parent.parent / ".cache",
        user_agent=str(globals().get("USER_AGENT", MINUTES_USER_AGENT)),
        timeout=timeout,
        min_interval_seconds=float(
            globals().get("MIN_REQUEST_INTERVAL_SECONDS", 1.5)
        ),
    )
    # The old signature carries no tier, and regulations fetches its index
    # pages through this bridge. INDEX is the safe reading: a needless refetch
    # costs one request, a permanently cached index is the staleness bug this
    # whole change exists to fix.
    return client.fetch(url, tier=CacheTier.INDEX)


def __getattr__(name: str) -> Any:
    """Expose only the temporary names required by regulations."""

    if name == "USER_AGENT":
        return MINUTES_USER_AGENT
    if name == "MIN_REQUEST_INTERVAL_SECONDS":
        return 1.5
    if name == "polite_fetch":
        return _regulations_fetch_compat
    raise AttributeError(name)


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
