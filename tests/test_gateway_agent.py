from __future__ import annotations

from claw.config import Settings
from claw.gateway.protocol import dumps, req
from claw.gateway.server import create_app
from claw.runner import AgentRunner
from fastapi.testclient import TestClient


def test_agent_rpc_returns_run_id_then_wait(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    app = create_app(settings, runner)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(dumps(req("1", "connect", {"role": "operator"})))
            assert ws.receive_json()["ok"] is True

            ws.send_text(
                dumps(
                    req(
                        "2",
                        "agent",
                        {
                            "message": "ping",
                            "sessionKey": "gw",
                            "idempotencyKey": "once",
                        },
                    )
                )
            )

            # Drain until we get the agent response (events may arrive first)
            run_id = None
            for _ in range(50):
                frame = ws.receive_json()
                if frame.get("type") == "res" and frame.get("id") == "2":
                    assert frame["ok"] is True
                    run_id = frame["payload"]["runId"]
                    break

            assert run_id

            ws.send_text(dumps(req("3", "agent.wait", {"runId": run_id})))
            for _ in range(100):
                frame = ws.receive_json()
                if frame.get("type") == "res" and frame.get("id") == "3":
                    assert frame["ok"] is True
                    assert frame["payload"]["status"] == "ok"
                    return

            raise AssertionError("agent.wait response not received")
