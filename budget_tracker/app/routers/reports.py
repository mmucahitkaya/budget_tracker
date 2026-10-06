from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import analytics, finance, prefs, services
from ..auth import current_user
from ..db import get_db
from ..fx import Converter
from ..models import CardStatement, CreditCard, RecurringPayment, Transaction, User

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _rows(db: Session, start: date, end: date):
    return db.execute(
        select(
            Transaction.kind,
            Transaction.amount_cents,
            Transaction.currency,
            Transaction.date,
            Transaction.category_id,
            Transaction.user_id,
        ).where(Transaction.date >= start, Transaction.date <= end)
    ).all()


def upcoming(db: Session, today: date, days: int = 14) -> list[dict]:
    horizon = today + timedelta(days=days)
    items = []
    for r in db.scalars(
        select(RecurringPayment).where(RecurringPayment.active.is_(True), RecurringPayment.next_date <= horizon)
    ):
        if not finance.recurring_amount(r, r.next_date):
            continue
        items.append(
            {
                "type": "recurring",
                "id": r.id,
                "title": r.name,
                "date": r.next_date.isoformat(),
                "amount": finance.from_cents(finance.recurring_amount(r, r.next_date)),
                "currency": r.currency,
                "kind": r.kind,
            }
        )
    for c in db.scalars(select(CreditCard).where(CreditCard.archived.is_(False))):
        stmt = db.scalar(
            select(CardStatement)
            .where(CardStatement.card_id == c.id, CardStatement.paid.is_(False), CardStatement.due_date >= today - timedelta(days=7))
            .order_by(CardStatement.due_date)
            .limit(1)
        )
        if stmt and stmt.due_date <= horizon:
            rem = services.statement_remaining(db, stmt)
            primary = getattr(stmt, "currency", None) or prefs.base()
            curs = sorted((cur for cur, v in rem.items() if v), key=lambda cur: (cur != primary, cur)) or [primary]
            for cur in curs:
                item = {
                    "type": "card",
                    "id": c.id,
                    "statement_id": stmt.id,
                    "title": f"{c.name} payment due",
                    "date": stmt.due_date.isoformat(),
                    "amount": finance.from_cents(rem.get(cur, 0)),
                    "currency": cur,
                    "kind": "expense",
                }
                if cur == primary:
                    item["min_payment"] = finance.from_cents(stmt.min_payment_cents)
                items.append(item)
        elif not stmt and any(v > 0 for v in (c.debts or {}).values()):
            due = finance.next_due_date(c, today)
            if due <= horizon:
                for cur, cents in sorted((c.debts or {}).items(), key=lambda x: (x[0] != prefs.base(), x[0])):
                    if cents > 0:
                        items.append(
                            {
                                "type": "card",
                                "id": c.id,
                                "title": f"{c.name} payment due",
                                "date": due.isoformat(),
                                "amount": finance.from_cents(cents),
                                "currency": cur,
                                "kind": "expense",
                            }
                        )
    items.sort(key=lambda x: x["date"])
    return items


