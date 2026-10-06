from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, finance, prefs, services
from ..auth import current_user
from ..db import get_db
from ..fx import Converter
from ..models import CardPayment, CardStatement, CreditCard, InstallmentPlan, Transaction, User
from ..schemas import CardIn, InstallmentIn, PaymentIn, StatementIn

router = APIRouter(prefix="/api/cards", tags=["cards"])


def amounts_out(cents: dict[str, int] | None, keep_zero: bool = False) -> dict[str, float]:
    """{currency: cents} -> {currency: amount}, base currency first, zero entries dropped unless keep_zero."""
    base = prefs.base()
    items = sorted(((c, v) for c, v in (cents or {}).items() if v or keep_zero), key=lambda x: (x[0] != base, x[0]))
    return {c: finance.from_cents(v) for c, v in items}


def statement_out(s: CardStatement, db: Session | None = None) -> dict:
    paid = services.statement_paid(db, s) if db else {}
    remaining = services.statement_remaining(db, s) if db else dict(s.totals or {})
    return {
        "id": s.id,
        "card_id": s.card_id,
        "period_end": s.period_end.isoformat(),
        "due_date": s.due_date.isoformat(),
        "currency": s.currency or prefs.base(),
        "totals": amounts_out(s.totals),
        "min_payment": finance.from_cents(s.min_payment_cents),
        "paid": amounts_out(paid),  # paid so far per currency
        "remaining": amounts_out(remaining, keep_zero=True),
        "is_paid": s.paid,
        "document_id": s.document_id,
    }


def payment_out(p: CardPayment) -> dict:
    return {
        "id": p.id,
        "card_id": p.card_id,
        "statement_id": p.statement_id,
        "amount": finance.from_cents(p.amount_cents),
        "currency": p.currency,
        "date": p.date.isoformat(),
        "user_id": p.user_id,
        "note": p.note,
    }


def plan_out(p: InstallmentPlan, today: date) -> dict:
    remaining = finance.remaining_installments(p, today)
    return {
        "id": p.id,
        "card_id": p.card_id,
        "description": p.description,
        "monthly": finance.from_cents(p.monthly_cents),
        "currency": p.currency,
        "count": p.count,
        "first_month": p.first_month.strftime("%Y-%m"),
        "remaining": remaining,
        "remaining_total": finance.from_cents(p.monthly_cents * remaining),
    }


def card_out(db: Session, c: CreditCard, today: date | None = None) -> dict:
    today = today or date.today()
    cut = finance.last_statement_cut(c, today)
    period_spend: dict[str, float] = {}
    for amount, cur, kind in db.execute(
        select(Transaction.amount_cents, Transaction.currency, Transaction.kind).where(
            Transaction.card_id == c.id, Transaction.date > cut
        )
    ):
        sign = -1 if kind == "income" else 1
        period_spend[cur] = round(period_spend.get(cur, 0) + sign * amount / 100, 2)
    plans = db.scalars(select(InstallmentPlan).where(InstallmentPlan.card_id == c.id)).all()
    active_plans = [plan_out(p, today) for p in plans if finance.remaining_installments(p, today) > 0]
    statements = db.scalars(
        select(CardStatement).where(CardStatement.card_id == c.id).order_by(CardStatement.period_end.desc()).limit(12)
    ).all()
    unpaid = next((s for s in statements if not s.paid), None)
    return {
        "id": c.id,
        "name": c.name,
        "bank": c.bank,
        "last4": c.last4,
        "limit": finance.from_cents(c.limit_cents) if c.limit_cents is not None else None,
        "available": (
            finance.from_cents(c.limit_cents - services.card_debts_base_cents(Converter(db), c, today))
            if c.limit_cents is not None else None
        ),
        "statement_day": c.statement_day,
        "due_day": c.due_day,
        "owner_id": c.owner_id,
        "holder_id": c.holder_id,
        "color": c.color,
        "archived": c.archived,
        "debts": amounts_out(c.debts),
        "debt_updated_at": c.debt_updated_at.isoformat() if c.debt_updated_at else None,
        "last_cut": cut.isoformat(),
        "next_due_date": finance.next_due_date(c, today).isoformat(),
        "next_statement_date": finance.next_statement_date(c, today).isoformat(),
        "period_spend": period_spend,
        "unpaid_statement": statement_out(unpaid, db) if unpaid else None,
        "statements": [statement_out(s, db) for s in statements],
        "payments": [
            payment_out(p)
            for p in db.scalars(
                select(CardPayment).where(CardPayment.card_id == c.id).order_by(CardPayment.date.desc(), CardPayment.id.desc()).limit(20)
            )
        ],
        "installments": active_plans,
        "schedule": finance.installment_schedule(plans, today, 12),
    }


