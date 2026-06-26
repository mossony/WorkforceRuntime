from __future__ import annotations

import math
from typing import Any


def coerce_confidence(value: Any, *, default: float | None) -> float | None:
    if value is None:
        return default
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(confidence):
        return default
    if confidence > 1:
        if confidence <= 10:
            confidence /= 10
        elif confidence <= 100:
            confidence /= 100
    return min(max(confidence, 0.0), 1.0)
