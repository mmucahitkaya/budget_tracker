"""Categories, users, recurring payments and budgets."""
import threading
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, finance, prefs, scheduler, services, telegram
from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..fx import Converter
from ..models import AppSetting, AuditLog, Budget, Category, RecurringPayment, User
from ..schemas import BudgetCopyIn, BudgetIn, CategoryIn, RecurringIn

router = APIRouter(prefix="/api", tags=["misc"])


def cat_out(c: Category) -> dict:
    return {"id": c.id, "name": c.name, "kind": c.kind, "icon": c.icon, "color": c.color, "archived": c.archived, "essential": c.essential}


@router.get("/me")
def me(db: Session = Depends(get_db), user: User = Depends(current_user)):
    users = db.scalars(select(User).order_by(User.id)).all()
    return {
        "me": {"id": user.id, "name": user.name},
        "users": [{"id": u.id, "name": u.name} for u in users],
        "ai_enabled": settings.ai_enabled,
        "notify_enabled": bool(settings.notify_services and settings.supervisor_token),
        "telegram": {
            "enabled": telegram.enabled(),
            "bot": telegram.bot_username(),
            "linked": user.telegram_chat_id is not None,
            "linked_users": [u.name for u in users if u.telegram_chat_id is not None],
        },
    }


@router.post("/telegram/link-code")
def telegram_link_code(user: User = Depends(current_user)):
    if not telegram.enabled():
        raise HTTPException(400, "Telegram bot token is not configured")
    return {"code": telegram.new_link_code(user.id), "bot": telegram.bot_username()}


@router.post("/telegram/unlink")
def telegram_unlink(db: Session = Depends(get_db), user: User = Depends(current_user)):
    user.telegram_chat_id = None
    db.commit()
    return {"ok": True}


# ---- Categories -----------------------------------------------------------


