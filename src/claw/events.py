"""OpenClaw-inspired agent event shapes and Pydantic AI stream mapping."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)

StreamName = Literal["lifecycle", "assistant", "tool"]
LifecyclePhase = Literal["start", "end", "error"]


@dataclass
class AgentEvent:
    run_id: str
    seq: int
    stream: StreamName
    session_key: str
    data: dict[str, Any]
    ts: float = field(default_factory=lambda: time.time() * 1000)

    def to_payload(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "seq": self.seq,
            "stream": self.stream,
            "sessionKey": self.session_key,
            "ts": self.ts,
            "data": self.data,
        }


@dataclass
class EventSequencer:
    """Per-run sequence numbers for agent event streams."""

    run_id: str
    session_key: str
    _seq: int = 0

    def next(self, stream: StreamName, data: dict[str, Any]) -> AgentEvent:
        self._seq += 1
        return AgentEvent(
            run_id=self.run_id,
            seq=self._seq,
            stream=stream,
            session_key=self.session_key,
            data=data,
        )

    def lifecycle(self, phase: LifecyclePhase, **extra: Any) -> AgentEvent:
        data: dict[str, Any] = {"phase": phase, **extra}
        if phase == "start" and "startedAt" not in data:
            data["startedAt"] = int(time.time() * 1000)
        if phase in {"end", "error"} and "endedAt" not in data:
            data["endedAt"] = int(time.time() * 1000)
        return self.next("lifecycle", data)

    def assistant_delta(self, text: str) -> AgentEvent:
        return self.next("assistant", {"delta": text})

    def tool(
        self,
        *,
        phase: Literal["call", "result"],
        tool_name: str,
        tool_call_id: str,
        args: Any = None,
        result: Any = None,
    ) -> AgentEvent:
        data: dict[str, Any] = {
            "phase": phase,
            "toolName": tool_name,
            "toolCallId": tool_call_id,
        }
        if args is not None:
            data["args"] = args
        if result is not None:
            data["result"] = _truncate(result)
        return self.next("tool", data)


def map_pydantic_event(seq: EventSequencer, event: Any) -> list[AgentEvent]:
    """Map a Pydantic AI stream event to zero or more OpenClaw-style events."""
    out: list[AgentEvent] = []

    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        if event.part.content:
            out.append(seq.assistant_delta(event.part.content))
    elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        if event.delta.content_delta:
            out.append(seq.assistant_delta(event.delta.content_delta))
    elif isinstance(event, FunctionToolCallEvent):
        out.append(
            seq.tool(
                phase="call",
                tool_name=event.part.tool_name,
                tool_call_id=event.part.tool_call_id,
                args=event.part.args,
            )
        )
    elif isinstance(event, FunctionToolResultEvent):
        part = event.part
        content = getattr(part, "content", None)
        if content is None:
            content = event.content
        out.append(
            seq.tool(
                phase="result",
                tool_name=getattr(part, "tool_name", None) or "tool",
                tool_call_id=event.tool_call_id,
                result=content,
            )
        )
    return out


def _truncate(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        if isinstance(value, str) and len(value) > limit:
            return value[:limit] + "…"
        return value
    # Avoid dumping screenshot bytes into gateway/TUI payloads.
    data = getattr(value, "data", None)
    media_type = getattr(value, "media_type", None)
    if isinstance(data, (bytes, bytearray)) and media_type:
        return f"<{media_type} {len(data)} bytes>"
    if isinstance(value, (list, tuple)):
        return [_truncate(v, limit) for v in value]
    text = repr(value)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


truncate_payload = _truncate
