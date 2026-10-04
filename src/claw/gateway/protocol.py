"""OpenClaw-inspired WebSocket frame helpers."""

from __future__ import annotations

import json
from typing import Any


PROTOCOL_VERSION = 1
ADVERTISED_METHODS = [
    "connect",
    "health",
    "agent",
    "agent.wait",
    "chat.history",
    "connectors.list",
    "connectors.fetch",
    "channel.inbound",
    "cron.add",
    "cron.list",
    "cron.get",
    "cron.update",
    "cron.remove",
    "cron.run",
]
ADVERTISED_EVENTS = ["agent", "tick", "cron", "heartbeat", "channel"]


def parse_frame(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("frame must be a JSON object")
    return data


def req(id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "req", "id": id, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def res_ok(id: str, payload: Any = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "res", "id": id, "ok": True}
    if payload is not None:
        frame["payload"] = payload
    return frame


def res_err(
    id: str,
    code: str,
    message: str,
    *,
    details: Any = None,
    retryable: bool = False,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if details is not None:
        error["details"] = details
    return {"type": "res", "id": id, "ok": False, "error": error}


def event(name: str, payload: Any, *, seq: int | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "event", "event": name, "payload": payload}
    if seq is not None:
        frame["seq"] = seq
    return frame


def dumps(frame: dict[str, Any]) -> str:
    return json.dumps(frame, separators=(",", ":"), default=str)
