from __future__ import annotations

import html
import re

from .actions import Action
from .config import Config

_TAG_RE = re.compile(r"<[^>]+>")
_INTENSITY_RE = re.compile(r"(\d{1,3})\s*%")
_DURATION_RE = re.compile(r"(\d{1,4})\s*s(?:ec(?:onds)?)?\b", re.IGNORECASE)

# emoji -> pattern shape. First match found in the text wins.
EMOJI_PATTERNS = {
    "\U0001F30A": "wave",  # 🌊
    "\U0001F493": "pulse",  # 💓
    "\U0001F497": "pulse",  # 💗
    "\U0001F495": "pulse",  # 💕
    "⚡": "burst",  # ⚡
    "\U0001F525": "escalate",  # 🔥
}


def strip_html(content: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", content)).strip()


def parse_reply(text: str, config: Config) -> Action:
    """Turn reply text into an Action. Falls back to configured reply defaults."""
    plain = strip_html(text)

    intensity_match = _INTENSITY_RE.search(plain)
    duration_match = _DURATION_RE.search(plain)

    intensity = (
        int(intensity_match.group(1)) / 100.0
        if intensity_match
        else config.reply_default_intensity
    )
    duration_s = (
        float(duration_match.group(1)) if duration_match else config.reply_default_duration_s
    )

    pattern = "constant"
    for emoji, shape in EMOJI_PATTERNS.items():
        if emoji in plain:
            pattern = shape
            break

    action = Action(
        intensity=intensity,
        duration_s=duration_s,
        pattern=pattern,
        source_event="reply",
        source_text=plain,
    )
    return action.clamp(config.max_intensity, config.max_duration_s)
