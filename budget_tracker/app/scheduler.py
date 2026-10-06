"""Daily jobs: FX rates, recurring payments, card due date reminders."""
import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from . import analytics, finance, notify, prefs, services
from .config import BACKUP_DIR, DB_PATH, settings
from .db import SessionLocal
from .fx import backfill_missing_rates, fetch_rates
from .models import CardStatement, CreditCard, RecurringPayment

log = logging.getLogger(__name__)


def _money(amount: float, currency: str | None = None) -> str:
    """Human-readable amount for notifications, e.g. "1,234.00 USD" (defaults to the base currency)."""
    return finance.format_money(finance.to_cents(amount), currency or prefs.base())


def _money_by_currency(cents: dict[str, int], primary: str | None = None) -> str:
    """Several currencies in one line, primary/base first: "1,234.00 USD + 50.00 EUR"."""
    primary = primary or prefs.base()
    parts = [(cur, v) for cur, v in sorted(cents.items(), key=lambda x: (x[0] != primary, x[0])) if v]
    return " + ".join(finance.format_money(v, cur) for cur, v in parts) or finance.format_money(0, primary)


def job_fx() -> None:
    db = SessionLocal()
    try:
        fetch_rates(db)
        # Also fill in missing rates for past-dated foreign currency transactions
        backfill_missing_rates(db)
    finally:
        db.close()


def job_recurring(today: date | None = None) -> None:
    """Turns due recurring payments into transactions (if automatic) and advances them to the next date."""
    today = today or datetime.now(settings.tz).date()
    db = SessionLocal()
    try:
        due = db.scalars(
            select(RecurringPayment).where(RecurringPayment.active.is_(True), RecurringPayment.next_date <= today)
        ).all()
        for r in due:
            # Don't bulk-create entries for very old periods (more than 31 days ago); just advance
            dates = services.materialize_recurring(db, r, today, create_from=today - timedelta(days=31))
            for d in dates:
                notify.send_once(
                    db,
                    f"recurring:{r.id}:{d.isoformat()}",
                    "Recurring payment due" if r.kind == "expense" else "Expected income",
                    f"{r.name}: {_money(finance.from_cents(finance.recurring_amount(r, d)), r.currency)}"
                    + (" (recorded)" if r.auto_create else ""),
                )
            db.commit()

        # Remind ahead of payments due tomorrow
        tomorrow = today + timedelta(days=1)
        for r in db.scalars(
            select(RecurringPayment).where(
                RecurringPayment.active.is_(True),
                RecurringPayment.next_date == tomorrow,
                RecurringPayment.kind == "expense",
            )
        ):
            notify.send_once(
                db,
                f"recurring-pre:{r.id}:{r.next_date.isoformat()}",
                "Payment due tomorrow",
                f"{r.name}: {_money(finance.from_cents(finance.recurring_amount(r, r.next_date)), r.currency)}",
            )
    finally:
        db.close()


def job_card_reminders(today: date | None = None) -> None:
    today = today or datetime.now(settings.tz).date()
    db = SessionLocal()
    try:
        for c in db.scalars(select(CreditCard).where(CreditCard.archived.is_(False))):
            stmt = db.scalar(
                select(CardStatement)
                .where(CardStatement.card_id == c.id, CardStatement.paid.is_(False), CardStatement.due_date >= today)
                .order_by(CardStatement.due_date)
                .limit(1)
            )
            if stmt:
                # If a partial payment was made, remind about the remaining amount
                primary = stmt.currency or prefs.base()
                remaining = {cur: v for cur, v in services.statement_remaining(db, stmt).items() if v > 0}
                paid = (stmt.totals or {}).get(primary, 0) - remaining.get(primary, 0)
                due, amount, min_pay = stmt.due_date, remaining, max(0, stmt.min_payment_cents - paid)
                if not amount:
                    continue
            elif any(v > 0 for v in (c.debts or {}).values()):
                primary = prefs.base()
                due, amount, min_pay = finance.next_due_date(c, today), {k: v for k, v in c.debts.items() if v > 0}, 0
            else:
                continue
            days_left = (due - today).days
            if days_left not in (3, 1, 0):
                continue
            when = {3: "in 3 days", 1: "tomorrow", 0: "today"}[days_left]
            msg = f"{c.name} payment due {when}: {_money_by_currency(amount, primary)}"
            if min_pay:
                msg += f" (minimum {_money(finance.from_cents(min_pay), primary)})"
            notify.send_once(db, f"card-due:{c.id}:{due.isoformat()}:{days_left}", "Credit card payment", msg)
    finally:
        db.close()


