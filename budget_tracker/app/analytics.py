"""Report and statistics calculations. All totals are computed in the household base-currency equivalent (cents)."""
import calendar
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import finance
from .fx import Converter
from .models import Budget, Category, RecurringPayment, Transaction

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Filters:
    user_id: int | None = None
    card_id: int | None = None
    currency: str | None = None


@dataclass
class Tx:
    id: int
    kind: str
    cents: int  # in the original currency
    base_cents: int  # base-currency equivalent
    currency: str
    date: date
    category_id: int | None
    user_id: int | None
    card_id: int | None
    payment_method: str
    merchant: str
    recurring_id: int | None
    installment_plan_id: int | None
    spending_type: str = "variable"


def transactions(db: Session, start: date, end: date, f: Filters | None = None, conv: Converter | None = None) -> list[Tx]:
    """If conv is given, amounts with missing FX rates are collected in its `missing` field."""
    f = f or Filters()
    q = select(Transaction).where(Transaction.date >= start, Transaction.date <= end)
    if f.user_id:
        q = q.where(Transaction.user_id == f.user_id)
    if f.card_id:
        q = q.where(Transaction.card_id == f.card_id)
    if f.currency:
        q = q.where(Transaction.currency == f.currency)
    conv = conv or Converter(db)
    out = []
    for t in db.scalars(q).unique():
        out.append(
            Tx(
                id=t.id,
                kind=t.kind,
                cents=t.amount_cents,
                base_cents=conv.to_base_cents(t.amount_cents, t.currency, t.date),
                currency=t.currency,
                date=t.date,
                category_id=t.category_id,
                user_id=t.user_id,
                card_id=t.card_id,
                payment_method=t.payment_method,
                merchant=t.merchant,
                recurring_id=t.recurring_id,
                installment_plan_id=t.installment_plan_id,
                spending_type="fixed" if t.recurring_id else t.spending_type,
            )
        )
    return out


def _sum(txs: list[Tx], kind: str) -> int:
    return sum(t.base_cents for t in txs if t.kind == kind)


def _pct(new: float, old: float) -> float | None:
    return round((new - old) / old, 4) if old else None


def month_bounds(ym: str) -> tuple[date, date]:
    return finance.month_range(ym)


def prev_month(ym: str) -> str:
    start, _ = finance.month_range(ym)
    return finance.add_months(start, -1).strftime("%Y-%m")


# ---- Daily cumulative spending and month-end projection ----------------------


