from datetime import date

from fastapi.testclient import TestClient

from app import services
from app.main import app
from app.models import CardPayment, CardStatement, CreditCard, SentNotice


def setup_card(db, total=10000):
    card = CreditCard(name="Bonus", last4="1019", statement_day=26, due_day=6)
    db.add(card)
    db.commit()
    st = services.apply_statement(db, card, {"period_end": "2026-09-26", "due_date": "2026-10-06", "totals": {"TRY": total},
                                             "min_payment": total * 0.4}, None)
    db.commit()
    return card, st


def test_partial_then_full_payment(db):
    client = TestClient(app)
    card, st = setup_card(db)
    r = client.post(f"/api/cards/{card.id}/payments", json={"amount": 4000, "date": "2026-10-01", "statement_id": st.id})
    assert r.status_code == 200
    out = client.get("/api/cards").json()[0]
    s = out["statements"][0]
    assert (s["paid"], s["remaining"], s["is_paid"]) == ({"TRY": 4000}, {"TRY": 6000}, False)
    assert s["currency"] == "TRY" and s["totals"] == {"TRY": 10000}
    assert out["debts"] == {"TRY": 6000} and len(out["payments"]) == 1
    client.post(f"/api/cards/statements/{st.id}/paid?paid=true")  # the full remainder
    s = client.get("/api/cards").json()[0]["statements"][0]
    assert (s["remaining"], s["is_paid"]) == ({"TRY": 0}, True)
    # Deleting the payment restores the debt and the unpaid status
    pid = client.get("/api/cards").json()[0]["payments"][0]["id"]
    client.delete(f"/api/cards/payments/{pid}")
    out = client.get("/api/cards").json()[0]
    assert out["debts"] == {"TRY": 6000} and out["statements"][0]["is_paid"] is False


def test_payment_validation(db):
    client = TestClient(app)
    card, st = setup_card(db)
    other = CreditCard(name="Axess", statement_day=5, due_day=15)
    db.add(other)
    db.commit()
    assert client.post(f"/api/cards/{card.id}/payments", json={"amount": -5, "date": "2026-10-01"}).status_code == 422
    r = client.post(f"/api/cards/{other.id}/payments", json={"amount": 5, "date": "2026-10-01", "statement_id": st.id})
    assert r.status_code == 400


def test_reupload_keeps_payments_into_account(db):
    card, st = setup_card(db)
    services.add_card_payment(db, card, 300000, "TRY", date(2026, 10, 1), st.id, None)
    db.commit()
    services.apply_statement(db, card, {"period_end": "2026-09-26", "due_date": "2026-10-06", "totals": {"TRY": 10000}}, None)
    db.commit()
    assert card.debts == {"TRY": 700000}


def test_payments_are_not_expenses(db):
    client = TestClient(app)
    card, st = setup_card(db)
    client.post(f"/api/cards/{card.id}/payments", json={"amount": 4000, "date": date.today().isoformat(), "statement_id": st.id})
    assert client.get(f"/api/reports/summary?month={date.today():%Y-%m}").json()["expense"] == 0


def test_migration_creates_payments_for_old_paid_statements(db):
    card = CreditCard(name="Bonus", statement_day=26, due_day=6)
    db.add(card)
    db.commit()
    st = CardStatement(card_id=card.id, period_end=date(2026, 8, 26), due_date=date(2026, 9, 6), totals={"TRY": 500000}, paid=True)
    db.add(st)
    db.commit()
    services.migrate_paid_statements(db)
    services.migrate_paid_statements(db)  # second run does nothing
    pays = db.query(CardPayment).all()
    assert len(pays) == 1 and pays[0].amount_cents == 500000
    assert db.query(SentNotice).filter_by(key="migration:card_payments_v1").count() == 1
