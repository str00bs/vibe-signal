from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .actions import Action
from .config import Config
from .history_db import HistoryDB
from .mastodon_bot import MastodonBot
from .reply_parser import parse_reply
from .runtime_state import ArmedState, ModeState, RateLimitState
from .toy import ToyController

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    config: Config,
    db: HistoryDB,
    armed: ArmedState,
    mode: ModeState,
    toy: ToyController,
    bot: MastodonBot,
    rate_limit: RateLimitState,
) -> FastAPI:
    app = FastAPI(title="VibeSignal")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "armed": armed.armed,
                "favourite_action": config.favourite_action,
                "reblog_action": config.reblog_action,
                "quote_action": config.quote_action,
            },
        )

    @app.get("/api/history.json")
    async def api_history(limit: int = 20):
        return JSONResponse(await db.recent(limit))

    @app.get("/api/armed")
    async def api_armed_get():
        return {"armed": armed.armed}

    @app.post("/api/armed")
    async def api_armed_set(request: Request):
        body = await request.json()
        await armed.set(bool(body.get("armed", False)))
        return {"armed": armed.armed}

    @app.post("/api/panic-stop")
    async def api_panic_stop():
        await toy.panic_stop()
        return {"ok": True}

    @app.post("/api/trigger")
    async def api_trigger(request: Request):
        body = await request.json()
        kind = body.get("kind")

        if kind == "favourite":
            cfg = config.favourite_action
            action = Action(cfg.intensity, cfg.duration_s, cfg.pattern)
        elif kind == "boost":
            cfg = config.reblog_action
            action = Action(cfg.intensity, cfg.duration_s, cfg.pattern)
        elif kind == "quote":
            cfg = config.quote_action
            action = Action(cfg.intensity, cfg.duration_s, cfg.pattern)
        elif kind == "custom":
            text = body.get("text")
            if text:
                action = parse_reply(text, config)
            else:
                action = Action(
                    intensity=float(body.get("intensity", config.reply_default_intensity)),
                    duration_s=float(body.get("duration_s", config.reply_default_duration_s)),
                    pattern=body.get("pattern") or "constant",
                )
        else:
            return JSONResponse({"error": f"unknown kind {kind!r}"}, status_code=400)

        action.source_event = "manual"
        action.clamp(config.max_intensity, config.max_duration_s)

        reason = None if armed.armed else "disarmed"
        if armed.armed:
            result = toy.enqueue(action)
            reason = None if result == "ok" else result
        delivered = reason is None

        await db.record(
            action,
            account_acct="dashboard",
            account_display_name="Dashboard",
            account_avatar_url="",
            delivered=delivered,
        )
        return {
            "delivered": delivered,
            "reason": reason,
            "intensity": action.intensity,
            "duration_s": action.duration_s,
            "pattern": action.pattern,
        }

    @app.get("/api/mode")
    async def api_mode_get():
        return mode.as_dict()

    @app.post("/api/mode")
    async def api_mode_set(request: Request):
        body = await request.json()
        if body.get("mode") == "specific":
            status_id = body.get("status_id")
            if not status_id:
                return JSONResponse({"error": "status_id required"}, status_code=400)
            await mode.set_specific(
                str(status_id), body.get("status_url", ""), body.get("status_text", "")
            )
        else:
            await mode.set_all()
        return mode.as_dict()

    @app.get("/api/recent-posts")
    async def api_recent_posts():
        try:
            return await bot.recent_own_statuses()
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=503)

    @app.post("/api/resolve-post")
    async def api_resolve_post(request: Request):
        body = await request.json()
        try:
            return await bot.resolve_status_url(body.get("url", ""))
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/leaderboard")
    async def api_leaderboard():
        return await db.leaderboard(5)

    @app.get("/api/rate-limit")
    async def api_rate_limit_get():
        return {"buffer_s": rate_limit.buffer_s, "cooldown_remaining_s": toy.cooldown_remaining_s}

    @app.post("/api/rate-limit")
    async def api_rate_limit_set(request: Request):
        body = await request.json()
        try:
            buffer_s = float(body.get("buffer_s"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "buffer_s must be a number"}, status_code=400)
        await rate_limit.set(buffer_s)
        return {"buffer_s": rate_limit.buffer_s}

    @app.get("/api/toy")
    async def api_toy():
        return toy.status()

    @app.post("/api/toy/rescan")
    async def api_toy_rescan():
        return await toy.rescan()

    @app.get("/api/queue")
    async def api_queue():
        return toy.queue_status()

    return app
