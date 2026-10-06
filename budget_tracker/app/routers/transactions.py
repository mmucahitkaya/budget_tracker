import csv
import io
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, finance, services
from ..auth import current_user
from ..db import get_db
from ..models import Category, CreditCard, InstallmentPlan, Transaction, User
from ..schemas import TransactionIn

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def tx_out(t: Transaction) -> dict:
    plan = t.installment_plan
    return {
        "id": t.id,
        "kind": t.kind,
        "spending_type": t.spending_type,
        "amount": finance.from_cents(t.amount_cents),
        "currency": t.currency,
        "date": t.date.isoformat(),
        "category_id": t.category_id,
        "user_id": t.user_id,
        "payment_method": t.payment_method,
        "card_id": t.card_id,
        "merchant": t.merchant,
        "note": t.note,
        "user_note": user_note(t.note),
        "items": t.items,
        "document_id": t.document_id,
        "recurring_id": t.recurring_id,
        "time": t.occurred_at.strftime("%H:%M") if t.occurred_at else None,
        "installment_count": plan.count if plan else None,
        "installment_no": t.installment_no,
        "installment_monthly": finance.from_cents(plan.monthly_cents) if plan else None,
    }


def _filtered(db: Session, month: str | None, kind, category_id, card_id, user_id, currency, q):
    stmt = select(Transaction)
    if month:
        start, end = finance.month_range(month)
        stmt = stmt.where(Transaction.date >= start, Transaction.date <= end)
    if kind:
        stmt = stmt.where(Transaction.kind == kind)
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id.is_(None) if category_id == 0 else Transaction.category_id == category_id)
    if card_id:
        stmt = stmt.where(Transaction.card_id == card_id)
    if user_id:
        stmt = stmt.where(Transaction.user_id == user_id)
    if currency:
        stmt = stmt.where(Transaction.currency == currency)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Transaction.merchant.ilike(like), Transaction.note.ilike(like)))
    return stmt.order_by(Transaction.date.desc(), Transaction.id.desc())


@router.get("")
def list_transactions(
    month: str | None = None,
    kind: str | None = None,
    category_id: int | None = None,
    card_id: int | None = None,
    user_id: int | None = None,
    currency: str | None = None,
    q: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
    start: date | None = None,
    end: date | None = None,
    merchant_key: str | None = None,
    spending_type: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    stmt = _filtered(db, month if not start and not end else None, kind, category_id, card_id, user_id, currency, q)
    if start: stmt = stmt.where(Transaction.date >= start)
    if end: stmt = stmt.where(Transaction.date <= end)
    if start and end and (start > end or (end-start).days > 1096):
        raise HTTPException(422, "Invalid date range")
    if spending_type:
        stmt = stmt.where(or_(Transaction.recurring_id.is_not(None), Transaction.spending_type == "fixed") if spending_type == "fixed" else (Transaction.spending_type == spending_type) & Transaction.recurring_id.is_(None))
    if merchant_key:
        rows = [t for t in db.scalars(stmt).unique() if (finance.normalize_merchant(t.merchant) or "—") == merchant_key][:limit]
    else:
        rows = db.scalars(stmt.limit(limit)).unique().all()
    changes = audit.last_changes(db, "transaction", [t.id for t in rows])
    return [{**tx_out(t), "last_change": changes.get(t.id)} for t in rows]


def _csv_safe(value: str) -> str:
    """Turns cells that Excel/Sheets could execute as formulas into text (OWASP CSV Injection)."""
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + value
    return value


@router.get("/export.csv")
def export_csv(
    month: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)
):
    cats = {c.id: c.name for c in db.scalars(select(Category))}
    cards = {c.id: c.name for c in db.scalars(select(CreditCard))}
    users = {u.id: u.name for u in db.scalars(select(User))}
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM for Excel
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Date", "Type", "Amount", "Currency", "Category", "Merchant", "Note", "Payment", "Card", "Person"])
    for t in db.scalars(_filtered(db, month, None, None, None, None, None, None)).unique():
        w.writerow(
            [
                t.date.isoformat(),
                "Income" if t.kind == "income" else "Expense",
                f"{finance.from_cents(t.amount_cents):.2f}",
                t.currency,
                _csv_safe(cats.get(t.category_id, "")),
                _csv_safe(t.merchant),
                _csv_safe(t.note),
                {"cash": "Cash", "bank": "Bank", "card": "Credit card", "voucher": "Voucher"}.get(t.payment_method, ""),
                _csv_safe(cards.get(t.card_id, "")),
                _csv_safe(users.get(t.user_id, "")),
            ]
        )
    name = f"budget-{month or 'all'}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("")
