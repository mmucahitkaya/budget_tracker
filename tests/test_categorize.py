from datetime import date

from fastapi.testclient import TestClient

from app import finance
from app.main import app
from app.models import Category, Transaction


def test_group_categorize_learn_and_undo(db):
    client = TestClient(app)
    other = db.query(Category).filter_by(name="Other Expense").one().id
    hobby = db.query(Category).filter_by(name="Hobbies").one().id
    market = db.query(Category).filter_by(name="Groceries").one().id
    db.add_all([
        Transaction(kind="expense", amount_cents=10000, date=date(2026, 9, 1), merchant="BOOK CAFE 12 SPRINGFIELD", category_id=other),
        Transaction(kind="expense", amount_cents=20000, date=date(2026, 9, 3), merchant="BOOK CAFE 7 SPRINGFIELD", category_id=None),
        Transaction(kind="expense", amount_cents=5000, date=date(2026, 9, 2), merchant="MIGROS", category_id=market),
    ])
    db.commit()
    r = client.get("/api/transactions/categorize/pending").json()
    assert r["count"] == 2 and len(r["groups"]) == 1 and r["groups"][0]["count"] == 2 and other not in r["favorites"]
    ids = r["groups"][0]["tx_ids"]
    assert client.post("/api/transactions/categorize", json={"tx_ids": ids, "category_id": hobby}).json()["pending"] == 0
    assert finance.rule_category(db, "BOOK CAFE 99 SPRINGFIELD") == hobby  # learned
    # Undo: goes back to pending
    client.post("/api/transactions/categorize", json={"tx_ids": ids, "category_id": other, "learn": False, "confirm": False})
    assert client.get("/api/transactions/categorize/pending").json()["count"] == 2
    # "Keep as Other": confirmed, removed from the list
    client.post("/api/transactions/categorize", json={"tx_ids": ids, "category_id": other, "learn": False})
    assert client.get("/api/transactions/categorize/pending").json()["count"] == 0


def test_marketplace_orders_are_separate_and_not_learned(db):
    client = TestClient(app)
    other = db.query(Category).filter_by(name="Other Expense").one().id
    elec = db.query(Category).filter_by(name="Electronics").one().id
    db.add_all([
        Transaction(kind="expense", amount_cents=10000, date=date(2026, 9, 1), merchant="HEPSIBURADA GARANTI PAY", category_id=other),
        Transaction(kind="expense", amount_cents=20000, date=date(2026, 9, 3), merchant="HEPSIBURADA GARANTI PAY", category_id=other),
        Transaction(kind="expense", amount_cents=5000, date=date(2026, 9, 2), merchant="BIM K429", category_id=other),
        Transaction(kind="expense", amount_cents=6000, date=date(2026, 9, 4), merchant="BIM K429", category_id=other),
    ])
    db.commit()
    r = client.get("/api/transactions/categorize/pending").json()
    hb = [g for g in r["groups"] if g["per_order"]]
    bim = [g for g in r["groups"] if not g["per_order"]]
    assert len(hb) == 2 and len(bim) == 1 and len(bim[0]["orders"]) == 2
    client.post("/api/transactions/categorize", json={"tx_ids": hb[0]["tx_ids"], "category_id": elec, "learn": True})
    assert finance.rule_category(db, "HEPSIBURADA GARANTI PAY") is None  # marketplaces aren't learned
    assert client.get("/api/transactions/categorize/pending").json()["count"] == 3


def test_order_date_from_installment(db):
    from app.models import CreditCard, InstallmentPlan
    client = TestClient(app)
    card = CreditCard(name="Bonus", statement_day=26, due_day=5)
    db.add(card)
    db.flush()
    plan = InstallmentPlan(card_id=card.id, description="HEPSIBURADA GARANTI PAY", monthly_cents=132800, count=3,
                           first_month=date(2026, 7, 1))
    db.add(plan)
    db.flush()
    db.add(Transaction(kind="expense", amount_cents=132800, date=date(2026, 9, 28), merchant="HEPSIBURADA GARANTI PAY",
                       card_id=card.id, payment_method="card", installment_plan_id=plan.id, installment_no=3))
    db.commit()
    o = client.get("/api/transactions/categorize/pending").json()["groups"][0]["orders"][0]
    assert o["date"] == "2026-07-28" and o["amount"] == 3984 and o["current_no"] == 3


def test_category_create_dedupe_and_delete_moves(db):
    from app.models import Budget, MerchantRule, RecurringPayment
    client = TestClient(app)
    c = client.post("/api/categories", json={"name": "Books", "kind": "expense", "icon": "📚"}).json()
    assert client.post("/api/categories", json={"name": " books ", "kind": "expense"}).json()["id"] == c["id"]
    other = db.query(Category).filter_by(name="Other Expense").one().id
    db.add_all([
        Transaction(kind="expense", amount_cents=1000, date=date(2026, 9, 1), merchant="D&R", category_id=c["id"], category_confirmed=True),
        RecurringPayment(name="Magazine", kind="expense", amount_cents=500, next_date=date(2026, 11, 1), day=1, category_id=c["id"]),
        Budget(category_id=c["id"], month="2026-10", limit_cents=10000),
        MerchantRule(pattern="d r", category_id=c["id"]),
    ])
    db.commit()
    assert client.get(f"/api/categories/{c['id']}/usage").json() == {"transactions": 1, "recurring": 1, "installments": 0, "budgets": 1}
    r = client.delete(f"/api/categories/{c['id']}").json()
    assert r["moved"] == 1 and r["target"]["id"] == other
    db.expire_all()
    t = db.query(Transaction).one()
    assert t.category_id == other and t.category_confirmed is False
    assert db.query(Budget).count() == 0 and db.query(MerchantRule).count() == 0
    assert client.delete(f"/api/categories/{other}").status_code == 400


def test_categorize_with_note_applies_to_series(db):
    from app import services
    from app.models import CreditCard
    client = TestClient(app)
    card = CreditCard(name="Bonus", statement_day=26, due_day=5)
    db.add(card)
    db.flush()
    services.create_transaction(db, kind="expense", amount_cents=300000, currency="TRY", on=date(2026, 9, 3), category_id=None,
                                user_id=None, payment_method="card", card_id=card.id, merchant="HEPSIBURADA", installment_count=3)
    db.commit()
    g = client.get("/api/transactions/categorize/pending").json()["groups"][0]
    elec = db.query(Category).filter_by(name="Electronics").one().id
    client.post("/api/transactions/categorize", json={"tx_ids": g["tx_ids"], "category_id": elec, "note": "Headphones"})
    db.expire_all()
    assert {(t.category_id, t.note) for t in db.query(Transaction)} == {(elec, "Headphones")}
