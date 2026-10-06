"""Regression tests for bugs found in an external review."""
import io
from datetime import date

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import finance, services
from app.config import UPLOAD_DIR
from app.main import app
from app.models import CardStatement, CreditCard, Document, FxRate, Transaction


@pytest.fixture
def client(db):
    return TestClient(app)


def review_doc(db, rows, doc_type="receipt", statement=None, card_id=None):
    doc = Document(filename="x", stored_path="/x", mime="image/jpeg", status="review")
    doc.result = {"draft": {"doc_type": doc_type, "card_id": card_id, "statement": statement, "rows": rows}}
    db.add(doc)
    db.commit()
    return doc


def row(**kw):
    base = {"include": True, "mode": "transaction", "kind": "expense", "date": "2026-10-01", "amount": 100,
            "currency": "TRY", "merchant": "Walmart", "category_id": 1, "payment_method": "cash", "card_id": None,
            "installment_count": None, "installment_no": None, "duplicate_of": None, "items": None}
    base.update(kw)
    return base


# ---- P1: draft validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [{"amount": -50}, {"amount": 0}, {"currency": "XYZ"}, {"currency": "EURO"}, {"kind": "transfer"},
     {"installment_count": 3, "installment_no": 5}, {"date": "yesterday"}, {"merchant": "x" * 500}],
)
def test_confirm_rejects_invalid_rows(db, client, bad):
    doc = review_doc(db, [row()])
    r = client.post(f"/api/documents/{doc.id}/confirm", json={"draft": {"doc_type": "receipt", "rows": [row(**bad)]}})
    assert r.status_code == 422
    assert db.query(Transaction).count() == 0


def test_confirm_rejects_wrong_kind_category(db, client):
    doc = review_doc(db, [row()])
    salary = finance.category_by_name(db, "Salary", "income")
    r = client.post(f"/api/documents/{doc.id}/confirm", json={"draft": {"doc_type": "receipt", "rows": [row(category_id=salary)]}})
    assert r.status_code == 400 and "income" in r.json()["detail"]
    db.refresh(doc)
    assert doc.status == "review"  # lock released, can be retried


def test_confirm_twice_creates_once(db, client):
    doc = review_doc(db, [row()])
    body = {"draft": {"doc_type": "receipt", "rows": [row()]}}
    assert client.post(f"/api/documents/{doc.id}/confirm", json=body).status_code == 200
    assert client.post(f"/api/documents/{doc.id}/confirm", json=body).status_code == 409
    assert db.query(Transaction).count() == 1


def test_claim_document_is_atomic(db):
    doc = review_doc(db, [row()])
    assert services.claim_document(db, doc.id) is True
    assert services.claim_document(db, doc.id) is False


# ---- P1: duplicate detection ------------------------------------------------------


def add_tx(db, amount, on, merchant, kind="expense", cat=1, note=""):
    tx = services.create_transaction(db, kind=kind, amount_cents=amount * 100, currency="TRY", on=on,
                                     category_id=cat, user_id=None, payment_method="cash", card_id=None,
                                     merchant=merchant, note=note)
    db.commit()
    return tx


def test_income_never_matches_expense(db):
    add_tx(db, 500, date(2026, 10, 1), "Salary", kind="income", cat=finance.category_by_name(db, "Salary", "income"))
    assert finance.match_candidates(db, "expense", 50000, "TRY", date(2026, 10, 1), "Walmart") == []


def test_other_merchant_is_only_a_suggestion(db):
    tx = add_tx(db, 500, date(2026, 10, 1), "Shell")
    cands = finance.match_candidates(db, "expense", 50000, "TRY", date(2026, 10, 2), "Walmart")
    assert cands[0]["tx_id"] == tx.id and cands[0]["strong"] is False


def test_same_merchant_is_strong(db):
    tx = add_tx(db, 500, date(2026, 10, 1), "Mario pizza")
    cands = finance.match_candidates(db, "expense", 50000, "TRY", date(2026, 10, 2), "MARIO PIZZA DOWNTOWN")
    assert cands[0]["tx_id"] == tx.id and cands[0]["strong"] is True


