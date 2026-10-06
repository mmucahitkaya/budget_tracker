"""Spendable amount: what is left this month and the most you can spend next month (cash basis, calendar month).

Month = calendar month. Card spending counts in the month it is paid: statements due this month are deducted
from this month, and card spending made this month goes onto next month's estimated statement. Formula:

    income − fixed expenses (cash/bank) − card payments − cash/bank spending − savings plan − buffer

This month: what has happened so far + what is expected until month end (cash flow projection).
Next month: entirely expected amounts. Money left over from previous months (bank balance) is not counted.
Vouchers are not cash; they count as neither income nor expense.
"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import analytics, finance, prefs
from .fx import Converter
from .models import AppSetting, CardPayment, SavingsGoal, Transaction

BUFFER_KEY = "spend_buffer_cents"


def buffer_cents(db: Session) -> int:
    s = db.get(AppSetting, BUFFER_KEY)
    return int(s.value) if s and s.value.isdigit() else 0


def savings_plan_cents(db: Session, on: date, conv: Converter) -> int:
    """Total of the savings goals' monthly contribution plans (base currency)."""
    total = 0
    for g in db.scalars(select(SavingsGoal).where(SavingsGoal.archived.is_(False), SavingsGoal.monthly_cents > 0)):
        total += conv.to_base_cents(g.monthly_cents, g.currency, on)
    return total


def _empty(month: str) -> dict:
    return {"month": month, "income": 0, "fixed": 0, "cards": 0, "spent": 0, "items": []}


def compute(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    conv = Converter(db)
    m0_start, m0_end = finance.month_range(today.strftime("%Y-%m"))
    m1_start = m0_end + timedelta(days=1)
    m1_end = finance.month_range(m1_start.strftime("%Y-%m"))[1]
    m0, m1 = _empty(today.strftime("%Y-%m")), _empty(m1_start.strftime("%Y-%m"))

    # Actuals this month (including today)
    for t in db.scalars(select(Transaction).where(Transaction.date >= m0_start, Transaction.date <= today)):
        if t.payment_method == "voucher":
            continue
        cents = conv.to_base_cents(t.amount_cents, t.currency, t.date)
        if t.kind == "income":
            m0["income"] += cents
        elif t.payment_method != "card":
            m0["fixed" if t.recurring_id else "spent"] += cents
    for p in db.scalars(select(CardPayment).where(CardPayment.date >= m0_start, CardPayment.date <= today)):
        m0["cards"] += conv.to_base_cents(p.amount_cents, p.currency, p.date)

    # Expected: cash flow projection (from today to the end of next month)
    flow = analytics.cashflow(db, (m1_end - today).days, today)
    for e in flow["events"]:
        on = date.fromisoformat(e["date"])
        target = m0 if on <= m0_end else m1
        cents = finance.to_cents(e["amount"])
        if e["direction"] == "in":
            target["income"] += cents
        elif e["kind"] in ("statement", "card"):
            target["cards"] += cents
        else:
            target["fixed"] += cents
        target["items"].append(e)

    savings = savings_plan_cents(db, today, conv)
    buffer = buffer_cents(db)
    for m in (m0, m1):
        m["savings"], m["buffer"] = savings, buffer
        m["available"] = m["income"] - m["fixed"] - m["cards"] - m["spent"] - savings - buffer
    days_left = (m0_end - today).days + 1
    m0["per_day"] = round(max(0, m0["available"]) / days_left) if days_left else 0
    m0["days_left"] = days_left

    def out(m: dict) -> dict:
        money = ("income", "fixed", "cards", "spent", "savings", "buffer", "available", "per_day")
        return {k: (finance.from_cents(v) if k in money else v) for k, v in m.items()}

    return {"this_month": out(m0), "next_month": out(m1), "fx_missing": conv.missing_list()}


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _tl(v: float, currency: str | None = None) -> str:
    """Human-readable whole amount in the base currency (or `currency`), e.g. "1,234 USD"."""
    return f"{v:,.0f} {currency or prefs.base()}"


def summary_text(db: Session, today: date | None = None) -> str:
    """Short spendable summary for Telegram (HTML)."""
    r = compute(db, today)
    m0, m1 = r["this_month"], r["next_month"]
    name0 = MONTHS[int(m0["month"][5:]) - 1]
    name1 = MONTHS[int(m1["month"][5:]) - 1]
    lines = [
        f"💰 <b>{name0} left: {_tl(m0['available'])}</b>"
        + (f" (~{_tl(m0['per_day'])}/day, {m0['days_left']} days)" if m0["available"] > 0 else ""),
        f"Income {_tl(m0['income'])} − fixed {_tl(m0['fixed'])} − cards {_tl(m0['cards'])} − spent {_tl(m0['spent'])}"
        + (f" − savings {_tl(m0['savings'])}" if m0["savings"] else "")
        + (f" − buffer {_tl(m0['buffer'])}" if m0["buffer"] else ""),
        f"📅 <b>{name1} at most: {_tl(m1['available'])}</b>",
        f"Income {_tl(m1['income'])} − fixed {_tl(m1['fixed'])} − cards (estimated) {_tl(m1['cards'])}",
        "What you spend by card is deducted from next month's statement.",
    ]
    if r["fx_missing"]:
        lines.append("⚠️ Exchange rates are missing for some foreign currency amounts; totals may be incomplete.")
    return "\n".join(lines)
