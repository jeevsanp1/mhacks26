from __future__ import annotations

from claw.config import Settings
from claw.gateway.protocol import dumps, req
from claw.gateway.server import create_app
from claw.runner import AgentRunner
from fastapi.testclient import TestClient


def test_handshake_requires_connect(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    app = create_app(settings, runner)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(dumps(req("1", "health", {})))
            frame = ws.receive_json()
            assert frame["ok"] is False
            assert frame["error"]["code"] == "HANDSHAKE_REQUIRED"


def test_connect_hello_ok(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    app = create_app(settings, runner)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(
                dumps(
                    req(
                        "1",
                        "connect",
                        {"role": "operator", "client": {"id": "test"}},
                    )
                )
            )
            frame = ws.receive_json()
            assert frame["ok"] is True
            assert frame["payload"]["type"] == "hello-ok"
            assert "health" in frame["payload"]["features"]["methods"]


def test_auth_token_required(settings: Settings) -> None:
    settings = Settings(
        model=settings.model,
        workspace=settings.workspace,
        state_dir=settings.state_dir,
        gateway_host=settings.gateway_host,
        gateway_port=settings.gateway_port,
        gateway_token="secret",
    )
    runner = AgentRunner.create(settings)
    app = create_app(settings, runner)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(dumps(req("1", "connect", {"role": "operator"})))
            frame = ws.receive_json()
            assert frame["ok"] is False
            assert frame["error"]["code"] == "UNAUTHORIZED"
