from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app import savings
from app.models import AccountMovement, CreditCard, FxRate, Transaction


def client(db):
    return TestClient(app)


def account(c, name="Savings", kind="savings", amount=1000, currency="TRY", on=None):
    r = c.post(
        "/api/savings/accounts",
        json=dict(
            name=name,
            kind=kind,
            currency=currency,
            opening_balance=amount,
            opening_date=str(on or date.today()),
        ),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def goal(c, currency="TRY", **kwargs):
    r = c.post(
        "/api/savings/goals",
        json=dict(name="Home", currency=currency, target=5000, **kwargs),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def move(c, source, target, amount, **kwargs):
    body = dict(
        kind="transfer",
        source_id=source,
        target_id=target,
        amount=amount,
        date=str(date.today()),
        client_ref=str(uuid4()),
    )
    body.update(kwargs)
    return c.post("/api/savings/movements", json=body)


def allocate(c, g, a, amount, ref=None):
    return c.post(
        f"/api/savings/goals/{g}/allocations",
        json=dict(account_id=a, amount=amount, client_ref=ref or str(uuid4())),
    )


def test_transfer_is_atomic_idempotent_and_not_an_expense(db):
    c = client(db)
    a = account(c, kind="bank")
    b = account(c, amount=0)
    ref = str(uuid4())
    first = move(c, a, b, 400, client_ref=ref)
    again = move(c, a, b, 400, client_ref=ref)
    assert (
        first.status_code == again.status_code == 200 and first.json() == again.json()
    )
    s = c.get("/api/savings").json()
    assert [a["balance"] for a in s["accounts"]] == [600, 400]
    assert s["month_contribution"] == 400 and s["savings"] == 400
    assert not db.scalars(select(Transaction)).all()
    assert len(db.scalars(select(AccountMovement)).all()) == 1
    assert move(c, a, b, 401, client_ref=ref).status_code == 409


def test_two_goals_cannot_reserve_same_money_and_withdrawal_is_blocked(db):
    c = client(db)
    a = account(c)
    g1 = goal(c)
    g2 = goal(c)
    ref = str(uuid4())
    assert allocate(c, g1, a, 700, ref).status_code == 200
    assert allocate(c, g1, a, 700, ref).status_code == 200
    assert allocate(c, g2, a, 700, ref).status_code == 409
    assert allocate(c, g2, a, 400).status_code == 409
    assert move(c, a, None, 400, kind="withdrawal").status_code == 409
    assert allocate(c, g1, a, -200).status_code == 200
    assert move(c, a, None, 400, kind="withdrawal").status_code == 200
    s = c.get("/api/savings").json()
    assert s["accounts"][0]["balance"] == 600 and s["accounts"][0]["allocated"] == 500
    assert allocate(c, g1, a, -501).status_code == 409


def test_backdated_movement_and_deletion_preserve_historical_solvency(db):
    c = client(db)
    today = date.today()
    a = account(c, amount=0, on=today - timedelta(days=10))
    m = move(c, None, a, 500, kind="deposit", date=str(today - timedelta(days=2)))
    assert m.status_code == 200
    assert (
        move(
            c, a, None, 100, kind="withdrawal", date=str(today - timedelta(days=3))
        ).status_code
        == 409
    )
    g = goal(c)
    assert allocate(c, g, a, 400).status_code == 200
    assert c.delete(f"/api/savings/movements/{m.json()['id']}").status_code == 409
    assert c.get("/api/savings").json()["accounts"][0]["balance"] == 500


def test_cross_currency_actual_receipt_and_goal_currency_validation(db):
    c = client(db)
    a = account(c, kind="bank", amount=10000)
    b = account(c, amount=0, currency="USD")
    assert move(c, a, b, 4000).status_code == 400
    assert move(c, a, b, 4000, received_amount=100).status_code == 200
    assert allocate(c, goal(c), b, 50).status_code == 400
    assert allocate(c, goal(c, currency="USD"), b, 50).status_code == 200
    s = c.get("/api/savings").json()
    assert s["fx_missing"] and s["by_currency"]["USD"]["balance"] == 100


def test_fx_growth_and_opening_are_not_contributions(db):
    c = client(db)
    now = date.today()
    start = now.replace(day=1)
    before = start - timedelta(days=1)
    db.add_all(
        [
            FxRate(date=before, currency="USD", rate=30),
            FxRate(date=start, currency="USD", rate=40),
        ]
    )
    db.commit()
    account(c, amount=100, currency="USD", on=before)
    result = savings.overview(db, now)
    assert result["series"][-1]["contribution"] == 0
    assert result["series"][-1]["valuation_change"] == 1000
    assert result["savings"] == 4000


def test_zero_foreign_debt_does_not_require_a_rate(db):
    db.add(CreditCard(name="TRY card", debts={"TRY": 10000, "USD": 0}))
    db.commit()
    c = client(db)
    account(c)
    s = c.get("/api/savings").json()
    assert not s["fx_missing"]
    assert s["net_worth"] == 900


def test_account_and_goal_validation_and_auth(db):
    c = client(db)
    a = account(c)
    g = goal(c)
    assert allocate(c, g, a, 0).status_code == 400
    assert move(c, a, a, 100).status_code == 422
    assert move(c, None, a, 0.001, kind="deposit").status_code == 422
    assert (
        move(
            c, None, a, 100, kind="deposit", date=str(date.today() + timedelta(days=1))
        ).status_code
        == 400
    )

    assert (
        c.post("/api/savings/goals", json={"name": "  ", "target": 10}).status_code
        == 422
    )
    assert (
        c.post(
            "/api/savings/accounts",
            json=dict(
                name="x", kind="bank", opening_date=str(date.today()), owner_id=999
            ),
        ).status_code
        == 400
    )


def test_new_endpoints_enforce_ingress_auth(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dev_user", "")
    c = client(db)
    assert c.get("/api/savings").status_code == 403
    assert (
        c.get("/api/reports/workspace?start=2026-10-01&end=2026-10-05").status_code
        == 403
    )
    assert (
        c.post("/api/savings/goals", json={"name": "Denied", "target": 100}).status_code
        == 403
    )


def test_internal_savings_transfer_is_not_a_contribution(db):
    c = client(db)
    a = account(c)
    b = account(c, amount=0)
    assert move(c, a, b, 400).status_code == 200
    s = c.get("/api/savings").json()
    assert s["savings"] == 1000 and s["month_contribution"] == 0
    assert s["series"][-1]["adjustment"] == 1000


def test_contribution_reminder_does_not_create_movement(db, monkeypatch):
    from app import scheduler

    calls = []
    monkeypatch.setattr(
        scheduler.notify, "send_once", lambda *args: calls.append(args[1])
    )
    c = client(db)
    g = goal(c, monthly=100, reminder_day=date.today().day)
    scheduler.job_savings_reminders()
    assert calls and str(g) in calls[0]
    assert not db.scalars(select(AccountMovement)).all()


def test_concurrent_reservations_are_serialized(db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    c = client(db)
    a = account(c)
    goals = [goal(c), goal(c)]
    ready = Barrier(2)

    # Don't run application lifespan: it starts scheduled external integrations.
    def reserve(g):
        concurrent_client = TestClient(app)
        ready.wait()
        return allocate(concurrent_client, g, a, 700).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(reserve, goals))
    assert sorted(statuses) == [200, 409]
    assert c.get("/api/savings").json()["accounts"][0]["allocated"] == 700
