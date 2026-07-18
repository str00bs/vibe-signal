from __future__ import annotations

import asyncio
import signal
import sys

import uvicorn

from .config import load_config
from .engine_manager import EngineProcess
from .history_db import HistoryDB
from .mastodon_bot import MastodonBot
from .runtime_state import ArmedState, ModeState, RateLimitState
from .toy import ToyController
from .webapp import create_app


async def async_main() -> None:
    config = load_config()

    db = HistoryDB(config.db_path)
    armed = ArmedState(db, config.start_armed)
    await armed.init()
    mode = ModeState(db)
    await mode.init()
    rate_limit = RateLimitState(db, config.rate_limit_buffer_s)
    await rate_limit.init()

    engine = EngineProcess(config)
    await engine.start()

    toy = ToyController(config, rate_limit)
    await toy.connect_and_scan()

    bot = MastodonBot(config, toy, db, armed, mode)
    app = create_app(config, db, armed, mode, toy, bot, rate_limit)

    server = uvicorn.Server(
        uvicorn.Config(
            app, host=config.dashboard_host, port=config.dashboard_port, log_level="warning"
        )
    )

    print(
        f"[main] dashboard at http://{config.dashboard_host}:{config.dashboard_port} "
        f"(starts {'ARMED' if armed.armed else 'DISARMED'} - arm it from the dashboard when ready)",
        file=sys.stderr,
    )

    bot_task = asyncio.create_task(bot.run())
    server_task = asyncio.create_task(server.serve())

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()
    print("[main] shutting down...", file=sys.stderr)

    bot_task.cancel()
    server.should_exit = True
    await asyncio.gather(server_task, return_exceptions=True)

    await toy.disconnect()
    await engine.stop()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
