from datetime import date

from app import planning
from app.models import Category, CreditCard, InstallmentPlan, RecurringPayment, SavingsGoal, Transaction


def _cat(db, name):
    return db.query(Category).filter_by(name=name).one().id


def test_projection_buckets_and_goal(db):
    today = date(2026, 10, 5)
    debt = _cat(db, "Loans & Debt")
    card = CreditCard(name="Bonus", statement_day=26, due_day=5)
    db.add(card)
    db.flush()
    db.add_all([
        RecurringPayment(name="Salary", kind="income", amount_cents=10000000, next_date=date(2026, 10, 15), day=15),
        RecurringPayment(name="Rent", kind="expense", amount_cents=2000000, next_date=date(2026, 11, 1), day=1),
        RecurringPayment(name="Car loan", kind="expense", amount_cents=3000000, next_date=date(2026, 10, 20), day=20,
                         end_date=date(2027, 3, 20), category_id=debt, amount_plan={"2027-03": 9000000}),
        InstallmentPlan(card_id=card.id, description="TV", monthly_cents=500000, count=3, first_month=date(2026, 10, 1)),
        Transaction(kind="expense", amount_cents=900000, date=date(2026, 9, 1), payment_method="cash"),
        SavingsGoal(name="March installment", target_cents=6000000, target_date=date(2027, 3, 20)),
    ])
    db.commit()
    planning.save_settings(db, 1000, None, None)
    p = planning.projection(db, 6, today)
    m = {r["month"]: r for r in p["months"]}
    assert m["2026-10"]["income"] == 100000 and m["2026-10"]["fixed"] == 0
    assert m["2026-11"]["fixed"] == 20000 and m["2026-11"]["debt"] == 30000 + 5000
    assert m["2027-03"]["debt"] == 90000 and m["2027-01"]["installments"] == 0
    assert m["2026-11"]["variable"] > 0 and "hobby" not in m["2026-11"]
    # Goal: 60k is distributed across the months before March and used in March
    goal = p["goals"][0]
    assert goal["planned"] == 60000 and all(x["month"] < "2027-03" for x in goal["plan"])
    assert m["2027-03"]["release"] == 60000
    assert p["debt_total"] == 30000 * 5 + 90000 + 15000  # car loan remainder + installments
    assert p["months"][0]["balance"] == 1000 + p["months"][0]["net"]
