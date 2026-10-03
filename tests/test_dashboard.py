from __future__ import annotations

from claw.config import Settings
from claw.gateway.server import create_app
from claw.runner import AgentRunner
from fastapi.testclient import TestClient


def test_chat_page_served(settings: Settings) -> None:
    app = create_app(settings, AgentRunner.create(settings))
    with TestClient(app) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
        assert "Claw" in res.text
        assert "/ws" in res.text
        assert 'method", "agent"' in res.text or 'req("agent"' in res.text