@router.get("/categories")
def list_categories(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [cat_out(c) for c in db.scalars(select(Category).order_by(Category.kind, Category.sort, Category.id))]


PROTECTED_CATEGORIES = {"Other Expense", "Other Income"}


def _same_name(db: Session, name: str, kind: str, exclude: int | None = None) -> Category | None:
    key = name.strip().casefold()
    return next((c for c in db.scalars(select(Category).where(Category.kind == kind))
                 if c.name.strip().casefold() == key and c.id != exclude), None)


@router.post("/categories")
def create_category(body: CategoryIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Category name is required")
    existing = _same_name(db, name, body.kind)
    if existing:
        # If a category with the same name exists, use it (make it visible again if hidden)
        existing.archived = False
        db.commit()
        return cat_out(existing)
    c = Category(name=name, kind=body.kind, icon=body.icon, color=body.color, sort=999, essential=body.essential)
    db.add(c)
    db.commit()
    return cat_out(c)


@router.get("/categories/{cat_id}/usage")
def category_usage(cat_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Before deleting: how many transactions, recurring items, installments and budgets use this category."""
    from sqlalchemy import func

    from ..models import Budget, InstallmentPlan, Transaction

    def count(model):
        return db.scalar(select(func.count()).select_from(model).where(model.category_id == cat_id)) or 0

    return {"transactions": count(Transaction), "recurring": count(RecurringPayment),
            "installments": count(InstallmentPlan), "budgets": count(Budget)}


@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, move_to: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Deletes the category; transactions, recurring items and installments move to move_to (default: "Other").

    Transactions moved to "Other" return to the Categorize screen. This category's budgets and merchant rules are deleted.
    """
    from ..models import Budget, InstallmentPlan, MerchantRule, Transaction

    c = db.get(Category, cat_id)
    if not c:
        raise HTTPException(404)
    if c.name in PROTECTED_CATEGORIES:
        raise HTTPException(400, f"'{c.name}' can't be deleted")
    other_name = "Other Expense" if c.kind == "expense" else "Other Income"
    target = db.get(Category, move_to) if move_to else db.scalar(select(Category).where(Category.name == other_name, Category.kind == c.kind))
    if target is None or target.id == c.id or target.kind != c.kind:
        raise HTTPException(400, "Invalid target category")
    to_other = target.name in PROTECTED_CATEGORIES
    moved = 0
    for t in db.scalars(select(Transaction).where(Transaction.category_id == c.id)):
        before = audit.snapshot(t)
        t.category_id = target.id
        if to_other:
            t.category_confirmed = False  # show up again under Categorize
        audit.record(db, user.id, "transaction", t, "update", before)
        moved += 1
    for model in (RecurringPayment, InstallmentPlan):
        for row in db.scalars(select(model).where(model.category_id == c.id)):
            row.category_id = target.id
    from sqlalchemy import delete as sql_delete

    for model in (Budget, MerchantRule):
        db.execute(sql_delete(model).where(model.category_id == c.id))
    db.delete(c)
    db.commit()
    return {"ok": True, "moved": moved, "target": cat_out(target)}


@router.put("/categories/{cat_id}")
def update_category(cat_id: int, body: CategoryIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    c = db.get(Category, cat_id)
    if not c:
        raise HTTPException(404)
    if _same_name(db, body.name, body.kind, exclude=c.id):
        raise HTTPException(400, "A category with this name already exists")
    c.name, c.kind, c.icon, c.color, c.archived = body.name.strip(), body.kind, body.icon, body.color, body.archived
    c.essential = body.essential
    db.commit()
    return cat_out(c)


# ---- Recurring payments ----------------------------------------------------------


def _asset_label(r: RecurringPayment) -> str | None:
    """For items paid in gold, e.g. "2 Quarter gold"."""
    if not (r.asset_code and r.asset_qty):
        return None
    from .. import prices

    kind, code = prices.split_asset_code(r.asset_code)
    item = prices.catalog_item(kind, code)
    return f"{r.asset_qty:g} {item['name'] if item else code}"


def rec_out(r: RecurringPayment) -> dict:
    left = (
        finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, r.end_date)
        if r.end_date and r.active
        else []
    )
    left = [d for d in left if finance.recurring_amount(r, d)]
    remaining = len(left)
    upcoming = finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, r.next_date + timedelta(days=400)) if r.active else []
    # Months without a payment are skipped: the "next" shown is the first actual payment
    next_date = next((d for d in upcoming if finance.recurring_amount(r, d)), r.next_date)
    return {
        "id": r.id,
        "name": r.name,
        "kind": r.kind,
        "amount": finance.from_cents(r.amount_cents),
        "currency": r.currency,
        "frequency": r.frequency,
        "next_date": next_date.isoformat(),
        "category_id": r.category_id,
        "payment_method": r.payment_method,
        "card_id": r.card_id,
        "user_id": r.user_id,
        "auto_create": r.auto_create,
        "active": r.active,
        "note": r.note,
        "end_date": r.end_date.isoformat() if r.end_date else None,
        "last_business_day": r.day == finance.LAST_BUSINESS_DAY,
        "asset_label": _asset_label(r),
        "raise_months": [int(m) for m in (r.raise_months or "").split(",") if m],
        # For payments with an end date (loans etc.): remaining count and total
        "remaining_count": remaining if r.end_date else None,
        "remaining_total": finance.from_cents(sum(finance.recurring_amount(r, d) for d in left)) if r.end_date else None,
        "next_amount": finance.from_cents(finance.recurring_amount(r, next_date)),
        # Amounts that vary by month (only if there is a plan): upcoming payments
        "schedule": [
            {"date": d.isoformat(), "amount": finance.from_cents(finance.recurring_amount(r, d))} for d in upcoming
            if finance.recurring_amount(r, d)
        ] if r.amount_plan else [],
    }


def _apply_rec(r: RecurringPayment, body: RecurringIn) -> None:
    r.name = body.name.strip()
    r.kind = body.kind
    r.amount_cents = finance.to_cents(body.amount)
    r.currency = body.currency
    r.frequency = body.frequency
    r.next_date = body.next_date
    r.day = body.next_date.day
    if body.last_business_day and body.frequency == "monthly":
        r.day = finance.LAST_BUSINESS_DAY
        r.next_date = finance.last_business_day(body.next_date.year, body.next_date.month)
    r.raise_months = ",".join(str(m) for m in sorted(set(body.raise_months)))
    r.category_id = body.category_id
    r.payment_method = body.payment_method
    r.card_id = body.card_id if body.payment_method == "card" else None
    r.user_id = body.user_id
    r.auto_create = body.auto_create
    r.active = body.active
    r.note = body.note
    r.end_date = body.end_date
    if r.end_date and r.end_date < r.next_date:
        raise HTTPException(400, "End date can't be before the first payment date")


def _after_save(db: Session, r: RecurringPayment, backfill: bool) -> None:
    if backfill and r.next_date <= date.today():
        # Immediately add all past-dated payments as transactions
        services.materialize_recurring(db, r, date.today())
        db.commit()
    else:
        db.commit()
        threading.Thread(target=scheduler.job_recurring, daemon=True).start()


def planned_for_month(db: Session, month: str) -> list[dict]:
    """The month's recurring payments not yet turned into transactions (upcoming)."""
    start, end = finance.month_range(month)
    today = date.today()
    out = []
    for r in db.scalars(select(RecurringPayment).where(RecurringPayment.active.is_(True))):
        for d in finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, end):
            if d >= start and d >= today and finance.recurring_amount(r, d):
                out.append(
                    {
                        "recurring_id": r.id,
                        "name": r.name,
                        "date": d.isoformat(),
                        "kind": r.kind,
                        "amount": finance.from_cents(finance.recurring_amount(r, d)),
                        "currency": r.currency,
                        "category_id": r.category_id,
                        "payment_method": r.payment_method,
                        "card_id": r.card_id,
                        "user_id": r.user_id,
                    }
                )
    out.sort(key=lambda x: x["date"])
    return out


@router.get("/recurring/planned")
def planned(month: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return planned_for_month(db, month)


@router.get("/recurring")
def list_recurring(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.scalars(select(RecurringPayment).order_by(RecurringPayment.active.desc(), RecurringPayment.next_date))
    return [rec_out(r) for r in rows]


@router.post("/recurring")
def create_recurring(body: RecurringIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = RecurringPayment()
    _apply_rec(r, body)
    db.add(r)
    db.flush()
    audit.record(db, user.id, "recurring", r, "create")
    _after_save(db, r, body.backfill)
    return rec_out(r)


@router.put("/recurring/{rec_id}")
def update_recurring(rec_id: int, body: RecurringIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = db.get(RecurringPayment, rec_id)
    if not r:
        raise HTTPException(404)
    before = audit.snapshot(r)
    _apply_rec(r, body)
    audit.record(db, user.id, "recurring", r, "update", before)
    _after_save(db, r, body.backfill)
    return rec_out(r)


@router.delete("/recurring/{rec_id}")
def delete_recurring(rec_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = db.get(RecurringPayment, rec_id)
    if not r:
        raise HTTPException(404)
    audit.record(db, user.id, "recurring", r, "delete", audit.snapshot(r))
    db.delete(r)
    db.commit()
    return {"ok": True}


# ---- Budgets -----------------------------------------------------------------


@router.get("/budgets")
def list_budgets(month: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    month = month or date.today().strftime("%Y-%m")
    start, end = finance.month_range(month)
    out = []
    conv = Converter(db)
    for b in db.scalars(select(Budget).where(Budget.month == month)):
        spent = finance.category_spend_base(db, b.category_id, start, end, conv)
        out.append(
            {
                "id": b.id,
                "category_id": b.category_id,
                "month": b.month,
                "limit": finance.from_cents(b.limit_cents),
                "spent": finance.from_cents(spent),
                "ratio": round(spent / b.limit_cents, 4) if b.limit_cents else 0,
            }
        )
    out.sort(key=lambda x: -x["ratio"])
    return out


@router.put("/budgets")
def upsert_budget(body: BudgetIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Changes only the selected month's limit; past and other months are unaffected."""
    month = body.month or date.today().strftime("%Y-%m")
    b = db.scalar(select(Budget).where(Budget.category_id == body.category_id, Budget.month == month))
    if body.limit <= 0:
        if b:
            audit.record(db, user.id, "budget", b, "delete", audit.snapshot(b))
            db.delete(b)
            db.commit()
        return {"ok": True}
    before = audit.snapshot(b) if b else None
    if b is None:
        b = Budget(category_id=body.category_id, month=month, limit_cents=0)
        db.add(b)
    b.limit_cents = finance.to_cents(body.limit)
    audit.record(db, user.id, "budget", b, "update" if before else "create", before)
    db.commit()
    return {"ok": True}


@router.post("/budgets/copy")
def copy_budgets(body: BudgetCopyIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    n = finance.copy_budgets(db, body.from_month, body.to_month, only_if_empty=False)
    db.commit()
    return {"copied": n}


# ---- Change history -------------------------------------------------------------


@router.get("/audit")
def audit_history(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return audit.history(db, min(limit, 300))


@router.post("/audit/{entry_id}/undo")
def audit_undo(entry_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    e = db.get(AuditLog, entry_id)
    if not e:
        raise HTTPException(404)
    try:
        summary = audit.undo(db, e, user.id)
    except audit.UndoError as err:
        raise HTTPException(409, str(err))
    db.commit()
    return {"ok": True, "summary": summary}


# ---- Household settings ------------------------------------------------------------------

SETTINGS_DEFAULTS = {"household_payday": "1"}


class SettingsIn(BaseModel):
    household_payday: int | None = Field(None, ge=1, le=28)
    # Buffer set aside from the spendable amount each month (base currency)
    spend_buffer: float | None = Field(None, ge=0, le=10_000_000)
    # Household preferences (see prefs.py)
    setup_done: bool | None = None
    base_currency: str | None = Field(None, min_length=3, max_length=3)
    currencies: list[str] | None = Field(None, max_length=len(prefs.SUPPORTED_CURRENCIES))
    locale: str | None = None
    region: str | None = None
    investments: bool | None = None


REFRESH_RATES = True  # fetch FX rates in the background after a base/currency change (tests turn this off)
PREF_FIELDS = ("setup_done", "base_currency", "currencies", "locale", "region", "investments")


def _settings(db: Session) -> dict:
    from .. import spendable

    stored = {s.key: s.value for s in db.scalars(select(AppSetting).where(AppSetting.key == "household_payday"))}
    vals = {**SETTINGS_DEFAULTS, **stored}
    return {
        "household_payday": int(vals["household_payday"]),
        "spend_buffer": spendable.buffer_cents(db) / 100,
        **prefs.get(db),
        "supported_currencies": list(prefs.SUPPORTED_CURRENCIES),
        "locales": list(prefs.LOCALES),
        "regions": [{"code": code, "name": name} for code, name in prefs.REGIONS.items()],
    }


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _settings(db)


@router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Household settings: report period start day (1 = calendar month), spendable buffer and preferences
    (base currency, enabled currencies, locale, region pack, investments). Fields not sent stay unchanged."""
    from .. import spendable

    changes = {k: getattr(body, k) for k in PREF_FIELDS if getattr(body, k) is not None}
    if body.base_currency is not None:
        changes["base_currency"] = body.base_currency.upper()
    if body.currencies is not None:
        changes["currencies"] = [c.upper() for c in body.currencies]
    old_base = prefs.base()
    if changes:
        try:
            prefs.save(db, **changes)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if body.household_payday is not None:
        db.merge(AppSetting(key="household_payday", value=str(body.household_payday)))
    if body.spend_buffer is not None:
        db.merge(AppSetting(key=spendable.BUFFER_KEY, value=str(finance.to_cents(body.spend_buffer))))
    db.commit()
    if REFRESH_RATES and changes and (prefs.base() != old_base or "currencies" in changes):
        # New base / currencies: fetch rates in the background (stored rates were cleared on a base change)
        threading.Thread(target=_refresh_rates, daemon=True).start()
    return _settings(db)


def _refresh_rates() -> None:
    from ..db import SessionLocal
    from ..fx import fetch_rates

    db = SessionLocal()
    try:
        fetch_rates(db)
    except Exception:  # network etc.; the daily job retries
        pass
    finally:
        db.close()


@router.get("/fx")
def fx_today(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Today's rate of each enabled currency in base-currency units (1 unit = ? base)."""
    conv = Converter(db)
    today = date.today()
    base = prefs.base()
    return {cur: conv.to_base_cents(100, cur, today) / 100 for cur in prefs.currencies() if cur != base}
