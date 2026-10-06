from datetime import date, timedelta

from app import insights
from app.models import Category, Transaction


def test_insights_tips(db):
    today = date(2026, 10, 5)
    cat = {c.name: c.id for c in db.query(Category)}
    rows = []
    for i in range(60):
        d = today - timedelta(days=i)
        rows.append(Transaction(kind="expense", amount_cents=50000, date=d, merchant="BIM K429", category_id=cat["Groceries"]))
        if i % 3 == 0:
            rows.append(Transaction(kind="expense", amount_cents=60000, date=d, merchant="KEBAPCI", category_id=cat["Restaurants & Cafes"]))
    for i in range(3):
        rows.append(Transaction(kind="expense", amount_cents=19999, date=today - timedelta(days=30 * i), merchant="APPLE.COM/BILL"))
    db.add_all(rows)
    db.commit()
    r = insights.analyze(db, 90, today)
    kinds = {t["kind"] for t in r["tips"]}
    assert {"subscriptions", "eating", "frequent", "top"} <= kinds
    assert r["categories"][0]["name"] == "Groceries" and r["subscriptions"][0]["name"] == "APPLE.COM/BILL"
    assert r["saving_total"] > 0 and r["uncategorized_share"] < 0.1


def test_tips_have_drilldown_ids(db):
    from fastapi.testclient import TestClient
    from app.main import app
    test_insights_tips(db)
    r = insights.analyze(db, 90, date(2026, 10, 5))
    eating = next(t for t in r["tips"] if t["kind"] == "eating")
    assert len(eating["tx_ids"]) == 20 and all(c["tx_ids"] for c in r["categories"])
    rows = TestClient(app).post("/api/transactions/by-ids", json={"ids": eating["tx_ids"][:3]}).json()
    assert len(rows) == 3 and rows[0]["merchant"] == "KEBAPCI"


def test_insights_generic_usd_base(db):
    """A US household: amounts and tip texts are in USD, thresholds scale to the base currency."""
    from app import prefs

    prefs.save(db, base_currency="USD", currencies=["USD", "EUR"], region="", setup_done=True)
    today = date(2026, 10, 5)
    cat = {c.name: c.id for c in db.query(Category)}
    rows = []
    for i in range(60):
        d = today - timedelta(days=i)
        rows.append(Transaction(kind="expense", amount_cents=1200, currency="USD", date=d, merchant="Trader Joes",
                                category_id=cat["Groceries"]))
        if i % 3 == 0:
            rows.append(Transaction(kind="expense", amount_cents=2500, currency="USD", date=d, merchant="Chipotle",
                                    category_id=cat["Restaurants & Cafes"]))
        if i % 7 == 0:
            rows.append(Transaction(kind="expense", amount_cents=3000, currency="USD", date=d, merchant="DoorDash",
                                    category_id=cat["Restaurants & Cafes"]))
    for i in range(3):
        rows.append(Transaction(kind="expense", amount_cents=1549, currency="USD", date=today - timedelta(days=30 * i),
                                merchant="NETFLIX.COM"))
    db.add_all(rows)
    db.commit()
    r = insights.analyze(db, 90, today)
    kinds = {t["kind"] for t in r["tips"]}
    assert {"subscriptions", "eating", "delivery", "frequent", "top"} <= kinds
    text = " ".join(t["text"] for t in r["tips"])
    assert "USD" in text and "TRY" not in text
    assert r["categories"][0]["name"] in ("Groceries", "Restaurants & Cafes")
