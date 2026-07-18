from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterator, Literal, Optional

Pattern = Literal["constant", "pulse", "wave", "escalate", "burst"]

STEP_HZ = 10.0


@dataclass
class Action:
    intensity: float
    duration_s: float
    pattern: Pattern = "constant"
    source_event: str = "unknown"
    source_account: str = ""
    source_status_url: str = ""
    source_text: str = ""

    def clamp(self, max_intensity: float, max_duration_s: float) -> "Action":
        self.intensity = max(0.0, min(self.intensity, max_intensity))
        self.duration_s = max(0.1, min(self.duration_s, max_duration_s))
        return self


def setpoints(action: Action) -> Iterator[tuple[float, float]]:
    """Yield (elapsed_seconds, intensity) pairs at STEP_HZ for the given action's pattern."""
    n_steps = max(1, int(action.duration_s * STEP_HZ))
    for i in range(n_steps + 1):
        t = i / STEP_HZ
        elapsed_frac = min(t / action.duration_s, 1.0) if action.duration_s > 0 else 1.0

        if action.pattern == "constant":
            level = action.intensity
        elif action.pattern == "pulse":
            level = action.intensity if int(t * 2) % 2 == 0 else 0.0
        elif action.pattern == "wave":
            level = action.intensity * (0.5 + 0.5 * math.sin(2 * math.pi * t / 1.5))
        elif action.pattern == "escalate":
            level = action.intensity * elapsed_frac
        elif action.pattern == "burst":
            level = action.intensity if elapsed_frac < 0.5 else action.intensity * 0.15
        else:
            level = action.intensity

        yield t, max(0.0, min(level, 1.0))
