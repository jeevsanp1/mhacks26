from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from claw import connectors
from claw.config import Settings
from claw.gateway.protocol import dumps, req
from claw.gateway.server import create_app
from claw.runner import AgentRunner

NESSIE = "https://api.nessieisreal.com"
FINCH = "https://api.finchnode.com/demo/v1"

FINCH_RECORD = {
    "id": "patient-demo-001",
    "synthetic": True,
    "sources": [{"organization": "Northstar (Synthetic)"}],
    "data": {
        "demographics": {"name": "Morgan Rivera", "gender": "female", "birthDate": "1988-04-17"},
        "conditions": [{"name": "Type 2 diabetes", "status": "active", "severity": "Moderate", "onsetDate": "2021-03-12"}],
        "medications": [{"name": "Metformin", "frequency": "Twice daily", "status": "active", "prescriber": "Dr. Demo"}],
        "labs": [{"name": "Hemoglobin A1c", "value": "6.4", "unit": "%", "interpretation": "H", "date": "2026-07-18T15:30:00Z"}],
    },
}


@respx.mock
def test_health_connector_normalizes_finchnode_record(settings: Settings) -> None:
    respx.get(f"{FINCH}/users/patient-demo-001/records").respond(json=FINCH_RECORD)
    panel = connectors.fetch_connector("health", settings)
    assert panel["status"] == "live"
    assert "Patient" in panel["summary"] and "1 flagged" in panel["summary"]
    titles = {s["title"]: s for s in panel["sections"]}
    assert titles["Labs"]["rows"][0][:3] == ["Hemoglobin A1c", "6.4 %", "H"]
    assert "Type 2 diabetes" in connectors.panel_to_text(panel)


@respx.mock
def test_health_connector_reports_http_errors(settings: Settings) -> None:
    respx.get(f"{FINCH}/users/patient-demo-001/records").respond(status_code=500)
    panel = connectors.fetch_connector("health", settings)
    assert panel["status"] == "error" and panel["sections"] == []


@respx.mock
def test_nessie_falls_back_to_mock_when_account_is_empty(settings: Settings) -> None:
    respx.get(f"{NESSIE}/customers").respond(json=[])
    panel = connectors.fetch_connector("nessie", settings)
    assert panel["status"] == "mock"
    assert {s["title"] for s in panel["sections"]} == {"Accounts", "Recent purchases"}


@respx.mock
def test_nessie_live_pulls_accounts_and_purchases(settings: Settings) -> None:
    respx.get(f"{NESSIE}/customers").respond(json=[{"_id": "c1", "first_name": "Sam", "last_name": "Lee"}])
    respx.get(f"{NESSIE}/customers/c1/accounts").respond(
        json=[{"_id": "a1", "type": "Checking", "nickname": "Main", "balance": 100, "rewards": 0}]
    )
    respx.get(f"{NESSIE}/accounts/a1/purchases").respond(
        json=[{"purchase_date": "2026-10-01", "description": "Coffee", "amount": 4.5, "status": "completed"}]
    )
    panel = connectors.fetch_connector("nessie", settings)
    assert panel["status"] == "live"
    assert panel["sections"][1]["rows"][0] == ["2026-10-01", "Coffee", "$4.50", "completed"]


@respx.mock
def test_nessie_falls_back_to_mock_when_unreachable(settings: Settings) -> None:
    respx.get(f"{NESSIE}/customers").mock(side_effect=httpx.ConnectError("down"))
    panel = connectors.fetch_connector("nessie", settings)
    assert panel["status"] == "mock" and "ConnectError" in panel["debug"]


def test_unknown_connector_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError):
        connectors.fetch_connector("nope", settings)


@respx.mock
def test_gateway_ws_lists_and_fetches_connectors(settings: Settings) -> None:
    respx.get(f"{FINCH}/users/patient-demo-001/records").respond(json=FINCH_RECORD)
    app = create_app(settings, AgentRunner.create(settings))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_text(dumps(req("1", "connect", {"role": "operator"})))
        assert ws.receive_json()["ok"] is True

        ws.send_text(dumps(req("2", "connectors.list", {})))
        ids = [c["id"] for c in ws.receive_json()["payload"]["connectors"]]
        assert ids == ["nessie", "health", "calendar"]

        ws.send_text(dumps(req("3", "connectors.fetch", {"id": "health"})))
        assert ws.receive_json()["payload"]["status"] == "live"

        ws.send_text(dumps(req("4", "connectors.fetch", {"id": "bogus"})))
        assert ws.receive_json()["ok"] is False


def test_dashboard_page_has_connector_panels(settings: Settings) -> None:
    app = create_app(settings, AgentRunner.create(settings))
    with TestClient(app) as client:
        html = client.get("/").text
    assert "connectors.fetch" in html and "nessie" in html and "health" in html


@respx.mock
def test_agent_tools_register_and_return_connector_text(settings: Settings) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from claw.memory import ClawDeps

    respx.get(f"{FINCH}/users/patient-demo-001/records").respond(json=FINCH_RECORD)
    respx.get(f"{NESSIE}/customers").respond(json=[])
    agent: Agent[ClawDeps, str] = Agent(TestModel(), deps_type=ClawDeps)
    connectors.register_connector_tools(agent)
    assert {"nessie", "health"} <= set(agent._function_toolset.tools)
