"""Throwaway script to smoke-test the two-column header + divider. Not part of the app."""
from __future__ import annotations

import asyncio

import uvicorn

from .config import Config
from .history_db import HistoryDB
from .mastodon_bot import MastodonBot
from .runtime_state import ArmedState, ModeState, RateLimitState
from .toy import ToyController
from .webapp import create_app


async def main() -> None:
    cfg = Config(mastodon_api_base_url="https://kinkycats.org", mastodon_access_token="x", dashboard_port=8427)
    db = HistoryDB("/tmp/smoke_history6.sqlite3")
    armed = ArmedState(db, False)
    await armed.init()
    mode = ModeState(db)
    await mode.init()
    rate_limit = RateLimitState(db, cfg.rate_limit_buffer_s)
    await rate_limit.init()
    toy = ToyController(cfg, rate_limit)
    bot = MastodonBot(cfg, toy, db, armed, mode)
    app = create_app(cfg, db, armed, mode, toy, bot, rate_limit)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8427, log_level="warning"))
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
