"""Date-scoped report workspace. Read-only calculations; no network or materialization."""

from collections import defaultdict
from datetime import date, timedelta
import statistics

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import analytics, finance, prefs
from .fx import Converter
from .models import Budget, Category, Document, RecurringPayment, Transaction


def comparison_range(start: date, end: date) -> tuple[date, date]:
    # Calendar ranges compare to the preceding same-length block of calendar months.
    if start.day == 1:
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        previous_start = finance.add_months(start, -months)
        previous_end_month = finance.add_months(end.replace(day=1), -months)
        previous_day = (
            finance.month_range(previous_end_month.strftime("%Y-%m"))[1].day
            if end == finance.month_range(end.strftime("%Y-%m"))[1]
            else end.day
        )
        return previous_start, finance.clamp_day(
            previous_end_month.year, previous_end_month.month, previous_day
        )
    return start - timedelta(days=(end - start).days + 1), start - timedelta(days=1)


def workspace(
    db: Session,
    start: date,
    end: date,
    f: analytics.Filters | None = None,
    today: date | None = None,
) -> dict:
    today, f = today or date.today(), f or analytics.Filters()
    period_end = end  # for expected recurring income/expenses in the remaining days of the period
    end = min(end, today)
    ps, pe = comparison_range(start, end)
    conv, previous_conv = Converter(db), Converter(db)
    rows = analytics.transactions(db, start, end, f, conv)
    prev = analytics.transactions(db, ps, pe, f, previous_conv)
    expense = [t for t in rows if t.kind == "expense"]
    previous = [t for t in prev if t.kind == "expense"]
    cats = {c.id: c for c in db.scalars(select(Category))}
    money = lambda v: round(v / 100, 2)
    total = sum(t.base_cents for t in expense)
    income = sum(t.base_cents for t in rows if t.kind == "income")
    cat_ids = {t.category_id for t in expense + previous}
    changes = []
    for cid in cat_ids:
        current = [t for t in expense if t.category_id == cid]
        old = [t for t in previous if t.category_id == cid]
        now, before = sum(t.base_cents for t in current), sum(t.base_cents for t in old)
        changes.append(
            dict(
                category_id=cid,
                name=cats[cid].name if cid in cats else "Uncategorized",
                current=money(now),
                previous=money(before),
                delta=money(now - before),
                count=len(current),
                previous_count=len(old),
                average=money(now / len(current)) if current else 0,
                previous_average=money(before / len(old)) if old else 0,
            )
        )
    changes.sort(key=lambda c: -abs(c["delta"]))
    series = []
    month = start.replace(day=1)
    while month <= end:
        subset = [
            t for t in rows if t.date.strftime("%Y-%m") == month.strftime("%Y-%m")
        ]
        series.append(
            dict(
                month=month.strftime("%Y-%m"),
                income=money(sum(t.base_cents for t in subset if t.kind == "income")),
                expense=money(sum(t.base_cents for t in subset if t.kind == "expense")),
                **{
                    k: money(
                        sum(
                            t.base_cents
                            for t in subset
                            if t.kind == "expense" and t.spending_type == k
                        )
                    )
                    for k in ("fixed", "variable", "one_off")
                },
            )
        )
        month = finance.add_months(month, 1)
    calendar = []
    on = start
    while on <= end:
        same = [t for t in expense if t.date == on]
        calendar.append(
            dict(
                date=on.isoformat(),
                total=money(sum(t.base_cents for t in same)),
                count=len(same),
            )
        )
        on += timedelta(days=1)
    merchants = defaultdict(list)
    for t in expense:
        merchants[finance.normalize_merchant(t.merchant) or "—"].append(t)
    merchant_rows = [
        dict(
            name=key,
            display=ts[0].merchant or "—",
            total=money(sum(t.base_cents for t in ts)),
            count=len(ts),
        )
        for key, ts in merchants.items()
    ]
    merchant_rows.sort(key=lambda m: -m["total"])
    budget_rows = []
    budget_month = end.strftime("%Y-%m")
    ms, me = finance.month_range(budget_month)
    budget_conv = Converter(db)
    month_expenses = [
        t
        for t in analytics.transactions(db, ms, min(me, today), f, budget_conv)
        if t.kind == "expense"
    ]
    recs = list(
        db.scalars(select(RecurringPayment).where(RecurringPayment.active.is_(True)))
    )

    def selected(r):
        return not (
            (f.user_id and r.user_id != f.user_id)
            or (f.card_id and r.card_id != f.card_id)
            or (f.currency and r.currency != f.currency)
        )

    recs = [r for r in recs if selected(r)]
    for b in db.scalars(select(Budget).where(Budget.month == budget_month)):
        spent = sum(
            t.base_cents for t in month_expenses if t.category_id == b.category_id
        )
        planned = 0
        for r in recs:
            if r.kind == "expense" and r.category_id == b.category_id:
                for day in finance.recurring_dates(
                    r.next_date, r.frequency, r.day, r.end_date, me
                ):
                    if max(ms, today + timedelta(days=1)) <= day:
                        planned += budget_conv.to_base_cents(
                            finance.recurring_amount(r, day), r.currency, today
                        )
        budget_rows.append(
            dict(
                category_id=b.category_id,
                name=cats[b.category_id].name,
                spent=money(spent),
                planned=money(planned),
                limit=money(b.limit_cents),
            )
        )
    subscription_rows = []
    for r in recs:
        cat = cats.get(r.category_id)
        if r.kind != "expense" or not cat or "abonelik" not in cat.name.casefold():
            continue
        history = list(
            db.scalars(
                select(Transaction)
                .where(Transaction.recurring_id == r.id)
                .order_by(Transaction.date, Transaction.id)
            )
        )
        older = next(
            (
                t
                for t in reversed(history)
                if t.amount_cents != r.amount_cents and t.currency == r.currency
            ),
            None,
        )
        annual = (
            r.amount_cents * {"weekly": 52, "monthly": 12, "yearly": 1}[r.frequency]
        )
        subscription_rows.append(
            dict(
                id=r.id,
                name=r.name,
                currency=r.currency,
                amount=money(r.amount_cents),
                frequency=r.frequency,
                monthly=money(annual / 12),
                annual=money(annual),
                previous=money(older.amount_cents) if older else None,
                history=[
                    dict(date=t.date.isoformat(), amount=money(t.amount_cents))
                    for t in history[-12:]
                ],
            )
        )
    review = (
        db.scalar(select(func.count(Document.id)).where(Document.status == "review"))
        or 0
    )
    uncategorized = [t for t in expense if t.category_id is None]
    insights = []
    if changes:
        c = changes[0]
        insights.append(
            f"{c['name']}: {c['delta']:+,.0f} {prefs.base()} vs. the previous period of equal length. Transactions {c['previous_count']} → {c['count']}; average per transaction {c['previous_average']:,.0f} → {c['average']:,.0f} {prefs.base()}."
        )
    over = [b for b in budget_rows if b["spent"] + b["planned"] > b["limit"]]
    if over:
        insights.append(
            f"In {len(over)} categories, actual spending plus remaining recurring payments exceeds the monthly limit."
        )
    oneoff = sum(t.base_cents for t in expense if t.spending_type == "one_off")
    if oneoff:
        insights.append(
            f"This period includes {money(oneoff):,.0f} {prefs.base()} of one-off expenses; they are not repeated in the spending pace projection."
        )
    weekday = []
    for day in range(7):
        items = [t for t in expense if t.date.weekday() == day]
        occurrences = sum(
            1 for c in calendar if date.fromisoformat(c["date"]).weekday() == day
        )
        weekday.append(
            dict(
                day=analytics.WEEKDAYS[day],
                total=money(sum(t.base_cents for t in items)),
                count=len(items),
                average=money(sum(t.base_cents for t in items) / max(1, occurrences)),
            )
        )

    def shares(attr):
        grouped = defaultdict(int)
        for t in expense:
            grouped[getattr(t, attr)] += t.base_cents
        return [dict(key=k, total=money(v)) for k, v in grouped.items()]

    income_categories = defaultdict(int)
    for t in rows:
        if t.kind == "income":
            income_categories[t.category_id] += t.base_cents
    ytd_conv = Converter(db)
    ytd = analytics.transactions(db, date(end.year, 1, 1), end, f, ytd_conv)
    first = db.scalar(select(func.min(Transaction.date)))
    return dict(
        start=start.isoformat(),
        end=end.isoformat(),
        comparison_start=ps.isoformat(),
        comparison_end=pe.isoformat(),
        expense=money(total),
        income=money(income),
        net=money(income - total),
        surplus_rate=round((income - total) / income, 4)
        if income and not conv.missing
        else None,
        **_expected_rate(db, today, period_end, f, income, total, conv),
        daily_avg=money(total / max(1, (end - start).days + 1)),
        median=money(statistics.median([t.base_cents for t in expense]))
        if expense
        else 0,
        average_ticket=money(total / len(expense)) if expense else 0,
        monthly_average=money(total / max(1, len(series))),
        ytd=dict(
            year=end.year,
            income=money(sum(t.base_cents for t in ytd if t.kind == "income")),
            expense=money(sum(t.base_cents for t in ytd if t.kind == "expense")),
            fx_missing=ytd_conv.missing_list(),
        ),
        count=len(expense),
        weekday=weekday,
        by_method=shares("payment_method"),
        by_card=shares("card_id"),
        by_user=shares("user_id"),
        income_categories=[
            dict(
                category_id=k,
                name=cats[k].name if k in cats else "Uncategorized",
                total=money(v),
            )
            for k, v in income_categories.items()
        ],
        largest=[
            dict(
                id=t.id,
                merchant=t.merchant,
                date=t.date.isoformat(),
                amount=money(t.cents),
                currency=t.currency,
            )
            for t in sorted(expense, key=lambda t: -t.base_cents)[:5]
        ],
        changes=changes,
        series=series,
        calendar=calendar,
        merchants=merchant_rows[:20],
        budgets=budget_rows,
        budget_month=budget_month,
        budget_fx_missing=budget_conv.missing_list(),
        subscriptions=subscription_rows,
        insights=[] if conv.missing or previous_conv.missing else insights[:3],
        quality=dict(
            review_documents=review,
            uncategorized_count=len(uncategorized),
            uncategorized_amount=money(sum(t.base_cents for t in uncategorized)),
            recorded_since=first.isoformat() if first else None,
        ),
        fx_missing=conv.missing_list(),
        comparison_fx_missing=previous_conv.missing_list(),
    )


def _expected_rate(db: Session, today: date, period_end: date, f, income: int, expense: int, conv: Converter) -> dict:
    """If the rate cannot be calculated, the reason, plus an expected rate using recurring income/expenses for the rest of the period."""
    planned_in = planned_out = 0
    if period_end > today:
        for r in db.scalars(select(RecurringPayment).where(RecurringPayment.active.is_(True))):
            if (f.user_id and r.user_id != f.user_id) or r.payment_method == "voucher":
                continue
            for d in finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, period_end):
                if d > today:
                    cents = conv.to_base_cents(finance.recurring_amount(r, d), r.currency, today)
                    if r.kind == "income":
                        planned_in += cents
                    else:
                        planned_out += cents
    if conv.missing:
        reason = "Exchange rates are missing for some foreign currency transactions"
    elif not income:
        reason = "No income recorded in this period yet"
    else:
        reason = None
    exp_income = income + planned_in
    return dict(
        rate_reason=reason,
        planned_income=round(planned_in / 100, 2),
        planned_expense=round(planned_out / 100, 2),
        expected_rate=round((exp_income - expense - planned_out) / exp_income, 4) if exp_income and planned_in else None,
    )
