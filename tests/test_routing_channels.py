from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from claw.channels.dispatch import dispatch_inbound
from claw.config import Settings
from claw.gateway.server import create_app
from claw.memory import load_bootstrap_context
from claw.routing import (
    Peer,
    build_main_session_key,
    build_session_key,
    is_private_session_key,
    parse_inbound_params,
    resolve_route,
)
from claw.runner import AgentRunner
from claw.sessions import SessionStore, session_filename


def test_dm_main_collapses_across_channels() -> None:
    tg = build_session_key(
        channel="telegram",
        peer=Peer(kind="direct", id="111"),
        dm_scope="main",
    )
    slack = build_session_key(
        channel="slack",
        peer=Peer(kind="direct", id="U999"),
        dm_scope="main",
    )
    assert tg == slack == build_main_session_key("main")


def test_dm_per_channel_peer_isolates() -> None:
    a = build_session_key(
        channel="telegram",
        peer=Peer(kind="direct", id="111"),
        dm_scope="per-channel-peer",
    )
    b = build_session_key(
        channel="slack",
        peer=Peer(kind="direct", id="111"),
        dm_scope="per-channel-peer",
    )
    assert a != b
    assert a == "agent:main:telegram:direct:111"


def test_group_isolated_by_default() -> None:
    g = build_session_key(
        channel="discord",
        peer=Peer(kind="group", id="G1"),
        group_scope="per-group",
    )
    assert g == "agent:main:discord:group:g1"
    assert not is_private_session_key(g)
    assert is_private_session_key("agent:main:main")
    assert is_private_session_key("agent:main:telegram:direct:111")


def test_identity_links_collapse_peers() -> None:
    key = build_session_key(
        channel="telegram",
        peer=Peer(kind="direct", id="111"),
        dm_scope="per-peer",
        identity_links={"owner": ["telegram:111", "slack:U1"]},
    )
    assert key == "agent:main:direct:owner"


def test_session_filename_encodes_colons(settings: Settings) -> None:
    path = SessionStore(settings).path_for("agent:main:main")
    assert path.name == "agent=main=main.json"
    assert session_filename("agent:main:slack:group:g1") == "agent=main=slack=group=g1.json"


def test_group_bootstrap_omits_memory(settings: Settings) -> None:
    from claw.memory import ensure_workspace

    ensure_workspace(settings)
    (settings.agent_home / "MEMORY.md").write_text(
        "# MEMORY.md\n\n- private-only\n", encoding="utf-8"
    )
    assert "private-only" in load_bootstrap_context(
        settings.agent_home, "agent:main:main"
    )
    assert "private-only" not in load_bootstrap_context(
        settings.agent_home, "agent:main:slack:group:room1"
    )


@pytest.mark.asyncio
async def test_dispatch_inbound_isolates_group_from_dm(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    dm = await dispatch_inbound(
        runner,
        {
            "message": "dm secret ALPHA",
            "channel": "telegram",
            "peer": {"kind": "direct", "id": "u1"},
        },
    )
    group = await dispatch_inbound(
        runner,
        {
            "message": "group chat BETA",
            "channel": "telegram",
            "peer": {"kind": "group", "id": "g9"},
        },
    )
    assert dm["route"]["sessionKey"] == "agent:main:main"
    assert group["route"]["sessionKey"] == "agent:main:telegram:group:g9"
    assert dm["route"]["sessionKey"] != group["route"]["sessionKey"]
    assert dm["run"]["status"] == "ok"
    assert group["run"]["status"] == "ok"


def test_http_channel_inbound(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    app = create_app(settings, runner)
    with TestClient(app) as client:
        res = client.post(
            "/v1/channel/inbound",
            json={
                "message": "hello from slack",
                "channel": "slack",
                "peer": {"kind": "channel", "id": "C123"},
                "wait": True,
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["route"]["sessionKey"] == "agent:main:slack:channel:c123"
        assert body["outbound"]["delivery"]["channel"] == "slack"
        assert body["run"]["status"] == "ok"


def test_parse_inbound_requires_peer() -> None:
    with pytest.raises(ValueError):
        parse_inbound_params({"message": "hi", "channel": "telegram"})


def test_resolve_route_delivery() -> None:
    inbound = parse_inbound_params(
        {
            "text": "hi",
            "channel": "telegram",
            "peerId": "42",
            "peerKind": "direct",
            "messageId": "m1",
        }
    )
    route = resolve_route(inbound, dm_scope="main")
    assert route.delivery.to == "dm:42"
    assert route.delivery.reply_to_id == "m1"
