"""OpenClaw-style session queue modes."""

from __future__ import annotations

from typing import Literal

QueueMode = Literal["steer", "followup", "collect", "interrupt"]
QUEUE_MODES: frozenset[str] = frozenset({"steer", "followup", "collect", "interrupt"})
DEFAULT_QUEUE_MODE: QueueMode = "followup"


def normalize_queue_mode(value: str | None, *, default: QueueMode = DEFAULT_QUEUE_MODE) -> QueueMode:
    if value is None:
        return default
    mode = str(value).strip().lower()
    if mode not in QUEUE_MODES:
        raise ValueError(
            f"invalid queue mode {value!r}; use steer|followup|collect|interrupt"
        )
    return mode  # type: ignore[return-value]