def _tell(db, user_id: int | None, key: str, title: str, text: str) -> None:
    """Send to the person via Telegram (once); if there is no person or they are not linked, notify everyone."""
    from . import telegram
    from .models import User

    user = db.get(User, user_id) if user_id else None
    if user and user.telegram_chat_id and telegram.enabled():
        telegram.send_once_to(db, user.telegram_chat_id, key, text)
    else:
        notify.send_once(db, key, title, text.replace("<b>", "").replace("</b>", ""))


def job_statement_reminders(today: date | None = None) -> None:
    """The day after the statement closing date, asks the card owner for the statement PDF; reminds again on day 4 if still missing."""
    today = today or datetime.now(settings.tz).date()
    with SessionLocal() as db:
        for c in db.scalars(select(CreditCard).where(CreditCard.archived.is_(False))):
            cut = finance.clamp_day(today.year, today.month, c.statement_day)
            if cut >= today:
                prev = finance.add_months(finance.month_start(today), -1)
                cut = finance.clamp_day(prev.year, prev.month, c.statement_day)
            days = (today - cut).days
            if days not in (1, 4):
                continue
            # On weekends/holidays the bank may shift the closing date by a day or two
            uploaded = db.scalar(
                select(CardStatement.id).where(
                    CardStatement.card_id == c.id,
                    CardStatement.period_end >= cut - timedelta(days=5),
                    CardStatement.period_end <= cut + timedelta(days=5),
                )
            )
            if uploaded:
                continue
            label = f"{c.name}{f' ·{c.last4}' if c.last4 else ''}"
            text = (f"📄 <b>{label}</b> statement closed on {cut:%m/%d}. Send the statement PDF to this chat; "
                    "spending, installments and card balance are processed automatically."
                    if days == 1 else
                    f"⏰ <b>{label}</b> statement (closed {cut:%m/%d}) hasn't been uploaded yet. You can send the PDF to this chat.")
            _tell(db, c.owner_id, f"stmt-ask:{c.id}:{cut.isoformat()}:{days}", "Card statement", text)


def job_weekly_spendable(today: date | None = None) -> None:
    """Monday morning: what is left to spend this month and the most you can spend next month."""
    from . import spendable

    today = today or datetime.now(settings.tz).date()
    if today.weekday() != 0:
        return
    with SessionLocal() as db:
        notify.send_once(db, f"spendable:{today:%G-W%V}", "Weekly spendable", spendable.summary_text(db, today))


def notify_statement_committed(card_id: int, statement_id: int) -> None:
    """After a statement is processed: card payment and current spendable amount (in the background, after the transaction is committed)."""
    from . import spendable

    with SessionLocal() as db:
        card, st = db.get(CreditCard, card_id), db.get(CardStatement, statement_id)
        if not card or not st:
            return
        rem = services.statement_remaining(db, st)
        from .routers.transactions import pending_count

        pending = pending_count(db)
        text = (f"💳 <b>{card.name}</b> statement processed: {_money_by_currency(rem, st.currency)}, "
                f"due {st.due_date:%m/%d}.\n\n" + spendable.summary_text(db))
        if pending:
            text += (f"\n\n🏷 {pending} expenses are still in 'Other'. Fix them with one tap in the app under More → Categorize; "
                     "merchants are learned and filled in automatically on future statements.")
        notify.send_once(db, f"stmt-done:{statement_id}:{sum((st.totals or {}).values())}", "Statement processed", text)


def job_monthly_summary(today: date | None = None) -> None:
    """On the 1st of each month, sends last month's summary and carries budget limits over to the new month."""
    today = today or datetime.now(settings.tz).date()
    if today.day != 1:
        return
    ym = finance.add_months(today, -1).strftime("%Y-%m")
    db = SessionLocal()
    try:
        # If the new month has no budgets, last month's are copied (the user can change them later)
        if finance.copy_budgets(db, ym, today.strftime("%Y-%m")):
            db.commit()
        s = analytics.monthly_summary(db, ym)
        if not s["expense"] and not s["income"]:
            return
        lines = [f"Expenses {_money(finance.from_cents(s['expense']))}"]
        if s["change"] is not None:
            arrow = "▲" if s["change"] > 0 else "▼"
            lines[0] += f" ({arrow} {abs(s['change']) * 100:.0f}%)"
        if s["income"]:
            lines.append(
                f"Income {_money(finance.from_cents(s['income']))} · Net {_money(finance.from_cents(s['net']))}"
            )
        if s["top"]:
            lines.append("Top: " + ", ".join(f"{n} {_money(finance.from_cents(v))}" for n, v in s["top"]))
        if s["over_budget"]:
            lines.append("Over budget: " + ", ".join(o["category"] for o in s["over_budget"]))
        month_name = MONTHS[int(ym[5:]) - 1]
        notify.send_once(db, f"monthly:{ym}", f"{month_name} summary", "\n".join(lines))
    finally:
        db.close()


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