def _apply(c: CreditCard, body: CardIn) -> None:
    debts = {cur: finance.to_cents(v) for cur, v in body.debts.items() if finance.to_cents(v)}
    debt_changed = dict(c.debts or {}) != debts
    c.name = body.name.strip()
    c.bank = body.bank.strip()
    c.last4 = "".join(ch for ch in body.last4 if ch.isdigit())[-4:]
    c.limit_cents = finance.to_cents(body.limit) if body.limit is not None else None
    c.statement_day = body.statement_day
    c.due_day = body.due_day
    c.owner_id = body.owner_id
    c.holder_id = body.holder_id if body.holder_id != body.owner_id else None
    c.color = body.color
    c.archived = body.archived
    c.debts = debts
    if debt_changed:
        c.debt_updated_at = datetime.now()


@router.get("")
def list_cards(include_archived: bool = False, db: Session = Depends(get_db), _: User = Depends(current_user)):
    q = select(CreditCard).order_by(CreditCard.id)
    if not include_archived:
        q = q.where(CreditCard.archived.is_(False))
    return [card_out(db, c) for c in db.scalars(q)]


@router.post("")
def create_card(body: CardIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = CreditCard()
    _apply(c, body)
    db.add(c)
    audit.record(db, user.id, "card", c, "create")
    db.commit()
    return card_out(db, c)


@router.put("/{card_id}")
def update_card(card_id: int, body: CardIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(CreditCard, card_id)
    if not c:
        raise HTTPException(404)
    before = audit.snapshot(c)
    _apply(c, body)
    audit.record(db, user.id, "card", c, "update", before)
    db.commit()
    return card_out(db, c)


@router.delete("/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(CreditCard, card_id)
    if not c:
        raise HTTPException(404)
    # Transactions are kept; the card is archived
    before = audit.snapshot(c)
    c.archived = True
    audit.record(db, user.id, "card", c, "update", before)
    db.commit()
    return {"ok": True}


# ---- Statements ------------------------------------------------------------


@router.post("/{card_id}/statements")
def add_statement(card_id: int, body: StatementIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    c = db.get(CreditCard, card_id)
    if not c:
        raise HTTPException(404)
    s = services.apply_statement(db, c, body.model_dump(), None)
    db.commit()
    return statement_out(s, db)


@router.post("/statements/{stmt_id}/paid")
def mark_paid(stmt_id: int, paid: bool = True, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Shortcut: pay the full remaining amount (paid=true) or undo this statement's payments (paid=false)."""
    s = db.get(CardStatement, stmt_id)
    if not s:
        raise HTTPException(404)
    card = db.get(CreditCard, s.card_id)
    if paid:
        for cur, cents in services.statement_remaining(db, s).items():
            if cents > 0:
                services.add_card_payment(db, card, cents, cur, date.today(), s.id, user.id, "Paid in full")
    else:
        for p in db.scalars(select(CardPayment).where(CardPayment.statement_id == s.id)).all():
            services.delete_card_payment(db, p)
    db.commit()
    return statement_out(s, db)


@router.post("/{card_id}/payments")
def add_payment(card_id: int, body: PaymentIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    card = db.get(CreditCard, card_id)
    if not card:
        raise HTTPException(404)
    try:
        p = services.add_card_payment(
            db, card, finance.to_cents(body.amount), body.currency, body.date, body.statement_id, user.id, body.note
        )
    except services.ValidationError as e:
        raise HTTPException(400, str(e))
    audit.record(db, user.id, "payment", p, "create")
    db.commit()
    return payment_out(p)


@router.delete("/payments/{payment_id}")
def delete_payment(payment_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = db.get(CardPayment, payment_id)
    if not p:
        raise HTTPException(404)
    audit.record(db, user.id, "payment", p, "delete", audit.snapshot(p))
    services.delete_card_payment(db, p)
    db.commit()
    return {"ok": True}


@router.delete("/statements/{stmt_id}")
def delete_statement(stmt_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    s = db.get(CardStatement, stmt_id)
    if not s:
        raise HTTPException(404)
    db.delete(s)
    db.commit()
    return {"ok": True}


# ---- Installment plans -----------------------------------------------------


@router.post("/installments")
def add_installment(body: InstallmentIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    if not db.get(CreditCard, body.card_id):
        raise HTTPException(404, "Card not found")
    start, _end = finance.month_range(body.first_month)
    p = InstallmentPlan(
        card_id=body.card_id,
        description=body.description.strip(),
        monthly_cents=finance.to_cents(body.monthly),
        currency=body.currency,
        count=body.count,
        first_month=start,
    )
    db.add(p)
    db.commit()
    return plan_out(p, date.today())


@router.delete("/installments/{plan_id}")
def delete_installment(plan_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    p = db.get(InstallmentPlan, plan_id)
    if not p:
        raise HTTPException(404)
    for tx in db.scalars(select(Transaction).where(Transaction.installment_plan_id == plan_id)):
        tx.installment_plan_id = None
    db.delete(p)
    db.commit()
    return {"ok": True}