def create(body: TransactionIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.client_ref:
        existing = db.scalar(select(Transaction).where(Transaction.client_ref == body.client_ref))
        if existing:
            return tx_out(existing)  # same form submitted again
    if body.payment_method == "card" and not body.card_id:
        body.payment_method = "cash" if body.kind == "expense" else "bank"
    category_id = body.category_id or finance.rule_category(db, body.merchant)
    try:
        services.validate_refs(db, body.kind, category_id, body.card_id if body.payment_method == "card" else None)
    except services.ValidationError as e:
        raise HTTPException(400, str(e))
    tx = services.create_transaction(
        db,
        kind=body.kind,
        amount_cents=finance.to_cents(body.amount),
        currency=body.currency,
        on=body.date,
        category_id=category_id,
        user_id=body.user_id or user.id,
        payment_method=body.payment_method,
        card_id=body.card_id,
        merchant=body.merchant.strip(),
        note=body.note.strip(),
        installment_count=body.installment_count,
    )
    tx.client_ref = body.client_ref
    tx.spending_type = body.spending_type
    try:
        audit.record(db, user.id, "transaction", tx, "create")
        db.commit()
    except IntegrityError:
        # Two concurrent requests: the first one saved
        db.rollback()
        existing = db.scalar(select(Transaction).where(Transaction.client_ref == body.client_ref)) if body.client_ref else None
        if existing is None:
            raise HTTPException(409, "Couldn't save the transaction; check the related records")
        return tx_out(existing)
    services.after_transaction_saved(db, tx)
    return tx_out(db.get(Transaction, tx.id))


@router.put("/{tx_id}")
def update(tx_id: int, body: TransactionIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(404)
    before = audit.snapshot(tx)
    try:
        services.validate_refs(db, body.kind, body.category_id, body.card_id if body.payment_method == "card" else None)
    except services.ValidationError as e:
        raise HTTPException(400, str(e))
    old_plan = tx.installment_plan
    tx.kind = body.kind
    tx.spending_type = body.spending_type
    tx.amount_cents = finance.to_cents(body.amount)
    tx.currency = body.currency
    tx.date = body.date
    tx.category_id = body.category_id
    if body.user_id:
        tx.user_id = body.user_id
    tx.payment_method = body.payment_method if (body.payment_method != "card" or body.card_id) else "cash"
    tx.card_id = body.card_id if tx.payment_method == "card" else None
    tx.merchant = body.merchant.strip()
    tx.note = body.note.strip()
    if tx.category_id != before.get("category_id"):
        tx.category_confirmed = True  # changed manually
    new_count = body.installment_count if body.installment_count and body.installment_count > 1 else None
    if old_plan:
        # Installment series: this month's amount, category, person and merchant apply to all months (installment count stays)
        tx.payment_method, tx.card_id = "card", tx.card_id or old_plan.card_id
        old_plan.monthly_cents, old_plan.currency = tx.amount_cents, tx.currency
        old_plan.description = tx.merchant or old_plan.description
        old_plan.category_id, old_plan.user_id, old_plan.card_id = tx.category_id, tx.user_id, tx.card_id
        for t in db.scalars(select(Transaction).where(Transaction.installment_plan_id == old_plan.id, Transaction.id != tx.id)):
            t.amount_cents, t.currency, t.category_id, t.user_id = tx.amount_cents, tx.currency, tx.category_id, tx.user_id
            t.merchant, t.card_id, t.category_confirmed = old_plan.description, tx.card_id, tx.category_confirmed
            t.note = tx.note  # the what-was-bought note on all months
    elif new_count and tx.card_id and tx.kind == "expense":
        # Converted to installments: the entered amount is the full purchase; each month gets that month's installment
        card = db.get(CreditCard, tx.card_id)
        plan = InstallmentPlan(
            card_id=tx.card_id,
            description=tx.merchant or tx.note or "Installment purchase",
            monthly_cents=round(tx.amount_cents / new_count),
            currency=tx.currency,
            count=new_count,
            first_month=finance.statement_month_for(card, tx.date),
            category_id=tx.category_id,
            user_id=tx.user_id,
        )
        db.add(plan)
        db.flush()
        tx.installment_plan_id = plan.id
        services.sync_installments(db, plan, base=tx)
    if tx.merchant and tx.category_id:
        finance.learn_rule(db, tx.merchant, tx.category_id)
    db.flush()
    db.refresh(tx)
    audit.record(db, user.id, "transaction", tx, "update", before)
    db.commit()
    services.after_transaction_saved(db, tx)
    db.expire_all()
    return tx_out(db.get(Transaction, tx_id))


@router.delete("/{tx_id}")
def delete(tx_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(404)
    audit.record(db, user.id, "transaction", tx, "delete", audit.snapshot(tx))
    plan = tx.installment_plan
    if plan:
        services.delete_installment_series(db, plan)  # installments: all months are deleted together
    else:
        db.delete(tx)
    db.commit()
    return {"ok": True}


@router.get("/suggest-category")
def suggest_category(merchant: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return {"category_id": finance.rule_category(db, merchant)}


@router.get("/merchants")
def merchants(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Recently used merchants for quick entry."""
    since = date.today().replace(day=1)
    since = finance.add_months(since, -6)
    rows = db.execute(
        select(Transaction.merchant, Transaction.category_id)
        .where(Transaction.merchant != "", Transaction.date >= since)
        .order_by(Transaction.date.desc())
        .limit(300)
    ).all()
    seen: dict[str, int | None] = {}
    for m, c in rows:
        seen.setdefault(m, c)
    return [{"merchant": m, "category_id": c} for m, c in list(seen.items())[:50]]


# ---- Quick categorize ------------------------------------------------------------------

OTHER_CATEGORY = "Other Expense"
MARKETPLACE = finance.MARKETPLACE
QUICK_CATEGORIES = ["Groceries", "Restaurants & Cafes", "Hobbies", "Clothing", "Home & Living", "Electronics", "Transport & Fuel",
                    "Health", "Subscriptions", "Personal Care", "Travel", "Entertainment"]


def _pending_query(db: Session):
    other = db.scalar(select(Category.id).where(Category.name == OTHER_CATEGORY, Category.kind == "expense"))
    q = select(Transaction).where(
        Transaction.kind == "expense",
        Transaction.recurring_id.is_(None),
        Transaction.category_confirmed.is_(False),
        Transaction.date <= date.today(),  # future installments are categorized together with the series
        or_(Transaction.category_id.is_(None), Transaction.category_id == other),
    )
    return q


def pending_count(db: Session) -> int:
    return len(db.scalars(_pending_query(db)).all())


@router.get("/categorize/pending")
def categorize_pending(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """"Other"/uncategorized expenses, grouped by merchant (one tap categorizes them all)."""
    from .. import telegram

    # Orders first: the months of an installment purchase are one order (plan), others are a single transaction
    orders: dict[str, dict] = {}
    for t in db.scalars(_pending_query(db).order_by(Transaction.date.desc())):
        okey = f"plan:{t.installment_plan_id}" if t.installment_plan_id else f"tx:{t.id}"
        # Order date: installments post on the purchase day each month → k-1 months back from installment k's date
        order_date = finance.add_months(t.date, -((t.installment_no or 1) - 1)) if t.installment_plan_id else t.date
        o = orders.setdefault(okey, {"key": okey, "tx_ids": [], "date": order_date, "amount": 0, "merchant": t.merchant or "No description",
                                     "card_id": t.card_id, "installment_count": None, "monthly": None, "current_no": None,
                                     "note": ""})
        o["note"] = o["note"] or user_note(t.note)
        o["tx_ids"].append(t.id)
        o["amount"] += t.amount_cents
        # Count back from the smallest installment (so days like the 31st don't drift in short months)
        if (t.installment_no or 1) <= o.setdefault("_min_no", t.installment_no or 1):
            o["_min_no"], o["date"] = t.installment_no or 1, order_date
        if t.installment_no:
            o["current_no"] = max(o["current_no"] or 0, t.installment_no)  # latest installment posted so far
        if t.installment_plan_id:
            plan = t.installment_plan
            o["installment_count"], o["monthly"] = plan.count, finance.from_cents(plan.monthly_cents)
            o["full"] = plan.monthly_cents * plan.count  # total amount of the order
    # Then group by merchant; on marketplaces each order is a separate card (each order may be something different)
    groups: dict[str, dict] = {}
    for o in orders.values():
        o["amount"] = o.pop("full", o["amount"])
        o.pop("_min_no", None)
        mkey = finance.normalize_merchant(o["merchant"]) or o["merchant"].strip().lower() or o["key"]
        per_order = bool(MARKETPLACE.search(o["merchant"]))
        key = f"{mkey}|{o['key']}" if per_order else mkey
        g = groups.setdefault(key, {"key": key, "merchant": o["merchant"], "tx_ids": [], "total": 0, "first": o["date"],
                                    "last": o["date"], "cards": set(), "orders": [], "per_order": per_order})
        g["tx_ids"].extend(o["tx_ids"])
        g["total"] += o["amount"]
        g["first"], g["last"] = min(g["first"], o["date"]), max(g["last"], o["date"])
        if o["card_id"]:
            g["cards"].add(o["card_id"])
        g["orders"].append({**o, "date": o["date"].isoformat(), "amount": finance.from_cents(o["amount"])})
    out = []
    for g in groups.values():
        # No merchant rule is suggested for marketplaces (you can buy groceries as well as electronics there)
        suggestion = None if g["per_order"] else (finance.rule_category(db, g["merchant"]) or telegram.keyword_category(db, g["merchant"]))
        g["orders"].sort(key=lambda o: o["date"], reverse=True)
        out.append({**g, "total": finance.from_cents(g["total"]), "first": g["first"].isoformat(), "last": g["last"].isoformat(),
                    "cards": sorted(g["cards"]), "count": len(g["tx_ids"]), "suggestion": suggestion})
    out.sort(key=lambda g: -g["total"])
    # Frequently used expense categories (button order)
    from sqlalchemy import func

    usage = dict(db.execute(select(Transaction.category_id, func.count()).where(Transaction.kind == "expense")
                            .group_by(Transaction.category_id)).all())
    cats = db.scalars(select(Category).where(Category.kind == "expense", Category.archived.is_(False))).all()
    # Fixed order (for muscle memory): common variable spending categories, then the rest by usage
    pinned = [n for n in QUICK_CATEGORIES]
    by_name = {c.name: c.id for c in cats}
    favorites = [by_name[n] for n in pinned if n in by_name]
    favorites += [c.id for c in sorted(cats, key=lambda c: (-usage.get(c.id, 0), c.sort))
                  if c.name != OTHER_CATEGORY and c.id not in favorites]
    return {"groups": out, "count": sum(g["count"] for g in out), "favorites": favorites}


class CategorizeIn(BaseModel):
    tx_ids: list[int] = Field(min_length=1, max_length=500)
    category_id: int | None = None
    learn: bool = True  # learn as a merchant rule (automatic on future statements)
    confirm: bool = True  # False: undo (returns to pending categorization)
    note: str | None = Field(None, max_length=200)  # what was bought (e.g. "Headphones"); None = leave unchanged


# Notes written by the system don't count as user notes (Turkish forms kept for existing data)
SYSTEM_NOTE = re.compile(r"^(Telegram( · (nakit|cash))?|Sabit ödeme.*|Recurring payment.*|Taksit \d+/\d+|Installment \d+/\d+)$")


def user_note(note: str | None) -> str:
    return "" if not note or SYSTEM_NOTE.match(note) else note


@router.post("/categorize")
def categorize(body: CategorizeIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.category_id is not None:
        try:
            services.validate_refs(db, "expense", body.category_id, None)
        except services.ValidationError as e:
            raise HTTPException(400, str(e))
    changed = 0
    for t in db.scalars(select(Transaction).where(Transaction.id.in_(body.tx_ids), Transaction.kind == "expense")):
        before = audit.snapshot(t)
        t.category_id = body.category_id
        t.category_confirmed = body.confirm
        if body.note is not None:
            t.note = body.note.strip()
        audit.record(db, user.id, "transaction", t, "update", before)
        if body.learn and body.confirm and body.category_id:
            finance.learn_rule(db, t.merchant, body.category_id)
        if t.installment_plan_id:
            # All months of the installment series together
            plan = db.get(InstallmentPlan, t.installment_plan_id)
            plan.category_id = body.category_id
            for o in db.scalars(select(Transaction).where(Transaction.installment_plan_id == plan.id, Transaction.id != t.id)):
                o.category_id, o.category_confirmed = body.category_id, body.confirm
                if body.note is not None:
                    o.note = body.note.strip()
        changed += 1
    db.commit()
    return {"changed": changed, "pending": pending_count(db)}


class IdsIn(BaseModel):
    ids: list[int] = Field(max_length=2000)


@router.post("/by-ids")
def by_ids(body: IdsIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Item-by-item review from the insights screen: the given transactions (newest first)."""
    if not body.ids:
        return []
    rows = db.scalars(select(Transaction).where(Transaction.id.in_(body.ids))
                      .order_by(Transaction.date.desc(), Transaction.id.desc())).unique().all()
    return [tx_out(t) for t in rows]
