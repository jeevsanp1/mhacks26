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
    assert {s["title"] for s in panel["sections"]} == {"Accounts", "Recent purchases", "Send money to"}


@respx.mock
def test_nessie_live_pulls_accounts_and_purchases(settings: Settings) -> None:
    respx.get(f"{NESSIE}/customers").respond(json=[{"_id": "c1", "first_name": "Sam", "last_name": "Lee"}])
    respx.get(f"{NESSIE}/customers/c1/accounts").respond(
        json=[{"_id": "a1", "type": "Checking", "nickname": "Main", "balance": 100, "rewards": 0}]
    )
    respx.get(f"{NESSIE}/accounts/a1/purchases").respond(
        json=[{"purchase_date": "2026-10-01", "description": "Coffee", "amount": 4.5, "status": "completed"}]
    )
    respx.get(f"{NESSIE}/accounts/a1/deposits").respond(
        json=[{"_id": "d1", "transaction_date": "2026-09-30", "description": "Paycheck", "amount": 2400}]
    )
    respx.get(f"{NESSIE}/accounts/a1/transfers").respond(json=[])
    panel = connectors.fetch_connector("nessie", settings)
    assert panel["status"] == "live"
    assert panel["sections"][1]["rows"][0] == ["2026-10-01", "Coffee", "$4.50", "completed"]
    assert panel["sections"][2]["rows"] == [["2026-09-30", "Deposit", "Paycheck", "$2,400.00"]]


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


@pytest.fixture(autouse=True)
def _reset_mock() -> None:
    connectors.reset_nessie_mock()


@respx.mock
def test_transaction_in_mock_updates_panel_and_balances(settings: Settings) -> None:
    respx.get(f"{NESSIE}/customers").respond(json=[])
    out = connectors.make_nessie_transaction(settings, "purchase", 20, "Uber")
    assert out["status"] == "mock"
    connectors.make_nessie_transaction(settings, "transfer", 100, "Savings", account="checking", to_account="savings")
    panel = connectors.fetch_connector("nessie", settings)
    accounts = {r[0]: r[2] for r in panel["sections"][0]["rows"]}
    assert accounts["Everyday Checking"] == "$4,701.37" and accounts["Rainy Day"] == "$12,750.00"
    assert panel["sections"][1]["rows"][0][:3] == [panel["sections"][1]["rows"][0][0], "Uber", "$20.00"]
    assert {x["title"]: x["rows"] for x in panel["sections"]}["Deposits and transfers"][0][1:] == [
        "Transfer", "Savings (to Rainy Day)", "$100.00"
    ]


def test_transaction_validation(settings: Settings) -> None:
    with pytest.raises(ValueError):
        connectors.make_nessie_transaction(settings, "refund", 5)
    with pytest.raises(ValueError):
        connectors.make_nessie_transaction(settings, "purchase", -5)
    with pytest.raises(ValueError):
        connectors.make_nessie_transaction(settings, "transfer", 5, account="checking")


@respx.mock
def test_transaction_live_posts_to_nessie(settings: Settings) -> None:
    respx.get(f"{NESSIE}/customers").respond(json=[{"_id": "c1"}])
    respx.get(f"{NESSIE}/customers/c1/accounts").respond(
        json=[{"_id": "a1", "type": "Checking", "nickname": "Main"}, {"_id": "a2", "type": "Savings", "nickname": "Rainy"}]
    )
    respx.get(f"{NESSIE}/merchants").respond(json=[{"_id": "m1"}])
    purchase = respx.post(f"{NESSIE}/accounts/a1/purchases").respond(201, json={"objectCreated": {}})
    transfer = respx.post(f"{NESSIE}/accounts/a1/transfers").respond(201, json={"objectCreated": {}})
    assert connectors.make_nessie_transaction(settings, "purchase", 12.5, "Coffee")["status"] == "live"
    body = purchase.calls.last.request.content.decode()
    assert '"merchant_id":"m1"' in body.replace(" ", "") and '"amount":12.5' in body.replace(" ", "")
    connectors.make_nessie_transaction(settings, "transfer", 50, account="main", to_account="savings")
    assert '"payee_id":"a2"' in transfer.calls.last.request.content.decode().replace(" ", "")


@respx.mock
def test_transaction_live_rejection_is_an_error_not_a_mock_fallback(settings: Settings) -> None:
    respx.get(f"{NESSIE}/customers").respond(json=[{"_id": "c1"}])
    respx.get(f"{NESSIE}/customers/c1/accounts").respond(json=[{"_id": "a1", "type": "Checking"}])
    respx.get(f"{NESSIE}/merchants").respond(json=[{"_id": "m1"}])
    respx.post(f"{NESSIE}/accounts/a1/purchases").respond(400, json={"message": "bad"})
    with pytest.raises(RuntimeError):
        connectors.make_nessie_transaction(settings, "purchase", 5)


@respx.mock
def test_send_money_to_ethan_in_mock(settings: Settings) -> None:
    respx.get(f"{NESSIE}/customers").respond(json=[])
    out = connectors.make_nessie_transaction(settings, "transfer", 50, "Dinner", to_account="Ethan")
    assert "Ethan Park" in out["message"]
    panel = connectors.fetch_connector("nessie", settings)
    sections = {s["title"]: s["rows"] for s in panel["sections"]}
    assert sections["Accounts"][0][2] == "$4,771.37"
    assert sections["Send money to"] == [["Ethan Park", "Checking", "$1,550.00"]]
    assert sections["Deposits and transfers"][0][1:3] == ["Transfer", "Dinner (to Ethan Park)"]
    with pytest.raises(ValueError):
        connectors.make_nessie_transaction(settings, "transfer", 5, to_account="Nobody")


@respx.mock
def test_send_money_creates_ethan_in_live_nessie(settings: Settings) -> None:
    respx.get(f"{NESSIE}/customers").respond(json=[{"_id": "c1"}])
    respx.get(f"{NESSIE}/customers/c1/accounts").respond(json=[{"_id": "a1", "type": "Checking", "nickname": "Main"}])
    respx.post(f"{NESSIE}/customers").respond(201, json={"objectCreated": {"_id": "c2"}})
    respx.post(f"{NESSIE}/customers/c2/accounts").respond(201, json={"objectCreated": {"_id": "a9", "nickname": "Ethan Park"}})
    transfer = respx.post(f"{NESSIE}/accounts/a1/transfers").respond(201, json={"objectCreated": {}})
    out = connectors.make_nessie_transaction(settings, "transfer", 25, to_account="ethan")
    assert out["status"] == "live" and "Ethan Park" in out["message"]
    assert '"payee_id":"a9"' in transfer.calls.last.request.content.decode().replace(" ", "")