def test_receipt_draft_keeps_weak_match_included(db):
    add_tx(db, 500, date(2026, 10, 1), "Shell")
    doc = Document(filename="f", stored_path="/x", mime="image/jpeg")
    db.add(doc)
    db.commit()
    draft = services.build_draft(db, doc, {"doc_type": "receipt", "merchant": "Walmart", "date": "2026-10-01",
                                           "total": 500, "currency": "TRY", "items": []})
    r = draft["rows"][0]
    assert r["include"] is True and r["match"] is None and len(r["match_candidates"]) == 1


# ---- P1: missing FX rate -------------------------------------------------------------


def test_missing_fx_is_reported_not_zeroed(db, client):
    add_tx(db, 100, date.today(), "Walmart")
    services.create_transaction(db, kind="expense", amount_cents=10000, currency="USD", on=date.today(),
                                category_id=1, user_id=None, payment_method="cash", card_id=None, merchant="Amazon")
    db.commit()
    s = client.get(f"/api/reports/summary?month={date.today():%Y-%m}").json()
    assert s["expense"] == 100 and s["fx_missing"] == [{"currency": "USD", "amount": 100.0}]
    db.add(FxRate(date=date.today(), currency="USD", rate=40.0))
    db.commit()
    s = client.get(f"/api/reports/summary?month={date.today():%Y-%m}").json()
    assert s["expense"] == 4100 and s["fx_missing"] == []


# ---- P1: older statement ----------------------------------------------------------------


def test_older_statement_does_not_override_debt(db):
    card = CreditCard(name="Bonus", last4="1019", statement_day=26, due_day=6)
    db.add(card)
    db.commit()
    services.apply_statement(db, card, {"period_end": "2026-09-26", "due_date": "2026-10-06", "totals": {"TRY": 10000}}, None)
    db.commit()
    assert card.debts == {"TRY": 1000000}
    services.apply_statement(db, card, {"period_end": "2026-08-26", "due_date": "2026-09-06", "totals": {"TRY": 5000}}, None)
    db.commit()
    assert card.debts == {"TRY": 1000000}  # older statement went into history, debt unchanged
    services.apply_statement(db, card, {"period_end": "2026-09-26", "due_date": "2026-10-06", "totals": {"TRY": 10500}}, None)
    db.commit()
    assert db.query(CardStatement).count() == 2 and card.debts == {"TRY": 1050000}  # same period was updated


# ---- P1: double submit --------------------------------------------------------------------


def test_client_ref_makes_create_idempotent(db, client):
    body = {"amount": 50, "date": "2026-10-01", "payment_method": "cash", "merchant": "Coffee", "client_ref": "abc123"}
    a = client.post("/api/transactions", json=body).json()
    b = client.post("/api/transactions", json=body).json()
    assert a["id"] == b["id"] and db.query(Transaction).count() == 1


def test_transaction_rejects_unknown_card_and_wrong_category(db, client):
    r = client.post("/api/transactions", json={"amount": 50, "date": "2026-10-01", "payment_method": "card", "card_id": 999})
    assert r.status_code == 400
    salary = finance.category_by_name(db, "Salary", "income")
    r = client.post("/api/transactions", json={"amount": 50, "date": "2026-10-01", "payment_method": "cash", "category_id": salary})
    assert r.status_code == 400


# ---- P2: multi-file upload -------------------------------------------------------------------


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_mixed_upload_leaves_nothing_behind(db, client):
    before = set(UPLOAD_DIR.glob("*")) if UPLOAD_DIR.exists() else set()
    files = [("files", ("a.png", png_bytes(), "image/png")), ("files", ("b.txt", b"hello", "text/plain"))]
    r = client.post("/api/documents", files=files)
    assert r.status_code == 400 and "b.txt" in r.json()["detail"]
    assert db.query(Document).count() == 0
    assert set(UPLOAD_DIR.glob("*")) == before


def test_fake_image_is_rejected(db, client):
    r = client.post("/api/documents", files=[("files", ("a.jpg", b"not an image", "image/jpeg"))])
    assert r.status_code == 400 and db.query(Document).count() == 0


def test_valid_upload_is_queued(db, client, monkeypatch):
    from app.routers import documents

    queued = []
    monkeypatch.setattr(documents, "enqueue", queued.append)
    r = client.post("/api/documents", files=[("files", ("a.png", png_bytes(), "image/png"))] * 2)
    assert r.status_code == 200 and len(queued) == 2 and db.query(Document).count() == 2
