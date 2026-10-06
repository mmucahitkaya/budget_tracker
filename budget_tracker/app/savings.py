"""Account ledger and goal reservations. Transfers never enter the expense table.

Balances are explicitly maintained by opening balances and dated movements. Goals reserve
existing money; reservations cannot exceed any account's balance, even after backdated edits.
"""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import finance, investments
from .fx import Converter
from .models import (
    Account,
    AccountMovement,
    Category,
    CreditCard,
    GoalAllocation,
    SavingsGoal,
    Transaction,
)


def write_lock(db: Session) -> None:
    # current_user may have opened a read transaction. All ledger writes use one SQLite writer.
    db.commit()
    db.execute(text("BEGIN IMMEDIATE"))


def balance(
    db: Session,
    account: Account,
    on: date,
    movements: list[AccountMovement] | None = None,
) -> int:
    if on < account.opening_date:
        return 0
    result = account.opening_cents
    for m in (
        movements
        if movements is not None
        else db.scalars(select(AccountMovement).where(AccountMovement.date <= on))
    ):
        if m.date > on:
            continue
        if m.source_id == account.id:
            result -= m.source_cents
        if m.target_id == account.id:
            result += m.target_cents
    return result


def reserved(db: Session, account_id: int, on: date) -> int:
    return sum(
        a.amount_cents
        for a in db.scalars(
            select(GoalAllocation).where(
                GoalAllocation.account_id == account_id, GoalAllocation.date <= on
            )
        )
    )


def validate_ledger(db: Session) -> None:
    """Validate every affected historical boundary, not only the current balance."""
    db.flush()
    movements = list(db.scalars(select(AccountMovement)))
    allocations = list(db.scalars(select(GoalAllocation)))
    for account in db.scalars(select(Account)):
        events = defaultdict(lambda: [0, 0])
        events[account.opening_date][0] += account.opening_cents
        for m in movements:
            if account.id in (m.source_id, m.target_id):
                if m.date < account.opening_date:
                    raise ValueError("Movement date cannot be before the account opening date")
                events[m.date][0] += (
                    m.target_cents if m.target_id == account.id else 0
                ) - (m.source_cents if m.source_id == account.id else 0)
        goals = defaultdict(int)
        for a in sorted(allocations, key=lambda x: (x.date, x.id)):
            if a.account_id == account.id:
                events[a.date][1] += a.amount_cents
                goals[a.goal_id] += a.amount_cents
                if goals[a.goal_id] < 0:
                    raise ValueError(
                        "Cannot release more than the amount reserved for the goal"
                    )
        cash = allocated = 0
        for on in sorted(events):
            cash += events[on][0]
            allocated += events[on][1]
            if cash < 0 or allocated < 0 or allocated > cash:
                raise ValueError(
                    f"{account.name}: insufficient balance or the money is reserved for a goal. Reduce the reservation first."
                )
        if account.archived and (cash or allocated):
            raise ValueError(
                "To archive, bring the account balance and goal reservations to zero"
            )


def account_out(db: Session, a: Account, today: date) -> dict:
    cents = balance(db, a, today)
    allocated = reserved(db, a.id, today)
    dates = [a.opening_date] + [
        m.date
        for m in db.scalars(
            select(AccountMovement).where(
                (AccountMovement.source_id == a.id)
                | (AccountMovement.target_id == a.id)
            )
        )
    ]
    return dict(
        id=a.id,
        name=a.name,
        kind=a.kind,
        currency=a.currency,
        owner_id=a.owner_id,
        opening_balance=a.opening_cents / 100,
        opening_date=a.opening_date.isoformat(),
        balance=cents / 100,
        allocated=allocated / 100,
        available=(cents - allocated) / 100,
        updated_on=max(dates).isoformat(),
        archived=a.archived,
    )


def goal_out(db: Session, g: SavingsGoal, today: date, by_goal: dict[int | None, int] | None = None) -> dict:
    rows = list(
        db.scalars(select(GoalAllocation).where(GoalAllocation.goal_id == g.id))
    )
    total = sum(a.amount_cents for a in rows)
    # Investments linked to a goal (gold, funds, stocks…) count at their current value
    invested = investments.goal_value_cents(db, g, today, by_goal)
    remaining = max(0, g.target_cents - total - invested)
    months = (
        max(
            1,
            (g.target_date.year - today.year) * 12
            + g.target_date.month
            - today.month
            + 1,
        )
        if g.target_date
        else None
    )
    by_user, by_account = defaultdict(int), defaultdict(int)
    for a in rows:
        by_user[a.user_id] += a.amount_cents
        by_account[a.account_id] += a.amount_cents
    month_total = sum(a.amount_cents for a in rows if a.date >= today.replace(day=1))
    return dict(
        id=g.id,
        name=g.name,
        currency=g.currency,
        target=g.target_cents / 100,
        target_date=g.target_date.isoformat() if g.target_date else None,
        owner_id=g.owner_id,
        monthly=g.monthly_cents / 100,
        reminder_day=g.reminder_day,
        emergency=g.emergency,
        archived=g.archived,
        allocated=total / 100,
        invested=invested / 100,
        funded=(total + invested) / 100,
        remaining=remaining / 100,
        required_monthly=round(remaining / months / 100, 2) if months else None,
        overdue=bool(g.target_date and g.target_date < today and remaining),
        month_allocated=month_total / 100,
        contributions=[dict(user_id=k, amount=v / 100) for k, v in by_user.items()],
        accounts=[
            dict(account_id=k, amount=v / 100) for k, v in by_account.items() if v
        ],
    )


