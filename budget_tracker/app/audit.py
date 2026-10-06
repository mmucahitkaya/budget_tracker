"""Change history and undo.

Every write is recorded with who/when/before/after. Undo only happens if the record has not been changed
by someone else in the meantime (so a later edit is not overwritten).
"""
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AuditLog,
    Budget,
    CardPayment,
    CardStatement,
    CreditCard,
    Document,
    InstallmentPlan,
    RecurringPayment,
    Transaction,
    User,
)

MODELS = {
    "transaction": Transaction,
    "recurring": RecurringPayment,
    "budget": Budget,
    "card": CreditCard,
    "payment": CardPayment,
}
IGNORED = {"created_at"}


class UndoError(Exception):
    pass


def _value(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return dict(v)  # e.g. card debts {currency: cents}; a copy, not the live mutable object
    return v


def snapshot(obj) -> dict:
    data = {c.name: _value(getattr(obj, c.name)) for c in obj.__table__.columns if c.name not in IGNORED}
    if isinstance(obj, Transaction) and obj.installment_plan_id:
        plan = obj.installment_plan or None
        if plan is not None:
            data["_plan"] = snapshot(plan)
    return data


def _money(cents: int, currency: str | None = None) -> str:
    """currency None = household base currency."""
    from .finance import format_money

    return format_money(cents, currency)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def describe(entity: str, data: dict) -> str:
    if entity in ("account", "goal"):
        return data.get("name", "")
    if entity == "movement":
        return f"{data.get('date', '')} · {data.get('note') or data.get('kind', '')}"
    if entity == "allocation":
        return f"Goal #{data['goal_id']} · account #{data['account_id']} · {_money(data['amount_cents']).rsplit(' ', 1)[0]}"  # the goal's currency is not stored on the record
    if entity == "asset":
        return data.get("name", "")
    if entity == "lot":
        side = "buy" if data.get("side") == "buy" else "sell"
        return f"{side} {data.get('quantity', 0):g} x {_money(data.get('unit_price_cents', 0))}"
    if entity == "transaction":
        name = data.get("merchant") or data.get("note") or "Transaction"
        return f"{name} {_money(data['amount_cents'], data['currency'])}"
    if entity == "recurring":
        return f"{data['name']} {_money(data['amount_cents'], data['currency'])}"
    if entity == "budget":
        months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        y, m = data["month"].split("-")
        return f"{data.get('_category', 'Category')} · {months[int(m) - 1]} {y} · {_money(data['limit_cents'])}"
    if entity == "card":
        return data.get("name") or "Card"
    if entity == "payment":
        return f"card payment {_money(data['amount_cents'], data['currency'])}"
    return entity


ACTION_TR = {"create": "added", "update": "edited", "delete": "deleted"}
ENTITY_TR = {"transaction": "Transaction", "recurring": "Recurring item", "budget": "Budget", "card": "Card", "payment": "Payment"}
ENTITY_TR.update(account="Account", movement="Account movement", goal="Savings goal", allocation="Goal allocation")
ENTITY_TR.update(asset="Investment", lot="Investment trade")


def record(db: Session, user_id: int | None, entity: str, obj, action: str, before: dict | None = None) -> AuditLog:
    """Does not commit; it goes into the same commit as the caller's transaction."""
    db.flush()
    after = None if action == "delete" else snapshot(obj)
    data = dict(after or before or {})
    if entity == "budget":
        from .models import Category

        cat = db.get(Category, data.get("category_id"))
        data["_category"] = cat.name if cat else "Category"
    entry = AuditLog(
        user_id=user_id,
        entity=entity,
        entity_id=data.get("id") or getattr(obj, "id", 0),
        action=action,
        before=before,
        after=after,
        summary=f"{ENTITY_TR.get(entity, entity)} {ACTION_TR.get(action, action)}: {describe(entity, data)}",
    )
    db.add(entry)
    return entry


def record_document_commit(
    db: Session,
    user_id: int | None,
    doc: Document,
    tx_ids: list[int],
    plan_ids: list[int],
    statement_before: dict | None,
    statement_id: int | None,
    card_before: dict | None,
    links: list[dict],
) -> AuditLog:
    label = "Statement" if statement_id else "Receipt"
    entry = AuditLog(
        user_id=user_id,
        entity="document",
        entity_id=doc.id,
        action="commit",
        before={"statement": statement_before, "card": card_before, "links": links},
        after={"tx_ids": tx_ids, "plan_ids": plan_ids, "statement_id": statement_id},
        summary=f"{label} saved: {_plural(len(tx_ids), 'transaction')}" + (f", {_plural(len(links), 'match', 'matches')}" if links else ""),
    )
    db.add(entry)
    return entry


# ---- Undo ------------------------------------------------------------------------


def _restore(db: Session, model, data: dict):
    cols = {c.name: c for c in model.__table__.columns}
    kwargs = {}
    for k, v in data.items():
        if k not in cols or k in IGNORED:
            continue
        try:
            py = cols[k].type.python_type
        except NotImplementedError:
            py = None
        if isinstance(v, str) and py is date:
            v = date.fromisoformat(v)
        elif isinstance(v, str) and py is datetime:
            v = datetime.fromisoformat(v)
        kwargs[k] = v
    return kwargs


def _same(obj, expected: dict | None) -> bool:
    if obj is None or expected is None:
        return False
    current = snapshot(obj)
    return all(current.get(k) == v for k, v in expected.items() if k != "_plan")


def can_undo(db: Session, e: AuditLog) -> bool:
    if e.undone_at or e.action == "undo":
        return False
    if e.entity == "document":
        return all(db.get(Transaction, i) is not None for i in (e.after or {}).get("tx_ids", []))
    model = MODELS.get(e.entity)
    if model is None:
        return False
    obj = db.get(model, e.entity_id)
    if e.action == "delete":
        return obj is None
    return _same(obj, e.after)


def undo(db: Session, e: AuditLog, user_id: int | None) -> str:
    """Reverts the change. Does not commit."""
    if not can_undo(db, e):
        raise UndoError("This change cannot be undone (it was modified later or has already been undone).")
    if e.entity == "document":
        _undo_document(db, e)
    else:
        model = MODELS[e.entity]
        obj = db.get(model, e.entity_id)
        if e.action == "create":
            if e.entity == "payment":
                from . import services

                services.delete_card_payment(db, obj)  # adds the debt back, deletes the payment
            else:
                plan = obj.installment_plan if isinstance(obj, Transaction) else None
                db.delete(obj)
                if plan:
                    db.delete(plan)
        elif e.action == "delete":
            data = dict(e.before)
            plan = data.pop("_plan", None)
            if plan and db.get(InstallmentPlan, plan["id"]) is None:
                db.add(InstallmentPlan(**_restore(db, InstallmentPlan, plan)))
                db.flush()
            restored = model(**_restore(db, model, data))
            db.add(restored)
            _restore_side_effects(db, e.entity, restored)
        elif e.action == "update":
            for k, v in _restore(db, model, e.before).items():
                if k != "id":
                    setattr(obj, k, v)
    e.undone_at = datetime.now()
    db.add(AuditLog(user_id=user_id, entity=e.entity, entity_id=e.entity_id, action="undo",
                    summary=f"Undone: {e.summary}"))
    return e.summary


def _restore_side_effects(db: Session, entity: str, obj) -> None:
    if entity == "payment":
        # When a deleted payment is restored, it is deducted from the debt again
        from . import services

        card = db.get(CreditCard, obj.card_id)
        services._add_debt(card, obj.currency, -obj.amount_cents)
        st = db.get(CardStatement, obj.statement_id) if obj.statement_id else None
        if st:
            services._refresh_statement_paid(db, st)


def _undo_document(db: Session, e: AuditLog) -> None:
    after, before = e.after or {}, e.before or {}
    for tx_id in after.get("tx_ids", []):
        tx = db.get(Transaction, tx_id)
        if tx:
            plan = tx.installment_plan
            if plan:
                from .services import delete_installment_series

                delete_installment_series(db, plan)
            else:
                db.delete(tx)
    for plan_id in after.get("plan_ids", []):
        plan = db.get(InstallmentPlan, plan_id)
        if plan:
            from .services import delete_installment_series

            delete_installment_series(db, plan)
    for link in before.get("links", []):
        tx = db.get(Transaction, link["tx_id"])
        if tx:
            tx.card_id, tx.payment_method = link["card_id"], link["payment_method"]
    st_id = after.get("statement_id")
    if st_id:
        st = db.get(CardStatement, st_id)
        if st is not None:
            if before.get("statement"):
                for k, v in _restore(db, CardStatement, before["statement"]).items():
                    if k != "id":
                        setattr(st, k, v)
            else:
                db.delete(st)
    if before.get("card"):
        card = db.get(CreditCard, before["card"]["id"])
        if card:
            for k, v in _restore(db, CreditCard, before["card"]).items():
                if k != "id":
                    setattr(card, k, v)
    doc = db.get(Document, e.entity_id)
    if doc and doc.status == "done":
        doc.status = "review"  # can be confirmed again


def history(db: Session, limit: int = 100) -> list[dict]:
    names = {u.id: u.name for u in db.scalars(select(User))}
    out = []
    for e in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)):
        out.append(
            {
                "id": e.id,
                "at": e.at.isoformat(),
                "user": names.get(e.user_id, "System") if e.user_id else "System",
                "summary": e.summary,
                "action": e.action,
                "entity": e.entity,
                "undone": e.undone_at is not None,
                "can_undo": can_undo(db, e),
            }
        )
    return out


def last_changes(db: Session, entity: str, ids: list[int]) -> dict[int, dict]:
    """Most recent change of each record (who, when)."""
    if not ids:
        return {}
    names = {u.id: u.name for u in db.scalars(select(User))}
    out: dict[int, dict] = {}
    rows = db.scalars(
        select(AuditLog).where(AuditLog.entity == entity, AuditLog.entity_id.in_(ids)).order_by(AuditLog.id.desc())
    )
    for e in rows:
        if e.entity_id not in out:
            out[e.entity_id] = {"by": names.get(e.user_id, "System") if e.user_id else "System", "at": e.at.isoformat()}
    return out
