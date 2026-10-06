from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import services
from app.main import app
from app.models import AuditLog, CreditCard, Document, InstallmentPlan, Transaction


@pytest.fixture
def client(db):
    return TestClient(app)


def entries(client):
    return client.get("/api/audit").json()


def test_create_update_delete_and_undo(db, client):
    tx = client.post("/api/transactions", json={"amount": 50, "date": "2026-10-01", "payment_method": "cash", "merchant": "Coffee"}).json()
    client.put(f"/api/transactions/{tx['id']}", json={"amount": 75, "date": "2026-10-01", "payment_method": "cash", "merchant": "Coffee"})
    h = entries(client)
    assert [e["summary"] for e in h[:2]] == ["Transaction edited: Coffee 75.00 TRY", "Transaction added: Coffee 50.00 TRY"]
    assert h[0]["user"] == "Test" and h[0]["can_undo"] and not h[1]["can_undo"]  # changed after creation

    assert client.post(f"/api/audit/{h[0]['id']}/undo").status_code == 200
    assert db.get(Transaction, tx["id"]).amount_cents == 5000
    assert client.post(f"/api/audit/{h[0]['id']}/undo").status_code == 409  # cannot be undone twice

    client.delete(f"/api/transactions/{tx['id']}")
    del_entry = entries(client)[0]
    client.post(f"/api/audit/{del_entry['id']}/undo")
    db.expire_all()
    assert db.get(Transaction, tx["id"]).merchant == "Coffee"


def test_undo_restores_installment_plan(db, client):
    card = client.post("/api/cards", json={"name": "Bonus", "statement_day": 26, "due_day": 6}).json()
    tx = client.post("/api/transactions", json={"amount": 1200, "date": "2026-10-01", "payment_method": "card",
                                                "card_id": card["id"], "installment_count": 3, "merchant": "Electronics Store"}).json()
    client.delete(f"/api/transactions/{tx['id']}")
    assert db.query(InstallmentPlan).count() == 0
    client.post(f"/api/audit/{entries(client)[0]['id']}/undo")
    db.expire_all()
    restored = db.get(Transaction, tx["id"])
    assert restored.installment_plan.count == 3


def test_document_commit_undo(db, client):
    card = CreditCard(name="Bonus", last4="1019", statement_day=26, due_day=6, debts={"TRY": 12345})
    db.add(card)
    db.commit()
    telegram_tx = services.create_transaction(db, kind="expense", amount_cents=50000, currency="TRY", on=date(2026, 9, 14),
                                              category_id=1, user_id=None, payment_method="cash", card_id=None,
                                              merchant="Mario pizza", note="Telegram")
    db.commit()
    doc = Document(filename="e.pdf", stored_path="/x", mime="application/pdf", status="review")
    db.add(doc)
    db.commit()
    rows = [
        {"include": True, "mode": "transaction", "kind": "expense", "date": "2026-09-10", "amount": 100, "currency": "TRY",
         "merchant": "Walmart", "category_id": 1, "payment_method": "card", "card_id": card.id},
        {"include": False, "mode": "transaction", "kind": "expense", "date": "2026-09-15", "amount": 500, "currency": "TRY",
         "merchant": "MARIO PIZZA DOWNTOWN", "category_id": 2, "payment_method": "card", "card_id": card.id, "match": telegram_tx.id},
    ]
    body = {"draft": {"doc_type": "statement", "card_id": card.id, "rows": rows,
                      "statement": {"period_end": "2026-09-26", "due_date": "2026-10-06", "currency": "TRY", "totals": {"TRY": 600}}}}
    assert client.post(f"/api/documents/{doc.id}/confirm", json=body).status_code == 200
    db.expire_all()
    assert db.get(CreditCard, card.id).debts == {"TRY": 60000}
    assert db.get(Transaction, telegram_tx.id).card_id == card.id
    e = entries(client)[0]
    assert e["summary"] == "Statement saved: 1 transaction, 1 match" and e["can_undo"]
    client.post(f"/api/audit/{e['id']}/undo")
    db.expire_all()
    assert db.query(Transaction).count() == 1  # only the Telegram record is left
    assert db.get(Transaction, telegram_tx.id).card_id is None  # the match was undone
    assert db.get(CreditCard, card.id).debts == {"TRY": 12345}
    assert db.get(Document, doc.id).status == "review"


def test_payment_undo_restores_debt(db, client):
    card = client.post("/api/cards", json={"name": "Bonus", "statement_day": 26, "due_day": 6, "debts": {"TRY": 1000}}).json()
    client.post(f"/api/cards/{card['id']}/payments", json={"amount": 400, "date": "2026-10-01"})
    assert client.get("/api/cards").json()[0]["debts"] == {"TRY": 600}
    client.post(f"/api/audit/{entries(client)[0]['id']}/undo")
    out = client.get("/api/cards").json()[0]
    assert out["debts"] == {"TRY": 1000} and out["payments"] == []


def test_transaction_list_shows_last_change(db, client):
    tx = client.post("/api/transactions", json={"amount": 50, "date": "2026-10-01", "payment_method": "cash", "merchant": "Coffee"}).json()
    row = client.get("/api/transactions").json()[0]
    assert row["id"] == tx["id"] and row["last_change"]["by"] == "Test"


def test_budget_summary_names_category(db, client):
    client.put("/api/budgets", json={"category_id": 1, "limit": 3000, "month": "2026-09"})
    assert entries(client)[0]["summary"] == "Budget added: Groceries · September 2026 · 3,000.00 TRY"