def overview(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    accounts = list(db.scalars(select(Account)))
    goals = list(db.scalars(select(SavingsGoal)))
    movements = list(
        db.scalars(
            select(AccountMovement).order_by(AccountMovement.date, AccountMovement.id)
        )
    )
    lookup = {a.id: a for a in accounts}
    saved = {a.id for a in accounts if a.kind == "savings"}
    conv = Converter(db)
    convert = conv.to_base_cents
    totals = defaultdict(lambda: dict(balance=0, allocated=0, available=0))
    for a in accounts:
        if a.id in saved:
            b, r = balance(db, a, today, movements), reserved(db, a.id, today)
            totals[a.currency]["balance"] += b / 100
            totals[a.currency]["allocated"] += r / 100
            totals[a.currency]["available"] += (b - r) / 100
    assets = sum(
        convert(balance(db, a, today, movements), a.currency, today) for a in accounts
    )
    savings = sum(
        convert(balance(db, a, today, movements), a.currency, today)
        for a in accounts
        if a.id in saved
    )
    debt = sum(
        convert(int(cents or 0), cur, today)
        for c in db.scalars(select(CreditCard).where(CreditCard.archived.is_(False)))
        for cur, cents in (c.debts or {}).items()
    )
    series = []
    for offset in range(-11, 1):
        start = finance.add_months(today.replace(day=1), offset)
        end = min(finance.add_months(start, 1) - timedelta(days=1), today)
        before = start - timedelta(days=1)
        start_value = sum(
            convert(balance(db, a, before, movements), a.currency, before)
            for a in accounts
            if a.id in saved
        )
        end_value = sum(
            convert(balance(db, a, end, movements), a.currency, end)
            for a in accounts
            if a.id in saved
        )
        deposits = withdrawals = adjustments = 0
        for a in accounts:
            if a.id in saved and start <= a.opening_date <= end:
                adjustments += convert(a.opening_cents, a.currency, a.opening_date)
        for m in movements:
            if not start <= m.date <= end:
                continue
            source_saved, target_saved = m.source_id in saved, m.target_id in saved
            if source_saved == target_saved:
                continue  # moving between savings accounts is not a new contribution
            value = (
                convert(m.target_cents, lookup[m.target_id].currency, m.date)
                if target_saved
                else -convert(m.source_cents, lookup[m.source_id].currency, m.date)
            )
            if m.kind == "adjustment":
                adjustments += value
            elif value >= 0:
                deposits += value
            else:
                withdrawals -= value
        # FX / cross-currency conversion effects are explicitly separate from contributions.
        series.append(
            dict(
                month=start.strftime("%Y-%m"),
                balance=end_value / 100,
                deposit=deposits / 100,
                withdrawal=withdrawals / 100,
                contribution=(deposits - withdrawals) / 100,
                adjustment=adjustments / 100,
                valuation_change=(
                    end_value - start_value - deposits + withdrawals - adjustments
                )
                / 100,
            )
        )
    essential_ids = list(
        db.scalars(select(Category.id).where(Category.essential.is_(True)))
    )
    essential_start = finance.add_months(today.replace(day=1), -3)
    essential_end = today.replace(day=1) - timedelta(days=1)
    essential_conv = Converter(db)
    essential = (
        sum(
            essential_conv.to_base_cents(t.amount_cents, t.currency, t.date)
            for t in db.scalars(
                select(Transaction).where(
                    Transaction.kind == "expense",
                    Transaction.category_id.in_(essential_ids),
                    Transaction.date >= essential_start,
                    Transaction.date <= essential_end,
                )
            )
        )
        / 3
    )
    by_goal = investments.values_by_goal(db)  # investments are valued once
    emergency_ids = [g.id for g in goals if g.emergency and not g.archived]
    emergency = sum(
        convert(a.amount_cents, lookup[a.account_id].currency, today)
        for a in db.scalars(
            select(GoalAllocation).where(GoalAllocation.goal_id.in_(emergency_ids))
        )
    ) + sum(by_goal.get(gid, 0) for gid in emergency_ids)
    invested_total = sum(by_goal.values())
    liquidity = sum(
        convert(
            balance(db, a, today, movements) - reserved(db, a.id, today),
            a.currency,
            today,
        )
        for a in accounts
        if a.kind != "savings"
    )
    missing_currencies = set(conv.missing) | set(essential_conv.missing)
    missing = [
        dict(
            currency=cur,
            amount=sum(
                balance(db, a, today, movements) for a in accounts if a.currency == cur
            )
            / 100,
        )
        for cur in sorted(missing_currencies)
    ]
    return dict(
        accounts=[account_out(db, a, today) for a in accounts],
        goals=[goal_out(db, g, today, by_goal) for g in goals],
        by_currency=dict(totals),
        savings=savings / 100,
        assets=assets / 100,
        card_debt=debt / 100,
        investments=invested_total / 100,
        net_worth=(assets + invested_total - debt) / 100,
        available_cash=liquidity / 100,
        month_contribution=series[-1]["contribution"],
        series=series,
        essential_monthly=essential / 100,
        emergency_months=round(emergency / essential, 1)
        if essential > 0 and not missing
        else None,
        fx_missing=missing,
        as_of=today.isoformat(),
        movements=[
            dict(
                id=m.id,
                source_id=m.source_id,
                target_id=m.target_id,
                source_amount=m.source_cents / 100,
                target_amount=m.target_cents / 100,
                date=m.date.isoformat(),
                kind=m.kind,
                note=m.note,
                user_id=m.user_id,
            )
            for m in reversed(movements[-100:])
        ],
    )
