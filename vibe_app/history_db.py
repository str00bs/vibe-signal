from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any

from .actions import Action

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    account_acct TEXT NOT NULL,
    account_display_name TEXT NOT NULL,
    account_avatar_url TEXT NOT NULL,
    status_url TEXT NOT NULL,
    status_text TEXT NOT NULL,
    intensity REAL NOT NULL,
    duration_s REAL NOT NULL,
    pattern TEXT NOT NULL,
    delivered INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class HistoryDB:
    def __init__(self, path: str):
        self.path = Path(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _record_sync(
        self,
        action: Action,
        account_acct: str,
        account_display_name: str,
        account_avatar_url: str,
        delivered: bool,
    ) -> None:
        self._conn.execute(
            "INSERT INTO interactions "
            "(ts, event_type, account_acct, account_display_name, account_avatar_url, "
            " status_url, status_text, intensity, duration_s, pattern, delivered) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                action.source_event,
                account_acct,
                account_display_name,
                account_avatar_url,
                action.source_status_url,
                action.source_text[:280],
                action.intensity,
                action.duration_s,
                action.pattern,
                1 if delivered else 0,
            ),
        )
        self._conn.commit()

    async def record(
        self,
        action: Action,
        account_acct: str,
        account_display_name: str,
        account_avatar_url: str,
        delivered: bool,
    ) -> None:
        await asyncio.to_thread(
            self._record_sync,
            action,
            account_acct,
            account_display_name,
            account_avatar_url,
            delivered,
        )

    def _recent_sync(self, limit: int) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT ts, event_type, account_acct, account_display_name, account_avatar_url, "
            "status_url, status_text, intensity, duration_s, pattern, delivered "
            "FROM interactions ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._recent_sync, limit)

    def _get_state_sync(self, key: str, default: str) -> str:
        cur = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

    def _set_state_sync(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    async def get_state(self, key: str, default: str) -> str:
        return await asyncio.to_thread(self._get_state_sync, key, default)

    async def set_state(self, key: str, value: str) -> None:
        await asyncio.to_thread(self._set_state_sync, key, value)

    def _leaderboard_sync(self, limit: int) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT account_acct, account_display_name, account_avatar_url, "
            "COUNT(*) AS count, SUM(duration_s) AS total_seconds "
            "FROM interactions WHERE delivered = 1 AND event_type != 'manual' "
            "GROUP BY account_acct ORDER BY count DESC LIMIT ?",
            (limit,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    async def leaderboard(self, limit: int = 5) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._leaderboard_sync, limit)
