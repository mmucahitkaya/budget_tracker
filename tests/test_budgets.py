from datetime import date

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app import finance
from app.main import app
from app.models import Budget


def test_budget_change_affects_only_its_month(db):
    client = TestClient(app)
    client.put("/api/budgets", json={"category_id": 1, "limit": 1000, "month": "2026-09"})
    client.put("/api/budgets", json={"category_id": 1, "limit": 2000, "month": "2026-10"})
    client.put("/api/budgets", json={"category_id": 1, "limit": 1500, "month": "2026-10"})
    assert client.get("/api/budgets?month=2026-09").json()[0]["limit"] == 1000
    assert client.get("/api/budgets?month=2026-10").json()[0]["limit"] == 1500
    assert client.get("/api/budgets?month=2026-11").json() == []


def test_copy_previous_month(db):
    client = TestClient(app)
    client.put("/api/budgets", json={"category_id": 1, "limit": 1000, "month": "2026-09"})
    client.put("/api/budgets", json={"category_id": 2, "limit": 500, "month": "2026-09"})
    client.put("/api/budgets", json={"category_id": 2, "limit": 700, "month": "2026-10"})
    assert client.post("/api/budgets/copy", json={"from_month": "2026-09", "to_month": "2026-10"}).json()["copied"] == 1
    got = {b["category_id"]: b["limit"] for b in client.get("/api/budgets?month=2026-10").json()}
    assert got == {1: 1000, 2: 700}  # the existing limit in the target month is kept


def test_scheduler_carries_budgets_on_first_day(db):
    from app import scheduler

    db.add(Budget(category_id=1, month="2026-09", limit_cents=100000))
    db.commit()
    scheduler.job_monthly_summary(date(2026, 10, 1))
    db.expire_all()
    assert db.query(Budget).filter_by(month="2026-10").one().limit_cents == 100000


def test_budget_alert_uses_month_limit(db, monkeypatch):
    from app import notify, services

    sent = []
    monkeypatch.setattr(notify, "send", lambda t, m: sent.append(t))
    db.add(Budget(category_id=1, month="2026-10", limit_cents=10000))  # 100 TRY, October only
    db.commit()
    tx = services.create_transaction(db, kind="expense", amount_cents=15000, currency="TRY", on=date(2026, 9, 5),
                                     category_id=1, user_id=None, payment_method="cash", card_id=None)
    db.commit()
    finance.check_budget(db, 1, tx.date)
    assert sent == []  # no budget in September


def test_old_budget_table_is_migrated(tmp_path, monkeypatch):
    """An old table without a month column is rebuilt; limits move to the current month."""
    from app import db as dbmod

    eng = sa.create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE categories (id INTEGER PRIMARY KEY)")
        c.exec_driver_sql("INSERT INTO categories VALUES (1)")
        c.exec_driver_sql("CREATE TABLE budgets (id INTEGER PRIMARY KEY, category_id INTEGER UNIQUE, limit_cents INTEGER)")
        c.exec_driver_sql("INSERT INTO budgets VALUES (7, 1, 50000)")
        dbmod._migrate_monthly_budgets(c)
        row = c.exec_driver_sql("SELECT id, category_id, month, limit_cents FROM budgets").one()
    assert tuple(row) == (7, 1, date.today().strftime("%Y-%m"), 50000)
