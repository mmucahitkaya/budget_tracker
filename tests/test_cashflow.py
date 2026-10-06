from datetime import date

from app import analytics, services
from app.models import CreditCard, RecurringPayment

TODAY = date(2026, 10, 4)


def setup(db):
    card = CreditCard(name="Bonus", last4="1019", statement_day=26, due_day=6)
    db.add(card)
    db.commit()
    # Unpaid statement (due Oct 6): 5,000, of which 1,000 is paid
    st = services.apply_statement(db, card, {"period_end": "2026-09-26", "due_date": "2026-10-06", "currency": "TRY", "totals": {"TRY": 5000}}, None)
    services.add_card_payment(db, card, 100000, "TRY", date(2026, 10, 2), st.id, None)
    # This period's card spending: 300 one-time + 1,200 (3 installments -> 400 this month)
    for amount, inst, merchant in ((300, None, "A101"), (1200, 3, "Teknosa")):
        services.create_transaction(db, kind="expense", amount_cents=amount * 100, currency="TRY", on=date(2026, 10, 1),
                                    category_id=1, user_id=None, payment_method="card", card_id=card.id,
                                    merchant=merchant, installment_count=inst)
    # Cash expense (not in the flow, already spent)
    services.create_transaction(db, kind="expense", amount_cents=99900, currency="TRY", on=date(2026, 10, 2),
                                category_id=1, user_id=None, payment_method="cash", card_id=None, merchant="Farmers market")
    db.add_all([
        RecurringPayment(name="Rent", amount_cents=2000000, next_date=date(2026, 10, 15), day=15, payment_method="bank"),
        RecurringPayment(name="Salary", kind="income", amount_cents=5000000, next_date=date(2026, 10, 15), day=15),
        RecurringPayment(name="Netflix", amount_cents=23000, next_date=date(2026, 10, 8), day=8,
                         payment_method="card", card_id=card.id),
    ])
    db.commit()
    return card


def titles(cf):
    return [(e["date"], e["title"], e["amount"], e["direction"]) for e in cf["events"]]


def test_cashflow_30_days(db):
    setup(db)
    cf = analytics.cashflow(db, 30, TODAY)
    assert titles(cf) == [
        ("2026-10-06", "Bonus statement", 4000.0, "out"),  # remaining amount
        ("2026-10-15", "Salary", 50000.0, "in"),
        ("2026-10-15", "Rent", 20000.0, "out"),
    ]
    # Card spending and Netflix were not counted separately as expenses; the cash expense is in the past
    assert (cf["in"], cf["out"], cf["net"]) == (50000.0, 24000.0, 26000.0)
    assert cf["series"][-1]["net"] == 26000.0


def test_cashflow_card_estimates_without_double_counting(db):
    setup(db)
    cf = analytics.cashflow(db, 90, TODAY)
    est = [(e["date"], e["amount"]) for e in cf["events"] if e["kind"] == "card"]
    # Nov 6: this period 300 + installment 400 + Netflix 230 ; Dec 7 (the 6th is a Sunday): installment 400 + Netflix 230
    assert est == [("2026-11-06", 930.0), ("2026-12-07", 630.0)]
    assert sum(1 for e in cf["events"] if e["title"] == "Rent") == 3


def test_card_reminder_lists_every_statement_currency(db, monkeypatch):
    from app import notify, scheduler

    card = CreditCard(name="Visa", last4="4242", statement_day=26, due_day=6)
    db.add(card)
    db.commit()
    services.apply_statement(db, card, {"period_end": "2026-09-26", "due_date": "2026-10-06", "currency": "TRY",
                                        "totals": {"TRY": 5000, "USD": 20}, "min_payment": 1000}, None)
    db.commit()
    sent = []
    monkeypatch.setattr(notify, "send_once", lambda db, key, title, msg: sent.append(msg))
    scheduler.job_card_reminders(date(2026, 10, 5))
    assert sent and "5,000.00 TRY + 20.00 USD" in sent[0] and "minimum 1,000.00 TRY" in sent[0]
