"""Thin channel adapters — normalize inbound, deliver outbound."""

from claw.channels.dispatch import dispatch_inbound, outbound_from_snapshot
from claw.routing import (
    DeliveryContext,
    InboundMessage,
    Peer,
    ResolvedRoute,
    parse_inbound_params,
    resolve_route,
)

__all__ = [
    "DeliveryContext",
    "InboundMessage",
    "Peer",
    "ResolvedRoute",
    "dispatch_inbound",
    "outbound_from_snapshot",
    "parse_inbound_params",
    "resolve_route",
]