SNAPSHOT_KEEP = 14


def job_db_snapshot(today: date | None = None) -> None:
    """Consistent daily copy of the database (/data/backups). HA backups (and Drive) include this folder too."""
    import sqlite3

    today = today or datetime.now(settings.tz).date()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"budget-{today.isoformat()}.db"
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(BACKUP_DIR.glob("budget-*.db"))[:-SNAPSHOT_KEEP]:
        old.unlink()
    log.info("Database snapshot: %s", target.name)


def job_savings_reminders(today: date | None = None) -> None:
    from .models import SavingsGoal
    from .savings import goal_out
    today = today or datetime.now(settings.tz).date()
    with SessionLocal() as db:
        for g in db.scalars(select(SavingsGoal).where(SavingsGoal.archived.is_(False))):
            if not g.reminder_day or g.monthly_cents <= 0:
                continue
            due = finance.clamp_day(today.year, today.month, g.reminder_day)
            status = goal_out(db, g, today)
            if today == due and status["remaining"] > 0 and status["month_allocated"] < g.monthly_cents / 100:
                notify.send_once(db, f"savings:{g.id}:{today:%Y-%m}", "Savings contribution plan",
                                 f"{g.name}: set aside this month {_money(status['month_allocated'], g.currency)} / "
                                 f"{_money(g.monthly_cents / 100, g.currency)}. The plan is not added to the balance automatically.")


def job_investment_prices() -> None:
    """Updates investment prices and records today's value (value history chart)."""
    from . import investments, prices

    db = SessionLocal()
    try:
        needed = investments.needed_prices(db)
        if needed:
            prices.refresh(db, needed)
        investments.snapshot(db)
    finally:
        db.close()


def job_raise_reminders(today: date | None = None) -> None:
    """On the 1st of raise months, reminds the salary owner to update the amount (Telegram; everyone if not linked)."""
    today = today or datetime.now(settings.tz).date()
    if today.day != 1:
        return
    with SessionLocal() as db:
        for r in db.scalars(select(RecurringPayment).where(RecurringPayment.active.is_(True))):
            if str(today.month) not in (r.raise_months or "").split(","):
                continue
            income = r.kind == "income"
            text = (f"{'💼' if income else '🏠'} <b>{MONTHS[today.month - 1]} {'raise' if income else 'increase'} period</b>\n{r.name}"
                    f" is currently {_money(r.amount_cents / 100, r.currency)}.\n"
                    f"{'If you got a raise' if income else 'If the amount changed'}, update it in the app under More → {'Recurring Income' if income else 'Recurring Expenses'}; past months stay unchanged.")
            _tell(db, r.user_id, f"raise:{r.id}:{today:%Y-%m}", "Raise period" if income else "Payment increase period", text)


def job_fund_reminders(today: date | None = None) -> None:
    """On the 1st of each month, reminds fund owners via Telegram to update values."""
    today = today or datetime.now(settings.tz).date()
    if today.day == 1:
        from . import telegram

        telegram.send_fund_reminders(today)


def daily() -> None:
    for job in (job_fx, job_recurring, job_card_reminders, job_monthly_summary, job_savings_reminders, job_investment_prices, job_fund_reminders, job_raise_reminders, job_statement_reminders, job_weekly_spendable):
        try:
            job()
        except Exception:
            log.exception("Daily job failed: %s", job.__name__)


def _purge_telegram() -> None:
    from . import telegram

    try:
        telegram.purge_due()
    except Exception:
        log.exception("Telegram message purge job failed")


def start() -> BackgroundScheduler:
    hour, minute = map(int, settings.notify_time.split(":"))
    sched = BackgroundScheduler(timezone=settings.tz)
    sched.add_job(daily, "cron", hour=hour, minute=minute, id="daily")
    # The central bank (TCMB) publishes rates around 15:30
    sched.add_job(job_fx, "cron", hour=16, minute=0, id="fx")
    # Gold/FX/stock prices
    sched.add_job(job_investment_prices, "interval", minutes=30, id="investments")
    # Delete expired Telegram messages
    sched.add_job(_purge_telegram, "interval", minutes=30, id="tg-purge")
    # HA's automatic backup usually runs at night; take a snapshot before it
    sched.add_job(job_db_snapshot, "cron", hour=3, minute=30, id="snapshot")
    sched.start()
    return sched
