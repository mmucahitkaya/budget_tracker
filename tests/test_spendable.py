from datetime import date

from fastapi.testclient import TestClient

from app import spendable
from app.main import app
from app.models import CardStatement, CreditCard, RecurringPayment, SavingsGoal, Transaction


def test_spendable_cash_basis(db):
    today = date(2026, 10, 5)
    card = CreditCard(name="Bonus", statement_day=26, due_day=5)
    db.add(card)
    db.flush()
    # This month: salary received 100k, rent 20k (from bank, fixed), groceries 1k in cash
    db.add_all([
        Transaction(kind="income", amount_cents=10000000, date=date(2026, 10, 1), payment_method="bank"),
        Transaction(kind="expense", amount_cents=2000000, date=date(2026, 10, 1), payment_method="bank", recurring_id=None),
        Transaction(kind="expense", amount_cents=100000, date=date(2026, 10, 2), payment_method="cash"),
        # 5k by card today: not deducted this month, goes onto next month's statement (Oct 26 closing -> Nov 5)
        Transaction(kind="expense", amount_cents=500000, date=date(2026, 10, 4), payment_method="card", card_id=card.id),
        # Vouchers are not counted
        Transaction(kind="income", amount_cents=300000, date=date(2026, 10, 3), payment_method="voucher"),
    ])
    # Statement due this month: 30k (Sep 26 closing, Oct 5 due)
    db.add(CardStatement(card_id=card.id, period_end=date(2026, 9, 26), due_date=date(2026, 10, 5), currency="TRY", totals={"TRY": 3000000}))
    # Next month: 50k salary on the 15th
    db.add(RecurringPayment(name="Salary", kind="income", amount_cents=5000000, next_date=date(2026, 11, 15), day=15))
    db.commit()
    r = spendable.compute(db, today)
    m0, m1 = r["this_month"], r["next_month"]
    assert (m0["income"], m0["cards"], m0["spent"]) == (100000, 30000, 21000)
    assert m0["available"] == 100000 - 30000 - 21000
    assert m1["income"] == 50000 and m1["cards"] == 5000 and m1["available"] == 45000

    # Buffer and savings plan are deducted
    db.add(SavingsGoal(name="Home", target_cents=1, monthly_cents=1000000, target_date=date(2027, 12, 1)))
    db.commit()
    client = TestClient(app)
    client.put("/api/settings", json={"spend_buffer": 5000})
    assert client.get("/api/settings").json()["household_payday"] == 1
    r = spendable.compute(db, today)
    assert r["next_month"]["available"] == 45000 - 10000 - 5000


def test_unuploaded_statement_is_estimated(db):
    today = date(2026, 10, 17)
    card = CreditCard(name="Maximum", statement_day=15, due_day=25)
    db.add(card)
    db.flush()
    db.add(Transaction(kind="expense", amount_cents=700000, date=date(2026, 10, 1), payment_method="card", card_id=card.id))
    db.commit()
    r = spendable.compute(db, today)
    assert r["this_month"]["cards"] == 7000  # Oct 15 closing, no statement -> estimated for Oct 25


def test_spendable_generic_usd_base(db):
    """US household with a EUR expense: everything is converted to USD; summary text uses USD."""
    from app import prefs
    from app.models import FxRate

    prefs.save(db, base_currency="USD", currencies=["USD", "EUR"], region="", setup_done=True)
    today = date(2026, 10, 5)
    db.add(FxRate(date=date(2026, 10, 1), currency="EUR", rate=1.1))  # 1 EUR = 1.10 USD
    db.add_all([
        Transaction(kind="income", amount_cents=500000, currency="USD", date=date(2026, 10, 1), payment_method="bank"),
        Transaction(kind="expense", amount_cents=10000, currency="EUR", date=date(2026, 10, 2), payment_method="cash"),
        Transaction(kind="expense", amount_cents=5000, currency="USD", date=date(2026, 10, 3), payment_method="cash"),
    ])
    db.commit()
    r = spendable.compute(db, today)
    m0 = r["this_month"]
    assert not r["fx_missing"]
    assert (m0["income"], m0["spent"]) == (5000, 160)
    assert m0["available"] == 5000 - 160
    text = spendable.summary_text(db, today)
    assert "4,840 USD" in text and "TRY" not in text
