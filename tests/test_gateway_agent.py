from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

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


def test_chat_history_pairs_tool_calls_with_results(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    runner.store.save(
        "web",
        [
            ModelRequest(parts=[UserPromptPart(content="list files")]),
            ModelResponse(
                parts=[
                    TextPart(content="Looking."),
                    ToolCallPart(tool_name="ls", args={"path": "."}, tool_call_id="c1"),
                ]
            ),
            ModelRequest(
                parts=[ToolReturnPart(tool_name="ls", content="a.py\nb.py", tool_call_id="c1")]
            ),
            ModelResponse(parts=[TextPart(content="Two files.")]),
        ],
    )
    app = create_app(settings, runner)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(dumps(req("1", "connect", {"role": "operator"})))
            assert ws.receive_json()["ok"] is True
            ws.send_text(dumps(req("2", "chat.history", {"sessionKey": "web"})))
            frame = ws.receive_json()

    assert frame["ok"] is True
    assert frame["payload"]["entries"] == [
        {"kind": "user", "text": "list files"},
        {"kind": "assistant", "text": "Looking."},
        {
            "kind": "tool",
            "name": "ls",
            "toolCallId": "c1",
            "args": {"path": "."},
            "done": True,
            "result": "a.py\nb.py",
        },
        {"kind": "assistant", "text": "Two files."},
    ]
