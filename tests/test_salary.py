from datetime import date

from fastapi.testclient import TestClient

from app import scheduler, telegram
from app.main import app
from app.models import Category, User


def test_salary_last_business_day_and_raise_reminder(db, monkeypatch):
    client = TestClient(app)
    cat = db.query(Category).filter_by(name="Salary").one()
    r = client.post("/api/recurring", json={
        "name": "Salary", "kind": "income", "amount": 108000, "next_date": "2026-10-05", "category_id": cat.id,
        "last_business_day": True, "raise_months": [7, 1, 1],
    }).json()
    assert r["next_date"] == "2026-10-30" and r["last_business_day"] and r["raise_months"] == [1, 7]
    assert db.query(Category).filter_by(name="Bonus").count() == 1

    user = db.query(User).one()
    user.telegram_chat_id = 42
    db.commit()
    sent = []
    monkeypatch.setattr(telegram, "enabled", lambda: True)
    monkeypatch.setattr(telegram, "send", lambda chat, text, buttons=None: sent.append((chat, text)))
    scheduler.job_raise_reminders(date(2026, 3, 1))
    assert sent == []
    scheduler.job_raise_reminders(date(2027, 1, 1))
    scheduler.job_raise_reminders(date(2027, 1, 1))
    assert len(sent) == 1 and sent[0][0] == 42 and "108,000" in sent[0][1]


def test_amount_plan_varies_by_month(db):
    from app import finance, services
    from app.models import RecurringPayment, Transaction
    r = RecurringPayment(name="Car loan", kind="expense", amount_cents=8500000, next_date=date(2026, 10, 20), day=20,
                         end_date=date(2026, 12, 20), amount_plan={"2026-12": 9000000})
    db.add(r)
    db.flush()
    services.materialize_recurring(db, r, date(2026, 12, 31))
    assert [t.amount_cents for t in db.query(Transaction).order_by(Transaction.date)] == [8500000, 8500000, 9000000]
    assert finance.recurring_amount(r, date(2027, 1, 20)) == 8500000


def test_yearly_month_override_and_skip(db):
    from app import services
    from app.models import RecurringPayment, Transaction
    from app.routers.misc import rec_out
    end_of_month = RecurringPayment(name="Salary", kind="income", amount_cents=10800000, next_date=date(2026, 8, 31), day=0,
                                    amount_plan={"09": 12000000})
    mid = RecurringPayment(name="Salary 15", kind="income", amount_cents=1200000, next_date=date(2026, 10, 15), day=15,
                           amount_plan={"10": 0})
    db.add_all([end_of_month, mid])
    db.flush()
    assert rec_out(mid)["next_date"] == "2026-11-15" and rec_out(mid)["next_amount"] == 12000
    services.materialize_recurring(db, end_of_month, date(2026, 10, 31))
    services.materialize_recurring(db, mid, date(2027, 10, 31))
    rows = [(t.date.isoformat(), t.amount_cents) for t in db.query(Transaction).order_by(Transaction.date)]
    assert rows[:3] == [("2026-08-31", 10800000), ("2026-09-30", 12000000), ("2026-10-30", 10800000)]
    mid_dates = [d for d, c in rows if c == 1200000]
    assert "2026-10-15" not in mid_dates and "2027-10-15" not in mid_dates and len(mid_dates) == 11


def test_statement_reminder_to_owner(db, monkeypatch):
    from app.models import CardStatement, CreditCard
    owner = User(ha_user_id="s", name="Bob", username="bob", telegram_chat_id=7)
    db.add(owner)
    db.flush()
    card = CreditCard(name="Maximum", last4="1140", statement_day=15, due_day=25, owner_id=owner.id)
    db.add(card)
    db.commit()
    sent = []
    monkeypatch.setattr(telegram, "enabled", lambda: True)
    monkeypatch.setattr(telegram, "send", lambda chat, text, buttons=None: sent.append((chat, text)))
    scheduler.job_statement_reminders(date(2026, 10, 15))  # closing day: not yet
    assert sent == []
    scheduler.job_statement_reminders(date(2026, 10, 16))
    scheduler.job_statement_reminders(date(2026, 10, 16))
    assert len(sent) == 1 and sent[0][0] == 7 and "1140" in sent[0][1]
    db.add(CardStatement(card_id=card.id, period_end=date(2026, 10, 15), due_date=date(2026, 10, 25)))
    db.commit()
    scheduler.job_statement_reminders(date(2026, 10, 19))  # uploaded: no second reminder
    assert len(sent) == 1


def test_supplementary_card_holder(db):
    from app import services
    from app.models import CreditCard
    alice = User(ha_user_id="m", name="alice", username="alice")
    bob = User(ha_user_id="s", name="Bob", username="bob")
    db.add_all([alice, bob])
    db.flush()
    main = CreditCard(name="Bonus", last4="1019", owner_id=alice.id)
    extra = CreditCard(name="Bonus extra", last4="1014", owner_id=alice.id, holder_id=bob.id)
    db.add_all([main, extra])
    db.flush()
    assert services.card_user(db, main.id) == alice.id
    assert services.card_user(db, extra.id) == bob.id
    assert services.card_user(db, None) is None


def test_gold_recurring_uses_price(db):
    from app import finance, prices
    from app.models import RecurringPayment
    from app.routers.misc import rec_out
    prices.store(db, "gold", "CEYREKALTIN", 10000.0, "test")
    r = RecurringPayment(name="Gold savings circle", kind="expense", amount_cents=1, next_date=date(2026, 10, 15), day=15,
                         end_date=date(2027, 2, 15), asset_code="gold:CEYREKALTIN", asset_qty=2)
    db.add(r)
    db.flush()
    assert finance.recurring_amount(r, date(2026, 10, 15)) == 2000000
    o = rec_out(r)
    assert o["remaining_count"] == 5 and o["remaining_total"] == 100000 and o["asset_label"].startswith("2 Quarter")