@router.get("/summary")
def summary(month: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    today = date.today()
    month = month or today.strftime("%Y-%m")
    start, end = finance.month_range(month)
    conv = Converter(db)

    income = expense = 0
    by_currency: dict[str, dict[str, float]] = {}
    by_category: dict[int | None, int] = {}
    by_user: dict[int | None, int] = {}
    for kind, cents, cur, d, cat, uid in _rows(db, start, min(end, today)):
        t = conv.to_base_cents(cents, cur, d)
        bucket = by_currency.setdefault(cur, {"income": 0.0, "expense": 0.0})
        bucket[kind] = round(bucket[kind] + cents / 100, 2)
        if kind == "income":
            income += t
        else:
            expense += t
            by_category[cat] = by_category.get(cat, 0) + t
            by_user[uid] = by_user.get(uid, 0) + t

    prev_start, prev_end = finance.month_range(finance.add_months(start, -1).strftime("%Y-%m"))
    if start <= today <= end:
        prev_end = finance.clamp_day(prev_start.year, prev_start.month, today.day)
    prev_expense = sum(conv.to_base_cents(c, cur, d) for k, c, cur, d, _, _ in _rows(db, prev_start, prev_end) if k == "expense")

    cards = db.scalars(select(CreditCard).where(CreditCard.archived.is_(False))).all()
    debt_cents: dict[str, int] = {prefs.base(): 0}
    for c in cards:
        for cur, cents in (c.debts or {}).items():
            debt_cents[cur] = debt_cents.get(cur, 0) + int(cents or 0)
    card_debt = {cur: finance.from_cents(v) for cur, v in debt_cents.items() if v or cur == prefs.base()}

    return {
        "month": month,
        "income": finance.from_cents(income),
        "expense": finance.from_cents(expense),
        "net": finance.from_cents(income - expense),
        "prev_expense": finance.from_cents(prev_expense),
        "by_currency": by_currency,
        "by_category": sorted(
            [{"category_id": k, "total": finance.from_cents(v)} for k, v in by_category.items()],
            key=lambda x: -x["total"],
        ),
        "by_user": [{"user_id": k, "total": finance.from_cents(v)} for k, v in by_user.items()],
        "card_debt": card_debt,
        "upcoming": upcoming(db, today) if month == today.strftime("%Y-%m") else [],
        "planned": _planned_totals(db, month),
        "fx_missing": conv.missing_list(),
    }


def _planned_totals(db: Session, month: str) -> dict:
    """The month's recurring payments that haven't happened yet (base-currency equivalent)."""
    from .misc import planned_for_month

    conv = Converter(db)
    out = {"expense": 0, "income": 0, "count": 0}
    for p in planned_for_month(db, month):
        out[p["kind"]] += conv.to_base_cents(finance.to_cents(p["amount"]), p["currency"], date.today())
        out["count"] += 1
    return {"expense": finance.from_cents(out["expense"]), "income": finance.from_cents(out["income"]), "count": out["count"]}


def _filters(user_id: int | None, card_id: int | None, currency: str | None) -> analytics.Filters:
    return analytics.Filters(user_id=user_id or None, card_id=card_id or None, currency=currency or None)


@router.get("/trend")
def trend(
    months: int = 12,
    end: str | None = None,
    user_id: int | None = None,
    card_id: int | None = None,
    currency: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    end = end or date.today().strftime("%Y-%m")
    end_start, end_date = finance.month_range(end)
    first = finance.add_months(end_start, -(months - 1))
    out = {}
    for i in range(months):
        m = finance.add_months(first, i).strftime("%Y-%m")
        out[m] = {"month": m, "income": 0, "expense": 0}
    for t in analytics.transactions(db, first, end_date, _filters(user_id, card_id, currency)):
        out[t.date.strftime("%Y-%m")][t.kind] += t.base_cents
    return [
        {"month": v["month"], "income": finance.from_cents(v["income"]), "expense": finance.from_cents(v["expense"])}
        for v in out.values()
    ]


@router.get("/categories")
def categories(
    start: str,
    end: str,
    kind: str = "expense",
    user_id: int | None = None,
    card_id: int | None = None,
    currency: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    """start/end: YYYY-MM (inclusive)."""
    s, _e = finance.month_range(start)
    _s, e = finance.month_range(end)
    totals: dict[int | None, int] = {}
    for t in analytics.transactions(db, s, e, _filters(user_id, card_id, currency)):
        if t.kind == kind:
            totals[t.category_id] = totals.get(t.category_id, 0) + t.base_cents
    return sorted(
        [{"category_id": k, "total": finance.from_cents(v)} for k, v in totals.items()], key=lambda x: -x["total"]
    )


@router.get("/daily")
def daily(
    month: str | None = None,
    user_id: int | None = None,
    card_id: int | None = None,
    currency: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    """Cumulative daily spending within the month (this month / last month) and month-end forecast."""
    return analytics.daily_cumulative(db, month or date.today().strftime("%Y-%m"), _filters(user_id, card_id, currency))


@router.get("/category-trend")
def category_trend(
    category_id: int = 0,
    months: int = 12,
    end: str | None = None,
    user_id: int | None = None,
    card_id: int | None = None,
    currency: str | None = None,
    with_meta: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    """category_id=0 → uncategorized transactions."""
    if not 1 <= months <= 36:
        raise HTTPException(422, "Select 1–36 months")
    conv = Converter(db)
    series = analytics.category_trend(
        db, category_id or None, end or date.today().strftime("%Y-%m"), months, _filters(user_id, card_id, currency), conv
    )
    return {"series": series, "fx_missing": conv.missing_list()} if with_meta else series


@router.get("/stats")
def stats(
    start: str,
    end: str,
    user_id: int | None = None,
    card_id: int | None = None,
    currency: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    return analytics.stats(db, start, end, _filters(user_id, card_id, currency))


@router.get("/cashflow")
def cashflow(days: int = 30, user_id: int | None = None, card_id: int | None = None, currency: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Expected cash inflow/outflow for 30/60/90 days."""
    return analytics.cashflow(db, max(7, min(days, 120)), f=_filters(user_id, card_id, currency))


@router.get("/spendable")
def spendable_amounts(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Remaining spendable amount this month and the maximum for next month."""
    from .. import spendable

    return spendable.compute(db)


@router.get("/insights")
def insights(days: int = 90, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Savings insights: largest items, subscriptions, frequent purchases and suggestions."""
    from .. import insights as ins

    return ins.analyze(db, max(14, min(days, 365)))


@router.get("/plan")
def plan(months: int = 14, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Upcoming months: income, fixed expenses, debt, spending, hobbies, savings and cumulative balance."""
    from .. import planning

    return planning.projection(db, max(6, min(months, 24)))


class PlanSettingsIn(BaseModel):
    start_balance: float | None = Field(None, ge=-100_000_000, le=1_000_000_000)
    variable: float | None = Field(None, ge=0, le=100_000_000)
    buffer: float | None = Field(None, ge=0, le=100_000_000)


@router.put("/plan/settings")
def plan_settings(body: PlanSettingsIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    from .. import planning

    planning.save_settings(db, body.start_balance, body.variable, body.buffer)
    return planning.settings_out(db)


@router.get("/workspace")
def report_workspace(start: date, end: date, user_id: int | None = None, card_id: int | None = None,
                     currency: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    from ..reporting import workspace
    if start > end or start > date.today() or (end-start).days > 1096:
        raise HTTPException(422, "Select a valid date range of at most 3 years")
    if currency and currency not in prefs.SUPPORTED_CURRENCIES:
        raise HTTPException(422, "Invalid currency")
    return workspace(db, start, end, _filters(user_id, card_id, currency))
