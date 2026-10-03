"""OpenClaw-inspired inbound routing → sessionKey + delivery context."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PeerKind = Literal["direct", "group", "channel", "thread"]
DmScope = Literal["main", "per-peer", "per-channel-peer", "per-account-channel-peer"]
GroupScope = Literal["main", "per-group"]

DEFAULT_AGENT_ID = "main"
DEFAULT_ACCOUNT_ID = "default"
DEFAULT_DM_SCOPE: DmScope = "main"
DEFAULT_GROUP_SCOPE: GroupScope = "per-group"

_SAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9._@+-]+")


def _seg(value: str | None, *, fallback: str = "unknown") -> str:
    raw = (value or "").strip().lower() or fallback
    return _SAFE_SEGMENT.sub("_", raw)[:120]


def normalize_agent_id(value: str | None) -> str:
    return _seg(value, fallback=DEFAULT_AGENT_ID)


def normalize_account_id(value: str | None) -> str:
    return _seg(value, fallback=DEFAULT_ACCOUNT_ID)


def normalize_dm_scope(value: str | None) -> DmScope:
    v = (value or DEFAULT_DM_SCOPE).strip().lower()
    if v not in {"main", "per-peer", "per-channel-peer", "per-account-channel-peer"}:
        raise ValueError(f"invalid dmScope {value!r}")
    return v  # type: ignore[return-value]


def normalize_group_scope(value: str | None) -> GroupScope:
    v = (value or DEFAULT_GROUP_SCOPE).strip().lower()
    if v not in {"main", "per-group"}:
        raise ValueError(f"invalid groupScope {value!r}")
    return v  # type: ignore[return-value]


def normalize_peer_kind(value: str | None) -> PeerKind:
    v = (value or "direct").strip().lower()
    if v in {"dm", "direct", "private"}:
        return "direct"
    if v in {"group", "room"}:
        return "group"
    if v in {"channel", "chan"}:
        return "channel"
    if v in {"thread"}:
        return "thread"
    raise ValueError(f"invalid peer.kind {value!r}")


@dataclass(frozen=True)
class Peer:
    kind: PeerKind
    id: str

    def to_payload(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}


@dataclass(frozen=True)
class DeliveryContext:
    """Where the reply should go (OpenClaw delivery context subset)."""

    channel: str
    account_id: str = DEFAULT_ACCOUNT_ID
    to: str | None = None
    thread_id: str | None = None
    reply_to_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "channel": self.channel,
            "accountId": self.account_id,
        }
        if self.to:
            out["to"] = self.to
        if self.thread_id:
            out["threadId"] = self.thread_id
        if self.reply_to_id:
            out["replyToId"] = self.reply_to_id
        return out


@dataclass(frozen=True)
class InboundMessage:
    """Normalized inbound message from any channel adapter."""

    text: str
    channel: str
    peer: Peer
    account_id: str = DEFAULT_ACCOUNT_ID
    agent_id: str = DEFAULT_AGENT_ID
    message_id: str | None = None
    thread_id: str | None = None
    sender_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def delivery(self) -> DeliveryContext:
        to = self.peer.id
        if self.peer.kind == "direct":
            to = f"dm:{self.peer.id}"
        elif self.peer.kind == "group":
            to = f"group:{self.peer.id}"
        elif self.peer.kind == "channel":
            to = f"channel:{self.peer.id}"
        return DeliveryContext(
            channel=_seg(self.channel),
            account_id=normalize_account_id(self.account_id),
            to=to,
            thread_id=self.thread_id,
            reply_to_id=self.message_id,
        )


@dataclass(frozen=True)
class ResolvedRoute:
    agent_id: str
    session_key: str
    main_session_key: str
    channel: str
    account_id: str
    peer: Peer
    dm_scope: DmScope
    group_scope: GroupScope
    delivery: DeliveryContext
    matched_by: str = "default"

    def to_payload(self) -> dict[str, Any]:
        return {
            "agentId": self.agent_id,
            "sessionKey": self.session_key,
            "mainSessionKey": self.main_session_key,
            "channel": self.channel,
            "accountId": self.account_id,
            "peer": self.peer.to_payload(),
            "dmScope": self.dm_scope,
            "groupScope": self.group_scope,
            "delivery": self.delivery.to_payload(),
            "matchedBy": self.matched_by,
        }


def build_main_session_key(agent_id: str = DEFAULT_AGENT_ID) -> str:
    return f"agent:{normalize_agent_id(agent_id)}:main"


def build_session_key(
    *,
    agent_id: str = DEFAULT_AGENT_ID,
    channel: str,
    account_id: str | None = None,
    peer: Peer,
    dm_scope: DmScope = DEFAULT_DM_SCOPE,
    group_scope: GroupScope = DEFAULT_GROUP_SCOPE,
    identity_links: dict[str, list[str]] | None = None,
) -> str:
    """OpenClaw-shaped session key from channel + peer."""
    agent = normalize_agent_id(agent_id)
    main = build_main_session_key(agent)
    ch = _seg(channel)
    acct = normalize_account_id(account_id)
    peer_id = _resolve_linked_peer_id(
        channel=ch,
        peer_id=peer.id,
        identity_links=identity_links,
    )
    peer_id = _seg(peer_id)

    if peer.kind == "direct":
        if dm_scope == "main":
            return main
        if dm_scope == "per-peer":
            return f"agent:{agent}:direct:{peer_id}"
        if dm_scope == "per-channel-peer":
            return f"agent:{agent}:{ch}:direct:{peer_id}"
        # per-account-channel-peer
        return f"agent:{agent}:{ch}:{acct}:direct:{peer_id}"

    if group_scope == "main":
        return main
    # thread inherits parent peer kind labeling
    kind = "thread" if peer.kind == "thread" else peer.kind
    return f"agent:{agent}:{ch}:{kind}:{peer_id}"


def resolve_route(
    inbound: InboundMessage,
    *,
    dm_scope: DmScope = DEFAULT_DM_SCOPE,
    group_scope: GroupScope = DEFAULT_GROUP_SCOPE,
    identity_links: dict[str, list[str]] | None = None,
) -> ResolvedRoute:
    agent_id = normalize_agent_id(inbound.agent_id)
    session_key = build_session_key(
        agent_id=agent_id,
        channel=inbound.channel,
        account_id=inbound.account_id,
        peer=inbound.peer,
        dm_scope=dm_scope,
        group_scope=group_scope,
        identity_links=identity_links,
    )
    return ResolvedRoute(
        agent_id=agent_id,
        session_key=session_key,
        main_session_key=build_main_session_key(agent_id),
        channel=_seg(inbound.channel),
        account_id=normalize_account_id(inbound.account_id),
        peer=Peer(kind=inbound.peer.kind, id=_seg(inbound.peer.id)),
        dm_scope=dm_scope,
        group_scope=group_scope,
        delivery=inbound.delivery(),
        matched_by="default",
    )


def parse_inbound_params(params: dict[str, Any]) -> InboundMessage:
    """Parse gateway/HTTP inbound payload into InboundMessage."""
    text = params.get("text") or params.get("message") or params.get("prompt")
    if not text or not isinstance(text, str) or not text.strip():
        raise ValueError("inbound text/message is required")
    channel = params.get("channel") or params.get("provider") or "cli"
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("channel is required")

    peer_raw = params.get("peer")
    if isinstance(peer_raw, dict):
        kind = normalize_peer_kind(str(peer_raw.get("kind") or "direct"))
        peer_id = str(peer_raw.get("id") or "").strip()
    else:
        kind = normalize_peer_kind(str(params.get("peerKind") or params.get("kind") or "direct"))
        peer_id = str(
            params.get("peerId")
            or params.get("from")
            or params.get("senderId")
            or params.get("userId")
            or ""
        ).strip()
    if not peer_id:
        raise ValueError("peer.id (or peerId/from) is required")

    meta = params.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        raise ValueError("metadata must be an object")

    return InboundMessage(
        text=text.strip(),
        channel=channel.strip(),
        peer=Peer(kind=kind, id=peer_id),
        account_id=str(params.get("accountId") or params.get("account") or DEFAULT_ACCOUNT_ID),
        agent_id=str(params.get("agentId") or params.get("agent") or DEFAULT_AGENT_ID),
        message_id=(
            str(params["messageId"])
            if params.get("messageId") is not None
            else (str(params["id"]) if params.get("id") is not None else None)
        ),
        thread_id=str(params["threadId"]) if params.get("threadId") is not None else None,
        sender_name=str(params["senderName"]) if params.get("senderName") is not None else None,
        metadata=dict(meta or {}),
    )


def is_private_session_key(session_key: str) -> bool:
    """True for DMs / main / explicit private chats (MEMORY.md eligible)."""
    key = (session_key or "").strip().lower()
    if not key or key in {"main", "global"}:
        return True
    if key.startswith(("cron:", "hook:", "webhook:")):
        return False
    # OpenClaw-shaped: agent:<id>:main or ...:direct:<peer>
    if key.startswith("agent:"):
        parts = key.split(":")
        if len(parts) >= 3 and parts[2] == "main":
            return True
        if "direct" in parts:
            return True
        # group/channel/thread segments are shared contexts
        if any(p in {"group", "channel", "thread", "room"} for p in parts):
            return False
        # agent:main:chat-... style private UI threads
        if len(parts) >= 3 and parts[2].startswith("chat"):
            return True
        return False
    # Legacy prefixes
    if key.startswith(("group:", "channel:", "cron:", "webhook:", "hook:")):
        return False
    return True


def delivery_prompt_block(delivery: DeliveryContext | None, *, session_key: str) -> str:
    if delivery is None:
        return f"## Delivery\n\nsessionKey: {session_key}\nchannel: cli (embedded)"
    lines = [
        "## Delivery",
        f"sessionKey: {session_key}",
        f"channel: {delivery.channel}",
        f"accountId: {delivery.account_id}",
    ]
    if delivery.to:
        lines.append(f"replyTo: {delivery.to}")
    if delivery.thread_id:
        lines.append(f"threadId: {delivery.thread_id}")
    if delivery.reply_to_id:
        lines.append(f"replyToMessageId: {delivery.reply_to_id}")
    lines.append(
        "Reply for this conversation only. Do not assume other channels share this transcript."
    )
    return "\n".join(lines)


def _resolve_linked_peer_id(
    *,
    channel: str,
    peer_id: str,
    identity_links: dict[str, list[str]] | None,
) -> str:
    """Map channel:peer aliases onto a canonical peer id when configured."""
    if not identity_links:
        return peer_id
    needle = f"{channel}:{peer_id}".lower()
    bare = peer_id.lower()
    for canonical, aliases in identity_links.items():
        canon = (canonical or "").strip()
        if not canon:
            continue
        for alias in aliases or []:
            a = (alias or "").strip().lower()
            if a == needle or a == bare or a.endswith(f":{bare}"):
                return canon
    return peer_id


def route_to_dict(route: ResolvedRoute) -> dict[str, Any]:
    return asdict(route)
