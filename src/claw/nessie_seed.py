"""Fill a Nessie sandbox account with a believable month of transactions.

Writes to the Nessie API (NESSIE_API_KEY), so run it deliberately: `claw nessie-seed`.
Without a key, or while Nessie is unreachable, there is nothing to seed; the dashboard's
bundled mock data is used instead and the assistant's `nessie_transaction` tool edits that
in memory.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

import httpx

from claw.config import Settings
from claw.connectors import TIMEOUT_S, _live_payee, _nessie_get, _nessie_post as _post_api, _post_live

CUSTOMER = {
    "first_name": "Rishi",
    "last_name": "Lokesh",
    "address": {"street_number": "500", "street_name": "State Street", "city": "Ann Arbor", "state": "MI", "zip": "48104"},
}
ACCOUNTS = [
    {"type": "Checking", "nickname": "Everyday Checking", "rewards": 0, "balance": 3500},
    {"type": "Savings", "nickname": "Rainy Day", "rewards": 0, "balance": 12000},
    {"type": "Credit Card", "nickname": "Venture Rewards", "rewards": 18450, "balance": 0},
]
MERCHANT = {
    "name": "Everyday Spending",
    "category": ["general"],
    "address": CUSTOMER["address"],
    "geocode": {"lat": 42.2808, "lng": -83.743},
}
# (description, low, high, how many over the period)
SPENDING = [
    ("Blue Bottle Coffee", 4.5, 9.0, 8),
    ("Whole Foods Market", 38.0, 120.0, 5),
    ("Uber", 11.0, 34.0, 4),
    ("Shell Gas", 30.0, 58.0, 3),
    ("Pharmacy", 8.0, 42.0, 2),
    ("Restaurant", 18.0, 64.0, 4),
    ("Amazon", 12.0, 140.0, 3),
]


_post = _post_api


def seed_nessie(settings: Settings, *, days: int = 45, seed: int | None = None) -> str:
    """Create a customer, accounts and a merchant if missing, then post transactions. Returns a summary."""
    if not settings.nessie_api_key:
        raise RuntimeError("NESSIE_API_KEY is not set; there is no Nessie account to seed.")
    rng = random.Random(seed)
    base = settings.nessie_base_url.rstrip("/")
    key = settings.nessie_api_key
    today = date.today()
    with httpx.Client(timeout=TIMEOUT_S) as client:
        customers = _nessie_get(client, base, "/customers", key)
        customer = customers[0] if customers else _post(client, base, key, "/customers", CUSTOMER)
        cid = customer["_id"]
        accounts = _nessie_get(client, base, f"/customers/{cid}/accounts", key)
        if not accounts:
            accounts = [_post(client, base, key, f"/customers/{cid}/accounts", a) for a in ACCOUNTS]
        _live_payee(client, base, key, "ethan", {a.get("_id") for a in accounts})  # the demo recipient
        merchants = _nessie_get(client, base, "/merchants", key)
        merchant_id = merchants[0]["_id"] if merchants else _post(client, base, key, "/merchants", MERCHANT)["_id"]

        def day_ago(n: int) -> str:
            return (today - timedelta(days=n)).isoformat()

        def add(kind: str, amount: float, description: str, when: str, account=None, to_account=None) -> None:
            _post_live(client, base, key, accounts, kind, round(amount, 2), description,
                       account, to_account, when, merchant_id=merchant_id)

        count = 0
        for description, lo, hi, n in SPENDING:
            for _ in range(n):
                add("purchase", rng.uniform(lo, hi), description, day_ago(rng.randint(0, days)))
                count += 1
        for n in range(0, days, 14):
            add("deposit", 2400, "Paycheck", day_ago(n + 3), account="checking")
            count += 1
        add("transfer", 250, "Monthly savings", day_ago(7), account="checking", to_account="savings")
        count += 1
    return f"Posted {count} transactions to Nessie customer {cid}."
