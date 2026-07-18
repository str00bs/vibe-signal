from __future__ import annotations

import json
from typing import Literal, Optional

from .history_db import HistoryDB

Mode = Literal["all", "specific"]


class ArmedState:
    """In-memory armed/disarmed flag, mirrored to the DB so the dashboard reflects it on reload."""

    def __init__(self, db: HistoryDB, start_armed: bool):
        self._db = db
        self._armed = start_armed

    @property
    def armed(self) -> bool:
        return self._armed

    async def set(self, armed: bool) -> None:
        self._armed = armed
        await self._db.set_state("armed", "1" if armed else "0")

    async def init(self) -> None:
        """Persist the startup armed state (from config) to the DB for the dashboard to read.

        Deliberately does NOT restore a previous session's armed state from the DB - every
        process start should require the user to consciously arm it again.
        """
        await self._db.set_state("armed", "1" if self._armed else "0")


class ModeState:
    """Which posts count: 'all' (default) or a single 'specific' post the user picked."""

    def __init__(self, db: HistoryDB):
        self._db = db
        self._mode: Mode = "all"
        self._target_status_id: Optional[str] = None
        self._target_status_url: Optional[str] = None
        self._target_status_text: Optional[str] = None

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def target_status_id(self) -> Optional[str]:
        return self._target_status_id

    def as_dict(self) -> dict:
        return {
            "mode": self._mode,
            "target_status_id": self._target_status_id,
            "target_status_url": self._target_status_url,
            "target_status_text": self._target_status_text,
        }

    async def set_all(self) -> None:
        self._mode = "all"
        self._target_status_id = None
        self._target_status_url = None
        self._target_status_text = None
        await self._persist()

    async def set_specific(self, status_id: str, status_url: str, status_text: str) -> None:
        self._mode = "specific"
        self._target_status_id = status_id
        self._target_status_url = status_url
        self._target_status_text = status_text
        await self._persist()

    async def _persist(self) -> None:
        await self._db.set_state("mode", json.dumps(self.as_dict()))

    async def init(self) -> None:
        """Always starts in 'all' mode on process start - same reasoning as armed defaulting off."""
        await self._persist()


class RateLimitState:
    """Configurable cooldown enforced between accepted triggers.

    The cooldown for a given action is that action's own duration plus this buffer, so a
    burst of favourites/boosts can't stack faster than the toy can physically keep up -
    unlike armed/mode, this is a tuning knob rather than a safety switch, so it *does*
    restore across restarts.
    """

    def __init__(self, db: HistoryDB, default_buffer_s: float):
        self._db = db
        self._buffer_s = max(0.0, default_buffer_s)

    @property
    def buffer_s(self) -> float:
        return self._buffer_s

    async def set(self, buffer_s: float) -> None:
        self._buffer_s = max(0.0, buffer_s)
        await self._db.set_state("rate_limit_buffer_s", str(self._buffer_s))

    async def init(self) -> None:
        stored = await self._db.get_state("rate_limit_buffer_s", "")
        if stored:
            try:
                self._buffer_s = max(0.0, float(stored))
            except ValueError:
                pass
        else:
            await self._db.set_state("rate_limit_buffer_s", str(self._buffer_s))
