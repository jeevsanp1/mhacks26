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
        assert "EWOK" in res.text
        assert "/ws" in res.text
        assert 'method", "agent"' in res.text or 'req("agent"' in res.text


def test_single_page_has_chat_voice_and_connectors(settings: Settings) -> None:
    app = create_app(settings, AgentRunner.create(settings))
    with TestClient(app) as client:
        html = client.get("/").text
        assert 'id="tab-voice"' in html and "/ws/voice" in html
        assert 'id="connector-tabs"' in html
        redirect = client.get("/voice", follow_redirects=False)
        assert redirect.status_code in (302, 307) and redirect.headers["location"] == "/#voice"


def test_design_tokens_served(settings: Settings) -> None:
    app = create_app(settings, AgentRunner.create(settings))
    with TestClient(app) as client:
        assert "/static/tokens.css" in client.get("/").text
        res = client.get("/static/tokens.css")
        assert res.status_code == 200
        assert "text/css" in res.headers.get("content-type", "")
        assert "--ds-primary" in res.text
