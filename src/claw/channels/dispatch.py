"""Dispatch normalized inbound messages through the agent runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claw.config import Settings
from claw.routing import (
    DeliveryContext,
    InboundMessage,
    ResolvedRoute,
    parse_inbound_params,
    resolve_route,
)
from claw.runner import AcceptedRun, AgentRunner, RunSnapshot


@dataclass(frozen=True)
class OutboundMessage:
    text: str
    delivery: DeliveryContext
    session_key: str
    run_id: str
    status: str
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "delivery": self.delivery.to_payload(),
            "sessionKey": self.session_key,
            "runId": self.run_id,
            "status": self.status,
            "error": self.error,
        }


def route_inbound(inbound: InboundMessage, settings: Settings) -> ResolvedRoute:
    return resolve_route(
        inbound,
        dm_scope=settings.dm_scope,  # type: ignore[arg-type]
        group_scope=settings.group_scope,  # type: ignore[arg-type]
        identity_links=settings.identity_links,
    )


def start_inbound(
    runner: AgentRunner,
    inbound: InboundMessage,
    *,
    queue_mode: str | None = None,
    timeout_s: float | None = None,
    idempotency_key: str | None = None,
) -> tuple[ResolvedRoute, AcceptedRun]:
    route = route_inbound(inbound, runner.settings)
    accepted = runner.start_turn(
        inbound.text,
        session_key=route.session_key,
        queue_mode=queue_mode,
        timeout_s=timeout_s,
        idempotency_key=idempotency_key,
        delivery=route.delivery,
    )
    return route, accepted


async def dispatch_inbound(
    runner: AgentRunner,
    inbound: InboundMessage | dict[str, Any],
    *,
    queue_mode: str | None = None,
    timeout_s: float | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """Route + run (optionally wait). Returns route + accept/run payload."""
    if isinstance(inbound, dict):
        inbound = parse_inbound_params(inbound)
    route, accepted = start_inbound(
        runner,
        inbound,
        queue_mode=queue_mode,
        timeout_s=timeout_s,
    )
    payload: dict[str, Any] = {
        "route": route.to_payload(),
        "accepted": accepted.to_payload(),
    }
    if wait:
        snap = await runner.wait(accepted.run_id, timeout_s=timeout_s)
        payload["run"] = snap.to_payload()
        payload["outbound"] = outbound_from_snapshot(snap, route.delivery).to_payload()
    return payload


def outbound_from_snapshot(
    snap: RunSnapshot, delivery: DeliveryContext
) -> OutboundMessage:
    return OutboundMessage(
        text=snap.output or "",
        delivery=delivery,
        session_key=snap.session_key,
        run_id=snap.run_id,
        status=snap.status,
        error=snap.error,
    )
