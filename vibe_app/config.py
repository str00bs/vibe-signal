from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class ActionConfig:
    intensity: float
    duration_s: float
    pattern: str = "constant"


@dataclass
class Config:
    # Mastodon
    mastodon_api_base_url: str
    mastodon_access_token: str
    poll_interval_s: float = 15.0

    # Buttplug engine / toy
    engine_binary_path: Optional[str] = None
    engine_websocket_port: int = 12345
    device_name_filter: Optional[str] = None
    scan_timeout_s: float = 15.0
    # When true, stream the raw intiface-engine process log to stderr. Off by default -
    # it's noisy (every BLE advertisement it sees during a scan), so normal runs only get
    # our own connect/disconnect lines.
    debug: bool = False

    # Safety
    max_intensity: float = 1.0
    max_duration_s: float = 30.0
    max_queued_seconds: float = 300.0
    start_armed: bool = False
    # Default cooldown buffer (seconds) added on top of an action's own duration before the
    # next trigger is accepted. Adjustable at runtime from the dashboard; this is just the
    # value used the first time the app ever starts (later runs restore the dashboard value).
    rate_limit_buffer_s: float = 2.0

    # Event -> action mapping
    favourite_action: ActionConfig = field(
        default_factory=lambda: ActionConfig(intensity=0.2, duration_s=3, pattern="constant")
    )
    reblog_action: ActionConfig = field(
        default_factory=lambda: ActionConfig(intensity=0.5, duration_s=8, pattern="constant")
    )
    quote_action: ActionConfig = field(
        default_factory=lambda: ActionConfig(intensity=0.6, duration_s=10, pattern="escalate")
    )
    reply_default_intensity: float = 0.4
    reply_default_duration_s: float = 6.0

    # Dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8420

    # Storage
    db_path: str = "history.sqlite3"


def _action_from_dict(d: dict, default: ActionConfig) -> ActionConfig:
    return ActionConfig(
        intensity=float(d.get("intensity", default.intensity)),
        duration_s=float(d.get("duration_s", default.duration_s)),
        pattern=str(d.get("pattern", default.pattern)),
    )


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        print(
            f"Config file not found at {path}.\n"
            f"Copy config.example.yaml to config.yaml and fill in your Mastodon access token.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    raw = yaml.safe_load(path.read_text()) or {}
    cfg = Config(
        mastodon_api_base_url=raw["mastodon_api_base_url"],
        mastodon_access_token=raw["mastodon_access_token"],
    )

    for key in (
        "poll_interval_s",
        "engine_binary_path",
        "engine_websocket_port",
        "device_name_filter",
        "scan_timeout_s",
        "debug",
        "max_intensity",
        "max_duration_s",
        "max_queued_seconds",
        "start_armed",
        "rate_limit_buffer_s",
        "reply_default_intensity",
        "reply_default_duration_s",
        "dashboard_host",
        "dashboard_port",
        "db_path",
    ):
        if key in raw:
            setattr(cfg, key, raw[key])

    if "favourite_action" in raw:
        cfg.favourite_action = _action_from_dict(raw["favourite_action"], cfg.favourite_action)
    if "reblog_action" in raw:
        cfg.reblog_action = _action_from_dict(raw["reblog_action"], cfg.reblog_action)
    if "quote_action" in raw:
        cfg.quote_action = _action_from_dict(raw["quote_action"], cfg.quote_action)

    return cfg