def daily_cumulative(db: Session, ym: str, f: Filters | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    start, end = month_bounds(ym)
    pstart, pend = month_bounds(prev_month(ym))
    conv = Converter(db)
    cur = [t for t in transactions(db, start, min(end, today), f, conv) if t.kind == "expense"]
    prev = [t for t in transactions(db, pstart, pend, f, conv) if t.kind == "expense"]
    n_days = max(end.day, pend.day)
    cur_by_day, prev_by_day = defaultdict(int), defaultdict(int)
    for t in cur:
        cur_by_day[t.date.day] += t.base_cents
    for t in prev:
        prev_by_day[t.date.day] += t.base_cents

    is_current = start <= today <= end
    last_day = today.day if is_current else (end.day if today > end else 0)
    days, c_acc, p_acc = [], 0, 0
    for d in range(1, n_days + 1):
        c_acc += cur_by_day.get(d, 0)
        p_acc += prev_by_day.get(d, 0)
        days.append(
            {
                "day": d,
                "current": finance.from_cents(c_acc) if d <= min(last_day, end.day) else None,
                "previous": finance.from_cents(p_acc) if d <= pend.day else None,
            }
        )

    current_total = sum(t.base_cents for t in cur)
    same_day_prev = sum(v for d, v in prev_by_day.items() if d <= (last_day or end.day))
    projection = None
    projection_low = projection_high = None
    projection_basis = "This month's daily average"
    if is_current:
        variable = sum(t.base_cents for t in cur if t.spending_type == "variable")
        remaining = _remaining_recurring(db, today, end, f, conv)
        # Use complete historical weeks, excluding explicit one-off / fixed expenses.
        monday = today - timedelta(days=today.weekday())
        history_start = monday - timedelta(days=56)
        history = [t for t in transactions(db, history_start, monday-timedelta(days=1), f, conv)
                   if t.kind == "expense" and t.spending_type == "variable"]
        first = min((t.date for t in history), default=monday)
        week_start = max(history_start, first + timedelta(days=(7-first.weekday()) % 7))
        weeks = []
        while week_start < monday:
            weeks.append(sum(t.base_cents for t in history if week_start <= t.date < week_start+timedelta(days=7)))
            week_start += timedelta(days=7)
        pace = variable / today.day
        if len(weeks) >= 4 and sum(v > 0 for v in weeks) >= 4:
            pace = statistics.median(weeks) / 7
            projection_basis = f"Median of the last {len(weeks)} complete weeks (excluding one-offs)"
            low, high = sorted(weeks)[len(weeks)//4]/7, sorted(weeks)[3*len(weeks)//4]/7
            projection_low = round(current_total + remaining + low*(end.day-today.day))/100
            projection_high = round(current_total + remaining + high*(end.day-today.day))/100
        projection = current_total + remaining + round(pace * (end.day-today.day))
    return {
        "month": ym,
        "days": days,
        "current_total": finance.from_cents(current_total),
        "previous_same_day": finance.from_cents(same_day_prev),
        "previous_total": finance.from_cents(sum(t.base_cents for t in prev)),
        "projection": finance.from_cents(projection) if projection is not None else None,
        "elapsed_days": last_day,
        "projection_low": projection_low,
        "projection_high": projection_high,
        "projection_basis": projection_basis,
        "fx_missing": conv.missing_list(),
    }


def _remaining_recurring(db: Session, today: date, month_end: date, f: Filters | None = None, conv: Converter | None = None) -> int:
    conv, f = conv or Converter(db), f or Filters()
    total = 0
    for r in db.scalars(select(RecurringPayment).where(RecurringPayment.active.is_(True), RecurringPayment.kind == "expense")):
        if (f.user_id and r.user_id != f.user_id) or (f.card_id and r.card_id != f.card_id) or (f.currency and r.currency != f.currency):
            continue
        for on in finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, month_end):
            if on > today:
                total += conv.to_base_cents(finance.recurring_amount(r, on), r.currency, today)
    return total


# ---- Category trend --------------------------------------------------------------


def category_trend(db: Session, category_id: int | None, end_ym: str, months: int, f: Filters | None = None, conv: Converter | None = None) -> list[dict]:
    end_start, end = month_bounds(end_ym)
    first = finance.add_months(end_start, -(months - 1))
    totals = defaultdict(int)
    for t in transactions(db, first, min(end, date.today()), f, conv or Converter(db)):
        if t.kind == "expense" and t.category_id == category_id:
            totals[t.date.strftime("%Y-%m")] += t.base_cents
    out = []
    for i in range(months):
        m = finance.add_months(first, i).strftime("%Y-%m")
        out.append({"month": m, "total": finance.from_cents(totals.get(m, 0))})
    return out


# ---- Statistics ----------------------------------------------------------------


def stats(db: Session, start_ym: str, end_ym: str, f: Filters | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    start, _ = month_bounds(start_ym)
    end_start, end = month_bounds(end_ym)
    conv = Converter(db)
    txs = transactions(db, start, min(end, today), f, conv)
    expenses = [t for t in txs if t.kind == "expense"]
    expense, income = _sum(txs, "expense"), _sum(txs, "income")

    # Average daily spending: days that haven't happened yet are not counted
    span_end = min(end, today) if start <= today else end
    n_days = max(1, (span_end - start).days + 1)
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    tickets = sorted(t.base_cents for t in expenses)

    merchants: dict[str, dict] = {}
    for t in expenses:
        key = finance.normalize_merchant(t.merchant) or f"cat:{t.category_id}"
        m = merchants.setdefault(key, {"name": t.merchant or "", "category_id": t.category_id, "total": 0, "count": 0})
        m["total"] += t.base_cents
        m["count"] += 1
        if not m["name"] and t.merchant:
            m["name"] = t.merchant
    top_merchants = sorted(merchants.values(), key=lambda m: -m["total"])[:10]

    weekday = [{"day": WEEKDAYS[i], "total": 0, "count": 0} for i in range(7)]
    by_method = defaultdict(int)
    by_card = defaultdict(int)
    by_user = defaultdict(int)
    for t in expenses:
        w = weekday[t.date.weekday()]
        w["total"] += t.base_cents
        w["count"] += 1
        by_method[t.payment_method] += t.base_cents
        if t.card_id:
            by_card[t.card_id] += t.base_cents
        by_user[t.user_id] += t.base_cents

    recurring = sum(t.base_cents for t in expenses if t.recurring_id)
    installment = sum(t.base_cents for t in expenses if t.installment_plan_id)

    # Category changes between the last month and the month before
    last = [t for t in expenses if t.date >= end_start]
    pstart, pend = month_bounds(prev_month(end_ym))
    if end_start <= today <= end:
        pend = finance.clamp_day(pstart.year, pstart.month, today.day)
    prev = [t for t in transactions(db, pstart, pend, f) if t.kind == "expense"]
    cat_now, cat_prev = defaultdict(int), defaultdict(int)
    for t in last:
        cat_now[t.category_id] += t.base_cents
    for t in prev:
        cat_prev[t.category_id] += t.base_cents
    changes = [
        {
            "category_id": c,
            "current": finance.from_cents(cat_now.get(c, 0)),
            "previous": finance.from_cents(cat_prev.get(c, 0)),
            "delta": finance.from_cents(cat_now.get(c, 0) - cat_prev.get(c, 0)),
        }
        for c in set(cat_now) | set(cat_prev)
    ]
    changes.sort(key=lambda x: -abs(x["delta"]))

    year_start = date(today.year, 1, 1)
    ytd = transactions(db, year_start, today, f)

    def money(c: int) -> float:
        return finance.from_cents(c)

    return {
        "start": start_ym,
        "end": end_ym,
        "months": months,
        "days": n_days,
        "count": len(expenses),
        "income_count": len(txs) - len(expenses),
        "expense": money(expense),
        "income": money(income),
        "net": money(income - expense),
        "savings_rate": round((income - expense) / income, 4) if income else None,
        "daily_avg": money(round(expense / n_days)),
        "monthly_avg": money(round(expense / months)),
        "avg_ticket": money(round(sum(tickets) / len(tickets))) if tickets else 0,
        "median_ticket": money(round(statistics.median(tickets))) if tickets else 0,
        "max_ticket": money(tickets[-1]) if tickets else 0,
        "recurring": money(recurring),
        "installment": money(installment),
        "top_merchants": [
            {"name": m["name"] or "—", "category_id": m["category_id"], "total": money(m["total"]), "count": m["count"]}
            for m in top_merchants
        ],
        "largest": [
            {
                "id": t.id,
                "merchant": t.merchant,
                "date": t.date.isoformat(),
                "amount": money(t.cents),
                "currency": t.currency,
                "base_amount": money(t.base_cents),
                "category_id": t.category_id,
            }
            for t in sorted(expenses, key=lambda t: -t.base_cents)[:5]
        ],
        "weekday": [{**w, "total": money(w["total"])} for w in weekday],
        "by_method": {k: money(v) for k, v in by_method.items()},
        "by_card": [{"card_id": k, "total": money(v)} for k, v in sorted(by_card.items(), key=lambda x: -x[1])],
        "by_user": [{"user_id": k, "total": money(v)} for k, v in sorted(by_user.items(), key=lambda x: -x[1])],
        "category_changes": changes[:6],
        "ytd": {"expense": money(_sum(ytd, "expense")), "income": money(_sum(ytd, "income")), "year": today.year},
        "fx_missing": conv.missing_list(),
    }


# ---- Monthly summary (notification) ----------------------------------------------


def monthly_summary(db: Session, ym: str) -> dict:
    start, end = month_bounds(ym)
    txs = transactions(db, start, end)
    pstart, pend = month_bounds(prev_month(ym))
    prev = transactions(db, pstart, pend)
    expense, income = _sum(txs, "expense"), _sum(txs, "income")
    prev_expense = _sum(prev, "expense")

    by_cat = defaultdict(int)
    for t in txs:
        if t.kind == "expense":
            by_cat[t.category_id] += t.base_cents
    names = {c.id: c.name for c in db.scalars(select(Category))}
    top = sorted(by_cat.items(), key=lambda x: -x[1])[:3]

    over = []
    for b in db.scalars(select(Budget).where(Budget.month == ym)):
        spent = by_cat.get(b.category_id, 0)
        if b.limit_cents and spent > b.limit_cents:
            over.append({"category": names.get(b.category_id, "?"), "spent": spent, "limit": b.limit_cents})

    return {
        "month": ym,
        "expense": expense,
        "income": income,
        "net": income - expense,
        "change": _pct(expense, prev_expense),
        "top": [(names.get(c, "Uncategorized"), v) for c, v in top],
        "over_budget": over,
        "days": calendar.monthrange(start.year, start.month)[1],
    }


# ---- Cash flow (30/60/90 days) -------------------------------------------------------


def cashflow(db: Session, days: int = 30, today: date | None = None, f: Filters | None = None) -> dict:
    """Expected cash inflows and outflows for the coming days (base-currency equivalent).

    Card spending and recurring payments charged to a card are not counted individually as expenses;
    they are counted only as an outflow on the card's due date, as that period's estimated statement (no double counting).
    """
    from . import services
    from .models import CardStatement, CreditCard, InstallmentPlan

    today = today or date.today()
    horizon = today + timedelta(days=days)
    f = f or Filters()
    conv = Converter(db)
    events: list[dict] = []

    def add(on: date, title: str, cents: int, direction: str, kind: str, estimated: bool = False, note: str = ""):
        if cents > 0 and today <= on <= horizon:
            events.append({"date": on.isoformat(), "title": title, "amount": finance.from_cents(cents),
                           "direction": direction, "kind": kind, "estimated": estimated, "note": note})

    # Recurring payments / income
    card_recurring: dict[int, list[tuple[date, int]]] = defaultdict(list)
    for r in db.scalars(select(RecurringPayment).where(RecurringPayment.active.is_(True))):
        if (f.user_id and r.user_id != f.user_id) or (f.card_id and r.card_id != f.card_id) or (f.currency and r.currency != f.currency):
            continue
        if r.payment_method == "voucher":
            continue  # vouchers are not cash: they never enter or leave an account
        for d in finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, horizon + timedelta(days=62)):
            cents = conv.to_base_cents(finance.recurring_amount(r, d), r.currency, today)
            if r.kind == "income":
                add(d, r.name, cents, "in", "recurring")
            elif r.payment_method == "card" and r.card_id:
                card_recurring[r.card_id].append((d, cents))  # goes onto the card statement
            else:
                add(d, r.name, cents, "out", "recurring")

    for card in db.scalars(select(CreditCard).where(CreditCard.archived.is_(False))):
        if (f.card_id and card.id != f.card_id) or (f.user_id and card.owner_id != f.user_id):
            continue
        # Confirmed: remaining balance of unpaid statements
        for st in db.scalars(select(CardStatement).where(CardStatement.card_id == card.id, CardStatement.paid.is_(False))):
            rem = services.statement_remaining(db, st)
            cents = sum(conv.to_base_cents(v, c, today) for c, v in rem.items() if not f.currency or c == f.currency)
            add(max(st.due_date, today), f"{card.name} statement", cents, "out", "statement",
                note="overdue" if st.due_date < today else "")

        # Estimated: upcoming statements
        plans = db.scalars(select(InstallmentPlan).where(InstallmentPlan.card_id == card.id)).all()
        prev_cut = finance.last_statement_cut(card, today)
        cut = finance.next_statement_date(card, today)
        # Period past its closing date but statement not uploaded yet: estimate from recorded spending
        uploaded = db.scalar(select(CardStatement.id).where(
            CardStatement.card_id == card.id,
            CardStatement.period_end >= prev_cut - timedelta(days=5),
            CardStatement.period_end <= prev_cut + timedelta(days=5),
        ))
        if not uploaded:
            before = finance.add_months(finance.month_start(prev_cut), -1)
            before_cut = finance.clamp_day(before.year, before.month, card.statement_day)
            due_month = prev_cut if card.due_day > card.statement_day else finance.add_months(prev_cut, 1)
            due = finance.business_day(finance.clamp_day(due_month.year, due_month.month, card.due_day))
            if due >= today:
                cents = 0
                for t in transactions(db, before_cut + timedelta(days=1), prev_cut, Filters(card_id=card.id, currency=f.currency), conv):
                    if t.kind == "expense" and not t.installment_plan_id:
                        cents += t.base_cents
                    elif t.kind == "income":
                        cents -= t.base_cents
                month = finance.month_start(prev_cut)
                for p in plans:
                    if f.currency and p.currency != f.currency:
                        continue
                    idx = (month.year - p.first_month.year) * 12 + (month.month - p.first_month.month)
                    if 0 <= idx < p.count:
                        cents += conv.to_base_cents(p.monthly_cents, p.currency, today)
                add(due, f"{card.name} (statement pending)", cents, "out", "card", estimated=True,
                    note="closing date passed, statement not uploaded yet")
        for k in range(4):
            due_month = cut if card.due_day > card.statement_day else finance.add_months(cut, 1)
            due = finance.business_day(finance.clamp_day(due_month.year, due_month.month, card.due_day))
            if due > horizon:
                break
            cents = 0
            if k == 0:
                # This period's spending (excluding installment purchases; their installment for this month is added below)
                for t in transactions(db, prev_cut + timedelta(days=1), today, Filters(card_id=card.id, currency=f.currency), conv):
                    if t.kind == "expense" and not t.installment_plan_id:
                        cents += t.base_cents
                    elif t.kind == "income":
                        cents -= t.base_cents
            month = finance.month_start(cut)
            for p in plans:
                if f.currency and p.currency != f.currency:
                    continue
                idx = (month.year - p.first_month.year) * 12 + (month.month - p.first_month.month)
                if 0 <= idx < p.count:
                    cents += conv.to_base_cents(p.monthly_cents, p.currency, today)
            cents += sum(c for d, c in card_recurring.get(card.id, []) if prev_cut < d <= cut and d >= today)
            note = "this period's spending + installments" if k == 0 else "installments and recurring payments only (minimum)"
            add(due, f"{card.name} (estimated)", cents, "out", "card", estimated=True, note=note)
            prev_cut, cut = cut, finance.clamp_day(*finance.add_months(month, 1).timetuple()[:2], card.statement_day)

    events.sort(key=lambda e: (e["date"], e["direction"] != "in"))
    total_in = sum(e["amount"] for e in events if e["direction"] == "in")
    total_out = sum(e["amount"] for e in events if e["direction"] == "out")

    # Daily cumulative net and weekly totals
    by_day: dict[str, float] = defaultdict(float)
    for e in events:
        by_day[e["date"]] += e["amount"] if e["direction"] == "in" else -e["amount"]
    series, acc = [], 0.0
    for i in range(days + 1):
        d = (today + timedelta(days=i)).isoformat()
        acc += by_day.get(d, 0)
        series.append({"date": d, "net": round(acc, 2)})
    weeks = []
    for w in range(0, days + 1, 7):
        ws, we = today + timedelta(days=w), min(today + timedelta(days=w + 6), horizon)
        inside = [e for e in events if ws.isoformat() <= e["date"] <= we.isoformat()]
        weeks.append({
            "start": ws.isoformat(), "end": we.isoformat(),
            "in": round(sum(e["amount"] for e in inside if e["direction"] == "in"), 2),
            "out": round(sum(e["amount"] for e in inside if e["direction"] == "out"), 2),
        })
    opening_balance = minimum_balance = None
    if not any((f.user_id, f.card_id, f.currency)):
        from .models import Account
        from .savings import balance, reserved
        cash_accounts = list(db.scalars(select(Account).where(Account.archived.is_(False), Account.kind != "savings")))
        if cash_accounts:
            opening_balance = sum(conv.to_base_cents(balance(db,a,today)-reserved(db,a.id,today),a.currency,today) for a in cash_accounts) / 100
            if conv.missing:
                opening_balance = None
            else:
                minimum_balance = round(opening_balance + min(p["net"] for p in series),2)
    return {
        "days": days,
        "opening_balance": opening_balance,
        "minimum_balance": minimum_balance,
        "in": round(total_in, 2),
        "out": round(total_out, 2),
        "net": round(total_in - total_out, 2),
        "events": events,
        "series": series,
        "weeks": weeks,
        "fx_missing": conv.missing_list(),
    }
