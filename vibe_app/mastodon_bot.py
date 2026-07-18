from __future__ import annotations

import asyncio
import re
import sys

from mastodon import Mastodon

from .actions import Action
from .config import Config
from .history_db import HistoryDB
from .reply_parser import parse_reply, strip_html
from .runtime_state import ArmedState, ModeState
from .toy import ToyController

NOTIFICATION_TYPES = ["favourite", "reblog", "mention", "quote"]
_TRAILING_ID_RE = re.compile(r"/(\d+)\s*$")


class MastodonBot:
    def __init__(
        self, config: Config, toy: ToyController, db: HistoryDB, armed: ArmedState, mode: ModeState
    ):
        self.config = config
        self.toy = toy
        self.db = db
        self.armed = armed
        self.mode = mode
        self.client = Mastodon(
            access_token=config.mastodon_access_token,
            api_base_url=config.mastodon_api_base_url,
        )
        self._my_account_id: str | None = None
        self._since_id: str | None = None
        self._latest: dict | None = None

    async def run(self) -> None:
        me = await asyncio.to_thread(self.client.me)
        self._my_account_id = me["id"]
        print(f"[mastodon] logged in as @{me['acct']}", file=sys.stderr)

        latest = await asyncio.to_thread(
            self.client.notifications, types=NOTIFICATION_TYPES, limit=1
        )
        self._latest = latest if latest else None
        self._since_id = self._latest[0]["id"] if self._latest else None
        start_msg = f"""[mastodon] Starting with latest notification:
-> Type: {self._latest[0]['type']}(id:{self._since_id})
-> From: {self._latest[0]['account']['acct']} 
-> Status: {self._latest[0]['status']["url"]}"""
        print(start_msg, file=sys.stderr)
        print("[mastodon] ...(ignoring older backlog)", file=sys.stderr)

        while True:
            try:
                await self._poll_once()
            except Exception as e:
                print(f"[mastodon] poll error: {e}", file=sys.stderr)
            await asyncio.sleep(self.config.poll_interval_s)

    async def _poll_once(self) -> None:
        notifications = await asyncio.to_thread(
            self.client.notifications,
            types=NOTIFICATION_TYPES,
            since_id=self._since_id,
            limit=40,
        )
        if not notifications:
            return

        # Mastodon returns newest-first; process oldest-first for chronological history.
        for notif in reversed(notifications):
            await self._handle_notification(notif)

        self._since_id = notifications[0]["id"]

    def _in_scope(self, status_id) -> bool:
        """Whether a status counts under the current mode ('all' or a specific target post)."""
        if self.mode.mode != "specific":
            return True
        return status_id is not None and str(status_id) == str(self.mode.target_status_id)

    async def _handle_notification(self, notif: dict) -> None:
        event_type = notif["type"]
        status = notif.get("status")
        account = notif["account"]

        if event_type == "mention":
            if status is None or status.get("in_reply_to_account_id") != self._my_account_id:
                return  # plain mention, not a reply to us - ignore
            if not self._in_scope(status.get("in_reply_to_id")):
                return
            action = parse_reply(status["content"], self.config)
            action.source_event = "reply"
        elif event_type == "favourite":
            if not self._in_scope(status["id"] if status else None):
                return
            cfg = self.config.favourite_action
            action = Action(cfg.intensity, cfg.duration_s, cfg.pattern, source_event="favourite")
        elif event_type == "reblog":
            if not self._in_scope(status["id"] if status else None):
                return
            cfg = self.config.reblog_action
            action = Action(cfg.intensity, cfg.duration_s, cfg.pattern, source_event="boost")
        elif event_type == "quote":
            if not self._in_scope(status["id"] if status else None):
                return
            cfg = self.config.quote_action
            action = Action(cfg.intensity, cfg.duration_s, cfg.pattern, source_event="quote")
        else:
            return

        action.clamp(self.config.max_intensity, self.config.max_duration_s)
        action.source_account = account["acct"]
        if status is not None:
            action.source_status_url = status.get("url", "")
            if not action.source_text and status.get("content"):
                action.source_text = strip_html(status["content"])

        reason = None if self.armed.armed else "disarmed"
        if self.armed.armed:
            result = self.toy.enqueue(action)
            reason = None if result == "ok" else result
        delivered = reason is None

        await self.db.record(
            action,
            account_acct=account["acct"],
            account_display_name=account.get("display_name") or account["acct"],
            account_avatar_url=account.get("avatar", ""),
            delivered=delivered,
        )
        print(
            f"[mastodon] {event_type} from @{account['acct']} -> "
            f"{action.pattern} {action.intensity:.0%} for {action.duration_s:.0f}s "
            f"({'queued' if delivered else f'logged only ({reason})'})",
            file=sys.stderr,
        )

    async def recent_own_statuses(self, limit: int = 20) -> list[dict]:
        """Your own recent statuses, for the dashboard's post picker."""
        if self._my_account_id is None:
            raise RuntimeError("still logging in to Mastodon, try again in a moment")
        statuses = await asyncio.to_thread(
            self.client.account_statuses,
            self._my_account_id,
            limit=limit,
            exclude_reblogs=True,
        )
        return [
            {
                "id": str(s["id"]),
                "url": s.get("url", ""),
                "text": strip_html(s.get("content", ""))[:140],
                "created_at": s["created_at"].isoformat() if s.get("created_at") else "",
            }
            for s in statuses
        ]

    async def resolve_status_url(self, url: str) -> dict:
        """Resolve a pasted post URL (same-instance only) to {id, url, text}."""
        match = _TRAILING_ID_RE.search(url.strip())
        if not match:
            raise ValueError(f"Couldn't find a post id in {url!r}")
        status = await asyncio.to_thread(self.client.status, match.group(1))
        return {
            "id": str(status["id"]),
            "url": status.get("url", ""),
            "text": strip_html(status.get("content", ""))[:140],
        }
