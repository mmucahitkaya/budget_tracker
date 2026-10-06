"""Period plan: where income goes in the coming months, when debts end, how much accumulates.

Each month (calendar month, by spending date):

    income − fixed expenses − debt payments − variable spending − buffer − savings = free

- Fixed expenses: recurring expense items (excluding the "Loans & Debt" category).
- Debt payments: recurring items in the "Loans & Debt" category (car loan, gold savings circle…), card installments and,
  for this month: unpaid statements + past card spending whose statement hasn't arrived yet.
- Variable spending: manually entered monthly estimate > that month's category budgets > average of recent spending.
  For this month, actual spending is used if it exceeds the estimate. Hobbies are not a separate budget: card spending
  in the "Hobbies" category is part of variable spending (the category is chosen when confirming the statement).
- Savings: goals with a monthly plan use that amount; dated goals without a plan are distributed up to the target
  date, proportionally to months with more free money. When the target date arrives the accumulated amount is used
  (e.g. a car loan installment in November 2027).
- Cumulative balance: starting balance + sum of monthly free amounts.
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import finance, prefs, services, spendable
from .fx import Converter
from .models import (
    AppSetting, Budget, CardStatement, Category, CreditCard, InstallmentPlan, RecurringPayment, SavingsGoal, Transaction,
)

START_BALANCE_KEY = "plan_start_balance_cents"
VARIABLE_KEY = "plan_variable_cents"  # 0/empty = automatic
DEBT_CATEGORY = "Loans & Debt"
HOBBY_CATEGORY = "Hobbies"
AVERAGE_DAYS = 90


def _setting(db: Session, key: str) -> int:
    s = db.get(AppSetting, key)
    try:
        return int(s.value) if s and s.value else 0
    except ValueError:
        return 0


def settings_out(db: Session) -> dict:
    return {
        "start_balance": _setting(db, START_BALANCE_KEY) / 100,
        "variable": _setting(db, VARIABLE_KEY) / 100,
        "buffer": spendable.buffer_cents(db) / 100,
    }


def save_settings(db: Session, start_balance: float | None, variable: float | None, buffer: float | None) -> None:
    for key, v in ((START_BALANCE_KEY, start_balance), (VARIABLE_KEY, variable), (spendable.BUFFER_KEY, buffer)):
        if v is not None:
            db.merge(AppSetting(key=key, value=str(finance.to_cents(v))))
    db.commit()


def average_variable(db: Session, today: date, conv: Converter) -> tuple[int, str]:
    """Monthly variable spending from recent spending (excluding recurring items and installment purchases; hobbies included)."""
    since = today - timedelta(days=AVERAGE_DAYS)
    rows = db.scalars(select(Transaction).where(
        Transaction.kind == "expense", Transaction.date >= since, Transaction.date <= today,
        Transaction.recurring_id.is_(None), Transaction.installment_plan_id.is_(None),
        Transaction.payment_method != "voucher",
    )).all()
    if not rows:
        return 0, "no spending data yet"
    first = min(t.date for t in rows)
    days = max(30, (today - first).days + 1)
    total = sum(conv.to_base_cents(t.amount_cents, t.currency, t.date) for t in rows)
    return round(total / days * 30.4), f"average of the last {days} days"


def _month_budgets(db: Session, month: str) -> dict[int, int]:
    """That month's category budgets; otherwise the closest previous month's (copied every month)."""
    latest = db.scalar(select(Budget.month).where(Budget.month <= month).order_by(Budget.month.desc()).limit(1))
    if not latest:
        return {}
    return {b.category_id: b.limit_cents for b in db.scalars(select(Budget).where(Budget.month == latest))}


def projection(db: Session, months: int = 14, today: date | None = None) -> dict:
    today = today or date.today()
    conv = Converter(db)
    cats = {c.id: c for c in db.scalars(select(Category))}
    debt_cat = next((c.id for c in cats.values() if c.name == DEBT_CATEGORY), None)

    m0 = finance.month_start(today)
    month_list = [finance.add_months(m0, i) for i in range(months)]
    keys = [m.strftime("%Y-%m") for m in month_list]
    horizon_end = finance.month_range(keys[-1])[1]
    rows = {k: {"month": k, "income": 0, "fixed": 0, "debt": 0, "installments": 0, "card_backlog": 0,
                "variable": 0, "buffer": 0, "savings": 0, "release": 0,
                "items": defaultdict(int)} for k in keys}

    # Recurring income and expenses (future dates)
    recs = list(db.scalars(select(RecurringPayment).where(RecurringPayment.active.is_(True))))
    debt_items: list[dict] = []
    for r in recs:
        if r.payment_method == "voucher":
            continue
        is_debt = r.kind == "expense" and r.category_id == debt_cat
        future = []
        for d in finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, horizon_end):
            cents = conv.to_base_cents(finance.recurring_amount(r, d), r.currency, today)
            if not cents:
                continue
            key = d.strftime("%Y-%m")
            if key in rows:
                row = rows[key]
                if r.kind == "income":
                    row["income"] += cents
                else:
                    row["debt" if is_debt else "fixed"] += cents
                    row["items"][r.name] += cents
                future.append((d, cents))
        if is_debt and r.end_date:
            # If it ends beyond the horizon, estimate the remaining amount (all remaining payments)
            all_left = [(d, conv.to_base_cents(finance.recurring_amount(r, d), r.currency, today))
                        for d in finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, r.end_date)]
            all_left = [x for x in all_left if x[1]]
            if all_left:
                debt_items.append({"name": r.name, "kind": "recurring", "remaining": sum(c for _, c in all_left),
                                   "end": all_left[-1][0].strftime("%Y-%m"), "schedule": all_left})

    # Actuals this month: transactions from income and recurring items
    first_key = keys[0]
    for t in db.scalars(select(Transaction).where(Transaction.date >= m0, Transaction.date <= today)):
        if t.payment_method == "voucher":
            continue
        cents = conv.to_base_cents(t.amount_cents, t.currency, t.date)
        if t.kind == "income":
            rows[first_key]["income"] += cents
        elif t.recurring_id:
            is_debt = t.category_id == debt_cat
            rows[first_key]["debt" if is_debt else "fixed"] += cents
            rows[first_key]["items"][t.merchant or "Recurring"] += cents

    # Card installments (by statement month)
    plans = list(db.scalars(select(InstallmentPlan)))
    inst_left = 0
    inst_end = None
    for p in plans:
        for i in range(p.count):
            m = finance.add_months(p.first_month, i)
            if m < m0:
                continue
            cents = conv.to_base_cents(p.monthly_cents, p.currency, today)
            key = m.strftime("%Y-%m")
            inst_left += cents
            inst_end = max(inst_end or key, key)
            if key in rows:
                rows[key]["installments"] += cents
                rows[key]["debt"] += cents
    if inst_left:
        debt_items.append({"name": "Card installments", "kind": "installments", "remaining": inst_left, "end": inst_end})

    # This month: unpaid statements + past card spending whose statement hasn't arrived yet
    backlog = 0
    for card in db.scalars(select(CreditCard).where(CreditCard.archived.is_(False))):
        for st in db.scalars(select(CardStatement).where(CardStatement.card_id == card.id, CardStatement.paid.is_(False))):
            rem = services.statement_remaining(db, st)
            backlog += sum(conv.to_base_cents(v, c, today) for c, v in rem.items())
        last_end = db.scalar(select(CardStatement.period_end).where(CardStatement.card_id == card.id)
                             .order_by(CardStatement.period_end.desc()).limit(1))
        q = select(Transaction).where(Transaction.card_id == card.id, Transaction.date < m0,
                                      Transaction.installment_plan_id.is_(None))
        if last_end:
            q = q.where(Transaction.date > last_end)
        for t in db.scalars(q):
            cents = conv.to_base_cents(t.amount_cents, t.currency, t.date)
            backlog += cents if t.kind == "expense" else -cents
    backlog = max(0, backlog)
    rows[first_key]["card_backlog"] = backlog
    rows[first_key]["debt"] += backlog
    if backlog:
        debt_items.append({"name": "Card statements (unpaid)", "kind": "cards", "remaining": backlog, "end": first_key})

    # Variable spending (including hobbies) and buffer
    override = _setting(db, VARIABLE_KEY)
    avg, avg_note = average_variable(db, today, conv)
    buffer = spendable.buffer_cents(db)
    variable_source = "entered manually" if override else avg_note
    for k in keys:
        row = rows[k]
        budgets = _month_budgets(db, k)
        if override:
            var = override
        elif budgets:
            ms, me = finance.month_range(k)
            planned_fixed = defaultdict(int)
            for r in recs:
                if r.kind == "expense" and r.category_id in budgets:
                    for d in finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, me):
                        if d >= ms:
                            planned_fixed[r.category_id] += finance.recurring_amount(r, d)
            var = sum(max(0, lim - planned_fixed[c]) for c, lim in budgets.items())
            if k == keys[0]:
                variable_source = "category budgets"
        else:
            var = avg
        row["variable"] = var
        row["buffer"] = buffer
    # If actual variable spending this month exceeds the estimate, use the actual
    actual_now = sum(
        conv.to_base_cents(t.amount_cents, t.currency, t.date)
        for t in db.scalars(select(Transaction).where(
            Transaction.kind == "expense", Transaction.date >= m0, Transaction.date <= today,
            Transaction.recurring_id.is_(None), Transaction.installment_plan_id.is_(None),
            Transaction.payment_method != "voucher"))
    )
    hobby_id = next((c.id for c in cats.values() if c.name == HOBBY_CATEGORY), None)
    hobby_spent = defaultdict(int)  # info: spending in the "Hobbies" category (per month)
    if hobby_id:
        for t in db.scalars(select(Transaction).where(Transaction.kind == "expense", Transaction.category_id == hobby_id,
                                                      Transaction.date >= finance.add_months(m0, -3))):
            hobby_spent[t.date.strftime("%Y-%m")] += conv.to_base_cents(t.amount_cents, t.currency, t.date)
    rows[first_key]["variable_actual"] = actual_now
    rows[first_key]["variable"] = max(rows[first_key]["variable"], actual_now)

    # Savings goals
    from .savings import goal_out

    goals_out = []
    for g in db.scalars(select(SavingsGoal).where(SavingsGoal.archived.is_(False))):
        status = goal_out(db, g, today)
        remaining = finance.to_cents(status["remaining"])
        if g.currency != prefs.base():
            remaining = conv.to_base_cents(remaining, g.currency, today)
        target_key = g.target_date.strftime("%Y-%m") if g.target_date else None
        saved_before = finance.to_cents(status.get("funded") or 0) if "funded" in status else 0
        plan: dict[str, int] = {}
        if g.monthly_cents:
            monthly = conv.to_base_cents(g.monthly_cents, g.currency, today)
            left = remaining
            for k in keys:
                if target_key and k > target_key or left <= 0:
                    break
                plan[k] = min(monthly, left)
                left -= plan[k]
        elif target_key and remaining > 0:
            # Distribute across months before the target month, proportional to their free amount
            window = [k for k in keys if k < target_key] or [keys[0]]
            free = {}
            for k in window:
                r = rows[k]
                free[k] = max(0, r["income"] - r["fixed"] - r["debt"] - r["variable"] - r["buffer"] - r["savings"])
            total_free = sum(free.values())
            if total_free > 0:
                need = min(remaining, total_free)
                acc = 0
                for k in window:
                    share = round(need * free[k] / total_free)
                    plan[k] = share
                    acc += share
                if plan:
                    last = max(plan, key=lambda x: plan[x])
                    plan[last] += need - acc
        for k, v in plan.items():
            rows[k]["savings"] += v
            rows[k]["items"][f"Savings: {g.name}"] += v
        planned_total = sum(plan.values())
        if target_key in rows and g.target_date:
            # On the target date the accumulated amount is used (e.g. a large installment)
            rows[target_key]["release"] += planned_total + saved_before
        goals_out.append({
            "id": g.id, "name": g.name, "target": status.get("target", g.target_cents / 100), "remaining": remaining / 100,
            "target_month": target_key, "planned": planned_total / 100, "monthly": g.monthly_cents / 100,
            "auto": not g.monthly_cents, "shortfall": max(0, remaining - planned_total) / 100,
            "plan": [{"month": k, "amount": v / 100} for k, v in sorted(plan.items()) if v],
        })

    # Net, cumulative balance, remaining debt
    balance = _setting(db, START_BALANCE_KEY)
    saved_total = 0
    debt_now = sum(d["remaining"] for d in debt_items)
    debt_left = debt_now
    out_rows = []
    for k in keys:
        r = rows[k]
        out_total = r["fixed"] + r["debt"] + r["variable"] + r["buffer"] + r["savings"]
        net = r["income"] + r["release"] - out_total
        balance += net
        saved_total += r["savings"] - r["release"]
        debt_left -= r["debt"]
        out_rows.append({
            **{x: r[x] / 100 for x in ("income", "fixed", "debt", "installments", "card_backlog", "variable",
                                      "buffer", "savings", "release")},
            "month": k,
            "variable_actual": r.get("variable_actual", 0) / 100 if k == first_key else None,
            "free": max(0, net) / 100,
            "net": net / 100,
            "balance": balance / 100,
            "saved": max(0, saved_total) / 100,
            "debt_left": max(0, debt_left) / 100,
            "top_items": [{"name": n, "amount": v / 100} for n, v in sorted(r["items"].items(), key=lambda x: -x[1])[:8]],
        })
    tight = [r["month"] for r in out_rows if r["net"] < 0 or r["balance"] < 0]
    for d in debt_items:
        d.pop("schedule", None)
        d["remaining"] = d["remaining"] / 100
    return {
        "months": out_rows,
        "debts": sorted(debt_items, key=lambda d: -d["remaining"]),
        "debt_total": debt_now / 100,
        "debt_free": max((d["end"] for d in debt_items), default=None),
        "goals": goals_out,
        "settings": {**settings_out(db), "variable_auto": avg / 100, "variable_source": variable_source},
        "tight_months": tight,
        # Spending in the "Hobbies" category for the last 3 months and this month (info, not a separate budget)
        "hobby_spent": [{"month": k, "amount": v / 100} for k, v in sorted(hobby_spent.items())],
        "fx_missing": conv.missing_list(),
    }
