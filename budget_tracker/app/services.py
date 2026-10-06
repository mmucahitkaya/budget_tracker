"""Transaction creation and the document (receipt/statement) workflow."""
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import ai_parser, audit, finance, notify, prefs, telegram
from .db import SessionLocal
from .fx import ensure_rate_for
from .models import CardPayment, CardStatement, Category, CreditCard, Document, InstallmentPlan, RecurringPayment, Transaction

log = logging.getLogger(__name__)


def create_transaction(
    db: Session,
    *,
    kind: str,
    amount_cents: int,
    currency: str,
    on: date,
    category_id: int | None,
    user_id: int | None,
    payment_method: str,
    card_id: int | None,
    merchant: str = "",
    note: str = "",
    items: list | None = None,
    document_id: int | None = None,
    installment_count: int | None = None,
    first_installment_month: date | None = None,
    recurring_id: int | None = None,
    occurred_at: datetime | None = None,
) -> Transaction:
    """Saves the transaction; if it is a card installment purchase, also creates the installment plan. Does not commit."""
    tx = Transaction(
        kind=kind,
        amount_cents=amount_cents,
        currency=currency,
        date=on,
        category_id=category_id,
        user_id=user_id,
        payment_method=payment_method,
        card_id=card_id if payment_method == "card" else None,
        merchant=merchant,
        note=note,
        items=items,
        document_id=document_id,
        recurring_id=recurring_id,
        occurred_at=occurred_at,
    )
    if kind == "expense" and tx.card_id and installment_count and installment_count > 1:
        card = db.get(CreditCard, tx.card_id)
        first = first_installment_month or finance.statement_month_for(card, on)
        plan = InstallmentPlan(
            card_id=tx.card_id,
            description=merchant or note or "Installment purchase",
            monthly_cents=round(amount_cents / installment_count),
            currency=currency,
            count=installment_count,
            first_month=finance.month_start(first),
            category_id=category_id,
            user_id=user_id,
        )
        db.add(plan)
        db.flush()
        tx.installment_plan_id = plan.id
    db.add(tx)
    db.flush()
    if tx.installment_plan_id:
        # Each month gets that month's installment: this transaction becomes installment #1, the rest follow monthly
        sync_installments(db, db.get(InstallmentPlan, tx.installment_plan_id), base=tx)
    if merchant and category_id:
        finance.learn_rule(db, merchant, category_id)
    return tx


def after_transaction_saved(db: Session, tx: Transaction) -> None:
    """FX and budget checks (after commit; failures must not break the transaction)."""
    try:
        ensure_rate_for(db, tx.currency, tx.date)
    except Exception as e:
        log.warning("Could not fetch FX rate: %s", e)
    if tx.kind == "expense":
        try:
            finance.check_budget(db, tx.category_id, tx.date)
        except Exception as e:
            log.warning("Budget check failed: %s", e)


# ---- Document processing ---------------------------------------------------


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _match_card(db: Session, last4: str | None, bank: str | None, hint: int | None) -> int | None:
    if hint:
        return hint
    cards = db.scalars(select(CreditCard).where(CreditCard.archived.is_(False))).all()
    digits = "".join(ch for ch in (last4 or "") if ch.isdigit())[-4:]
    if digits:
        # If the last 4 digits were read, match on them only: don't link to another card from the same bank
        return next((c.id for c in cards if c.last4 == digits), None)
    if bank:
        matches = [c for c in cards if c.bank and c.bank.lower() in bank.lower()]
        if len(matches) == 1:
            return matches[0].id
    return None


def _category_for(db: Session, merchant: str, ai_category: str | None) -> int | None:
    return finance.rule_category(db, merchant) or finance.category_by_name(db, ai_category, "expense")


def build_draft(db: Session, doc: Document, parsed: dict) -> dict:
    doc_type = parsed.get("doc_type", "other")
    card_id = _match_card(db, parsed.get("card_last4"), parsed.get("bank"), doc.card_id)
    rows: list[dict] = []
    statement = None

    if doc_type == "receipt":
        merchant = parsed.get("merchant") or ""
        total = parsed.get("total")
        if total is None:
            total = sum(i["amount"] for i in parsed.get("items", []))
        first_inst = next((i.get("installment_count") for i in parsed.get("items", []) if i.get("installment_count")), None)
        on = _parse_date(parsed.get("date")) or date.today()
        currency = parsed.get("currency") or prefs.base()
        amount_cents = finance.to_cents(total)
        cands = finance.match_candidates(db, "expense", amount_cents, currency, on, merchant, card_id)
        dup = cands[0]["tx_id"] if cands and cands[0]["strong"] else None
        method = parsed.get("payment_method") or ("card" if card_id else "cash")
        rows.append(
            {
                "include": dup is None,
                "mode": "transaction",
                "kind": "expense",
                "date": on.isoformat(),
                "amount": finance.from_cents(amount_cents),
                "currency": currency,
                "merchant": merchant,
                "category_id": _category_for(db, merchant, parsed.get("category")),
                "payment_method": method,
                "card_id": card_id if method == "card" else None,
                "installment_count": first_inst if first_inst and first_inst > 1 else None,
                "installment_no": None,
                "duplicate_of": dup,
                "match": dup,
                "match_candidates": cands[:3],
                "items": [{"description": i["description"], "amount": i["amount"]} for i in parsed.get("items", [])],
            }
        )

    elif doc_type == "statement":
        period_end = _parse_date(parsed.get("period_end")) or date.today()
        due_date = _parse_date(parsed.get("due_date")) or period_end
        card = db.get(CreditCard, card_id) if card_id else None
        item_dates = [d for d in (_parse_date(i.get("date")) for i in parsed.get("items", [])) if d]
        if card and _implausible_dates(period_end, due_date, item_dates):
            # Statement/due date misread from the image: derive from the card's configured days, based on the last transaction date
            last = max(item_dates) if item_dates else date.today()
            period_end = finance.next_statement_date(card, last)
            due_month = period_end if card.due_day > card.statement_day else finance.add_months(period_end, 1)
            due_date = finance.business_day(finance.clamp_day(due_month.year, due_month.month, card.due_day))
            parsed["dates_estimated"] = True
        stmt_currency = parsed.get("currency") or prefs.base()
        statement = {
            "period_end": period_end.isoformat(),
            "due_date": due_date.isoformat(),
            "dates_estimated": bool(parsed.get("dates_estimated")),
            "currency": stmt_currency,
            "totals": parsed_totals(parsed),
            "min_payment": parsed.get("min_payment") or 0,
            "period_spending": parsed.get("period_spending"),
        }
        plans = db.scalars(select(InstallmentPlan).where(InstallmentPlan.card_id == card_id)).all() if card_id else []
        used: set[int] = set()  # an existing record matches only one row
        used_plans: set[int] = set()  # and an installment plan matches only one row too

        def suggest(row: dict, cents: int) -> None:
            cands = finance.match_candidates(db, "expense", cents, currency, on, merchant, card_id, exclude=used)
            row["match_candidates"] = cands[:3]
            if cands and cands[0]["strong"]:
                used.add(cands[0]["tx_id"])
                row.update(include=False, duplicate_of=cands[0]["tx_id"], match=cands[0]["tx_id"])

        for item in parsed.get("items", []):
            merchant = item.get("description") or ""
            on = _parse_date(item.get("date")) or period_end
            currency = item.get("currency") or stmt_currency
            amount_cents = finance.to_cents(item["amount"])
            no, count = item.get("installment_no"), item.get("installment_count")
            if no and count and no > count:
                no = None
            row = {
                "line_amount": item["amount"],  # amount on the statement line (for the total check)
                "include": True,
                "mode": "transaction",
                "kind": "expense",
                "date": on.isoformat(),
                "amount": finance.from_cents(amount_cents),
                "currency": currency,
                "merchant": merchant,
                "category_id": _category_for(db, merchant, item.get("category")),
                "payment_method": "card",
                "card_id": card_id,
                "installment_count": count if count and count > 1 else None,
                "installment_no": no,
                "duplicate_of": None,
                "match": None,
                "match_candidates": [],
                "items": None,
            }
            if item.get("is_refund"):
                row.update(include=False, kind="income", category_id=finance.category_by_name(db, "Other Income", "income"))
            elif count and count > 1:
                existing = _match_plan(plans, amount_cents, count, currency, merchant, used_plans)
                if existing:
                    used_plans.add(existing.id)
                    row.update(include=False, mode="installment_only", note="This installment plan is already recorded")
                elif no and no > 1:
                    # Installment started earlier: the purchase belongs to past months, only open a plan for the card load
                    row.update(mode="installment_only")
                else:
                    # Installment #1: the transaction amount is the full purchase amount
                    row["amount"] = finance.from_cents(amount_cents * count)
                    suggest(row, amount_cents * count)
            else:
                suggest(row, amount_cents)
            rows.append(row)

    return {"doc_type": doc_type, "card_id": card_id, "statement": statement, "rows": rows, "source": parsed.get("source")}


_LEGACY_TOTAL_KEYS = {"TRY": "total_debt_try", "USD": "total_debt_usd", "EUR": "total_debt_eur"}


def parsed_totals(parsed: dict) -> dict[str, float]:
    """Statement balance per currency from the parser result: "totals": [{"currency", "amount"}] (or a dict).

    Older results with total_debt_try/usd/eur are still understood.
    """
    out: dict[str, float] = {}
    raw = parsed.get("totals")
    if isinstance(raw, dict):
        raw = [{"currency": c, "amount": a} for c, a in raw.items()]
    for t in raw or []:
        if not isinstance(t, dict):
            continue
        cur = str(t.get("currency") or parsed.get("currency") or prefs.base()).upper()
        try:
            amount = float(t.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if amount:
            out[cur] = round(out.get(cur, 0) + amount, 2)
    for cur, key in _LEGACY_TOTAL_KEYS.items():
        if parsed.get(key) and cur not in out:
            out[cur] = parsed[key]
    return out


def _match_plan(plans, cents: int, count: int, currency: str, merchant: str, used: set[int]):
    """The recorded plan that matches an installment line on the statement.

    Banks may print the first installment with a few cents difference (1,216.94 / 1,216.50): amounts within 1 unit or 1% are accepted.
    With multiple candidates, the one with a similar merchant name and the closest amount wins.
    """
    tol = max(100, round(cents * 0.01))
    cands = [p for p in plans if p.id not in used and p.count == count and p.currency == currency
             and abs(p.monthly_cents - cents) <= tol]
    if not cands:
        return None
    return min(cands, key=lambda p: (-finance.merchant_overlap(p.description, merchant), abs(p.monthly_cents - cents)))


def _implausible_dates(period_end: date, due: date, item_dates: list[date]) -> bool:
    """True if the statement/due dates are inconsistent: the due date must be 5-40 days after the cut, transactions before the cut."""
    if not 5 <= (due - period_end).days <= 40:
        return True
    if period_end > date.today() + timedelta(days=1):
        return True
    # Old installment lines may be back-dated; only a transaction after the cut is inconsistent
    return bool(item_dates) and max(item_dates) > period_end + timedelta(days=3)


def process_document(doc_id: int) -> None:
    """Runs in the background: reads the document and prepares a draft for review."""
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            return
        doc.status = "processing"
        doc.error = ""
        db.commit()
        names = [c.name for c in db.scalars(select(Category).where(Category.kind == "expense")).all()]
        try:
            parsed = ai_parser.parse_document(Path(doc.stored_path), doc.mime, doc.kind, names)
            draft = build_draft(db, doc, parsed)
        except Exception as e:
            log.exception("Could not process document: %s", doc.filename)
            doc.status = "error"
            doc.error = str(e)
            db.commit()
            if doc.telegram_chat_id:
                telegram.document_done(doc)
            else:
                notify.send("Could not read document", f"{doc.filename}: {e}")
            return
        doc.result = {"parsed": parsed, "draft": draft}
        doc.status = "review" if draft["doc_type"] != "other" else "error"
        if doc.status == "error":
            doc.error = "The document was not recognized as a receipt or a credit card statement."
        db.commit()
        if doc.telegram_chat_id:
            telegram.document_done(doc)
        elif doc.status == "review":
            label = "Statement" if draft["doc_type"] == "statement" else "Receipt"
            notify.send("Awaiting review", f"{label} read: {len(draft['rows'])} items are waiting for your review.")
    finally:
        db.close()


class ValidationError(ValueError):
    """Validation error shown to the user (400)."""


def validate_refs(db: Session, kind: str, category_id: int | None, card_id: int | None) -> None:
    """Checks that the category and card exist and that the category kind matches the transaction kind."""
    if category_id is not None:
        cat = db.get(Category, category_id)
        if cat is None:
            raise ValidationError("Category not found")
        if cat.kind != kind:
            raise ValidationError(f"'{cat.name}' is an {'income' if cat.kind == 'income' else 'expense'} category")
    if card_id is not None and db.get(CreditCard, card_id) is None:
        raise ValidationError("Card not found")


def claim_document(db: Session, doc_id: int) -> bool:
    """Atomically moves a document awaiting review to 'committing'; prevents a concurrent second confirmation."""
    res = db.execute(
        update(Document).where(Document.id == doc_id, Document.status == "review").values(status="committing")
    )
    db.commit()
    return res.rowcount == 1


def release_document(db: Session, doc_id: int) -> None:
    """If saving fails, returns the document to awaiting review."""
    db.rollback()
    db.execute(update(Document).where(Document.id == doc_id, Document.status == "committing").values(status="review"))
    db.commit()


def _link_existing_to_card(db: Session, row: dict, card_id: int | None) -> dict | None:
    """If a statement line matched an existing expense, links that record to the card.

    The name the user typed and the category they chose are kept; only the payment method and card are updated.
    Records explicitly marked "cash" in the message are not moved to the card.
    """
    row_card = row.get("card_id") or card_id
    match = row.get("match") or row.get("duplicate_of")
    if not match or not row_card or row.get("payment_method") != "card":
        return
    tx = db.get(Transaction, match)
    if tx and tx.card_id is None and tx.kind == "expense" and tx.note not in ("Telegram · cash", "Telegram · nakit"):
        link = {"tx_id": tx.id, "card_id": tx.card_id, "payment_method": tx.payment_method}
        tx.card_id = row_card
        tx.payment_method = "card"
        return link
    return None


def _split_matched_purchase(db: Session, row: dict, card_id: int | None) -> None:
    """If a purchase entered as a single payment shows up as an installment (1/N) on the statement, converts it to an installment series."""
    count = row.get("installment_count")
    match = row.get("match") or row.get("duplicate_of")
    if not count or count < 2 or not match or row.get("mode") == "installment_only":
        return
    tx = db.get(Transaction, match)
    if not tx or tx.installment_plan_id or tx.kind != "expense" or not tx.card_id:
        return
    card = db.get(CreditCard, tx.card_id)
    monthly = finance.to_cents(row.get("line_amount") or row["amount"] / count)
    plan = InstallmentPlan(card_id=tx.card_id, description=tx.merchant or row.get("merchant") or "Installment purchase",
                           monthly_cents=monthly, currency=tx.currency, count=count,
                           first_month=finance.month_start(finance.statement_month_for(card, tx.date)),
                           category_id=tx.category_id, user_id=tx.user_id)
    db.add(plan)
    db.flush()
    tx.installment_plan_id = plan.id
    sync_installments(db, plan, base=tx)


def apply_statement(db: Session, card: CreditCard, stmt: dict, document_id: int | None) -> CardStatement:
    """Writes the statement to the card's history. The card's current debt and statement/due days are only
    updated if this is the card's newest statement; re-uploading the same period updates the record."""
    period_end = stmt["period_end"] if isinstance(stmt["period_end"], date) else date.fromisoformat(stmt["period_end"])
    due = stmt["due_date"] if isinstance(stmt["due_date"], date) else date.fromisoformat(stmt["due_date"])
    st = db.scalar(select(CardStatement).where(CardStatement.card_id == card.id, CardStatement.period_end == period_end))
    if st is None:
        st = CardStatement(card_id=card.id, period_end=period_end)
        db.add(st)
    st.due_date = due
    st.currency = stmt.get("currency") or prefs.base()
    st.totals = statement_totals_cents(stmt)
    st.min_payment_cents = finance.to_cents(stmt.get("min_payment"))
    if document_id:
        st.document_id = document_id
    if stmt.get("paid"):
        st.paid = True
    db.flush()
    newest = db.scalar(select(func.max(CardStatement.period_end)).where(CardStatement.card_id == card.id))
    if period_end >= newest:
        # If the bank shifted the payment week, update the card's days from the statement
        if finance.day_distance(card.statement_day, period_end.day) > 2:
            card.statement_day = period_end.day
        if finance.day_distance(card.due_day, due.day) > 2:
            card.due_day = due.day
        # Payments already made toward this statement are deducted
        card.debts = {cur: v for cur, v in statement_remaining(db, st).items() if v > 0}
        card.debt_updated_at = datetime.now()
    return st


# ---- Card payments ---------------------------------------------------------------

_LEGACY_STMT_KEYS = {"TRY": "total_try", "USD": "total_usd", "EUR": "total_eur"}


def statement_totals_cents(stmt: dict) -> dict[str, int]:
    """{"totals": {cur: amount}} -> {cur: cents} without zero entries (legacy total_try/usd/eur also accepted)."""
    out: dict[str, int] = {}
    for cur, amount in (stmt.get("totals") or {}).items():
        cents = finance.to_cents(amount)
        if cents:
            out[cur.upper()] = out.get(cur.upper(), 0) + cents
    for cur, key in _LEGACY_STMT_KEYS.items():
        if stmt.get(key) and cur not in out:
            out[cur] = finance.to_cents(stmt[key])
    return out


def card_debts_base_cents(conv, card: CreditCard, on: date) -> int:
    """The card's debts converted to base-currency cents (conv: fx.Converter)."""
    return conv.dict_to_base_cents(card.debts or {}, on)


def _add_debt(card: CreditCard, currency: str, delta: int) -> None:
    debts = dict(card.debts or {})
    value = max(0, debts.get(currency, 0) + delta)
    if value:
        debts[currency] = value
    else:
        debts.pop(currency, None)
    card.debts = debts


def statement_paid(db: Session, st: CardStatement) -> dict[str, int]:
    rows = db.execute(
        select(CardPayment.currency, func.sum(CardPayment.amount_cents))
        .where(CardPayment.statement_id == st.id)
        .group_by(CardPayment.currency)
    ).all()
    return {cur: int(total or 0) for cur, total in rows}


def statement_remaining(db: Session, st: CardStatement) -> dict[str, int]:
    """Unpaid amount per currency; only currencies with a non-zero total or payments appear."""
    paid = statement_paid(db, st)
    totals = st.totals or {}
    return {cur: max(0, totals.get(cur, 0) - paid.get(cur, 0))
            for cur in sorted(set(totals) | set(paid)) if totals.get(cur) or paid.get(cur)}


def _refresh_statement_paid(db: Session, st: CardStatement) -> None:
    db.flush()
    remaining = statement_remaining(db, st)
    has_debt = any((st.totals or {}).values())
    st.paid = has_debt and all(v == 0 for v in remaining.values())
    st.paid_at = datetime.now() if st.paid else None


def add_card_payment(
    db: Session, card: CreditCard, amount_cents: int, currency: str, on: date,
    statement_id: int | None, user_id: int | None, note: str = "",
) -> CardPayment:
    """Records a card payment, reduces the card debt, and marks the statement paid if fully paid. Does not commit."""
    if amount_cents <= 0:
        raise ValidationError("Payment amount must be greater than zero")
    st = db.get(CardStatement, statement_id) if statement_id else None
    if statement_id and (st is None or st.card_id != card.id):
        raise ValidationError("Statement does not belong to this card")
    p = CardPayment(card_id=card.id, statement_id=statement_id, amount_cents=amount_cents, currency=currency,
                    date=on, user_id=user_id, note=note)
    db.add(p)
    _add_debt(card, currency, -amount_cents)
    card.debt_updated_at = datetime.now()
    if st:
        _refresh_statement_paid(db, st)
    return p


def delete_card_payment(db: Session, p: CardPayment) -> None:
    """Reverses a payment: the card debt is added back. Does not commit."""
    card = db.get(CreditCard, p.card_id)
    _add_debt(card, p.currency, p.amount_cents)
    card.debt_updated_at = datetime.now()
    st = db.get(CardStatement, p.statement_id) if p.statement_id else None
    db.delete(p)
    if st:
        _refresh_statement_paid(db, st)


def migrate_paid_statements(db: Session) -> None:
    """Creates payment records for statements marked 'paid' before the payment-record feature existed
    (does not touch the card since the debt was already deducted). Runs once."""
    from .models import SentNotice

    key = "migration:card_payments_v1"
    if db.scalar(select(SentNotice.id).where(SentNotice.key == key)):
        return
    for st in db.scalars(select(CardStatement).where(CardStatement.paid.is_(True))):
        if not db.scalar(select(CardPayment.id).where(CardPayment.statement_id == st.id)):
            for cur, cents in (st.totals or {}).items():
                if cents:
                    db.add(CardPayment(card_id=st.card_id, statement_id=st.id, amount_cents=cents, currency=cur,
                                       date=(st.paid_at.date() if st.paid_at else st.due_date), note="Previously paid"))
    db.add(SentNotice(key=key))
    db.commit()


def card_user(db: Session, card_id: int | None) -> int | None:
    """Default person for card spending: the card user (supplementary card), otherwise the card owner."""
    card = db.get(CreditCard, card_id) if card_id else None
    return (card.holder_id or card.owner_id) if card else None


def commit_draft(db: Session, doc: Document, draft: dict, user_id: int) -> int:
    """Saves the draft the user confirmed (and edited). Returns the number of records created."""
    created: list[Transaction] = []
    card_id = draft.get("card_id")
    stmt = draft.get("statement")
    stmt_month = finance.month_start(date.fromisoformat(stmt["period_end"])) if stmt else None

    links: list[dict] = []
    plans: list[InstallmentPlan] = []
    for row in draft.get("rows", []):
        if not row.get("include"):
            link = _link_existing_to_card(db, row, card_id)
            if link:
                links.append(link)
            _split_matched_purchase(db, row, card_id)
            continue
        row_card = (row.get("card_id") or card_id) if row.get("payment_method") == "card" else None
        validate_refs(db, row.get("kind", "expense"), row.get("category_id"), row_card)
        on = date.fromisoformat(row["date"])
        amount_cents = finance.to_cents(row["amount"])
        count = row.get("installment_count")
        if row.get("mode") == "installment_only":
            if not row_card or not count:
                continue
            no = row.get("installment_no") or 1
            first = finance.add_months(stmt_month or finance.month_start(on), -(no - 1))
            plan = InstallmentPlan(
                card_id=row_card,
                description=row.get("merchant") or "Installment",
                monthly_cents=amount_cents,
                currency=row.get("currency") or prefs.base(),
                count=count,
                first_month=first,
                category_id=row.get("category_id"),
                user_id=card_user(db, row_card) or user_id,
            )
            db.add(plan)
            db.flush()
            plans.append(plan)
            # Installment started earlier: from the day this installment hits the statement, one installment per month
            period_end = date.fromisoformat(stmt["period_end"]) if stmt else None
            sync_installments(db, plan, start_no=no, anchor=installment_posting_date(on, no, period_end))
            continue
        tx = create_transaction(
            db,
            kind=row.get("kind", "expense"),
            amount_cents=amount_cents,
            currency=row.get("currency") or prefs.base(),
            on=on,
            category_id=row.get("category_id"),
            user_id=card_user(db, row_card) or user_id,
            payment_method=row.get("payment_method", "card"),
            card_id=row_card,
            merchant=row.get("merchant", ""),
            items=row.get("items"),
            document_id=doc.id,
            installment_count=count,
            first_installment_month=stmt_month,
        )
        created.append(tx)

    statement_id = statement_before = card_before = None
    if stmt and card_id:
        card = db.get(CreditCard, card_id)
        if card is None:
            raise ValidationError("Card not found")
        period_end = date.fromisoformat(stmt["period_end"])
        existing = db.scalar(select(CardStatement).where(CardStatement.card_id == card.id, CardStatement.period_end == period_end))
        statement_before = audit.snapshot(existing) if existing else None
        card_before = audit.snapshot(card)
        statement_id = apply_statement(db, card, stmt, doc.id).id

    db.flush()
    audit.record_document_commit(
        db, user_id, doc,
        tx_ids=[t.id for t in created], plan_ids=[p.id for p in plans],
        statement_before=statement_before, statement_id=statement_id, card_before=card_before, links=links,
    )

    doc.status = "done"
    result = dict(doc.result or {})
    result["draft"] = draft
    doc.result = result
    db.commit()
    if statement_id and (telegram.enabled() or notify.settings.notify_services):
        # Statement processed: notify about the card payment and current spendable amount (does not block the request)
        from . import scheduler

        threading.Thread(target=scheduler.notify_statement_committed, args=(card_id, statement_id), daemon=True).start()
    for tx in created:
        after_transaction_saved(db, tx)
    return len(created)


def materialize_recurring(db: Session, r: RecurringPayment, until: date, create_from: date | None = None) -> list[date]:
    """Turns payments due up to `until` into transactions and advances next_date. Does not commit.

    create_from: no transactions are created for dates before this (they are only advanced). Returns: processed dates.
    """
    done = []
    for d in finance.recurring_dates(r.next_date, r.frequency, r.day, r.end_date, until):
        if finance.recurring_amount(r, d) == 0:  # no payment this month (e.g. the 10% is not paid in October)
            r.next_date = finance.advance_recurring(d, r.frequency, r.day)
            continue
        if r.auto_create and (create_from is None or d >= create_from):
            tx = create_transaction(
                db,
                kind=r.kind,
                amount_cents=finance.recurring_amount(r, d),
                currency=r.currency,
                on=d,
                category_id=r.category_id,
                user_id=r.user_id,
                payment_method=r.payment_method,
                card_id=r.card_id,
                merchant=r.name,
                note=f"Recurring payment · {r.asset_qty:g} gold units at that day's price" if r.asset_code and r.asset_qty else "Recurring payment",
                recurring_id=r.id,
            )
            db.flush()
            audit.record(db, None, "transaction", tx, "create")
            after_transaction_saved(db, tx)
        done.append(d)
        r.next_date = finance.advance_recurring(d, r.frequency, r.day)
    if r.end_date and r.next_date > r.end_date:
        r.active = False
    return done


def card_from_statement(db: Session, doc: Document, owner_id: int | None) -> CreditCard:
    """If no card matches, creates a new card from the statement details and links it to the draft. Does not commit."""
    parsed = (doc.result or {}).get("parsed") or {}
    draft = (doc.result or {}).get("draft") or {}
    stmt = draft.get("statement") or {}
    bank = (parsed.get("bank") or "").replace("T. ", "").strip()
    bank_short = bank.split()[0].title() if bank else "Card"
    last4 = "".join(ch for ch in (parsed.get("card_last4") or "") if ch.isdigit())[-4:]
    cut = date.fromisoformat(stmt["period_end"]) if stmt.get("period_end") else None
    due = date.fromisoformat(stmt["due_date"]) if stmt.get("due_date") else None
    card = CreditCard(
        name=bank_short,
        bank=bank,
        last4=last4,
        statement_day=cut.day if cut else 1,
        due_day=due.day if due else 10,
        owner_id=owner_id,
    )
    db.add(card)
    db.flush()
    draft["card_id"] = card.id
    for row in draft.get("rows", []):
        if row.get("payment_method") == "card":
            row["card_id"] = card.id
    result = dict(doc.result)
    result["draft"] = draft
    doc.result = result
    return card


# ---- Installment series ------------------------------------------------------------------


def sync_installments(db: Session, plan: InstallmentPlan, base: Transaction | None = None, start_no: int = 1,
                      anchor: date | None = None, note_prefix: str = "") -> list[Transaction]:
    """Creates a separate expense transaction for each month of the installment plan (system records are rebuilt).

    base: the purchase itself (becomes installment #1, its amount drops to the monthly installment). Without base
    (an older installment from a statement), one is created per month from start_no on, starting at the anchor date.
    """
    for t in db.scalars(select(Transaction).where(Transaction.installment_plan_id == plan.id)):
        if base is None or t.id != base.id:
            db.delete(t)
    db.flush()
    created = []
    if base is not None:
        base.amount_cents = plan.monthly_cents
        base.installment_no = start_no
        anchor, first_new = base.date, start_no + 1
        template = base
    else:
        anchor = anchor or plan.first_month
        first_new = start_no
        template = None
    user_id = template.user_id if template else plan.user_id
    category_id = template.category_id if template else plan.category_id
    for k in range(first_new, plan.count + 1):
        on = finance.add_months(anchor, k - (start_no if base is None else start_no))
        on = finance.clamp_day(on.year, on.month, anchor.day)
        t = Transaction(
            kind="expense", amount_cents=plan.monthly_cents, currency=plan.currency, date=on,
            category_id=category_id, user_id=user_id, payment_method="card", card_id=plan.card_id,
            merchant=plan.description, note=f"{note_prefix}Installment {k}/{plan.count}", installment_plan_id=plan.id,
            installment_no=k, category_confirmed=bool(template and template.category_confirmed),
        )
        db.add(t)
        created.append(t)
    db.flush()
    return created


def delete_installment_series(db: Session, plan: InstallmentPlan) -> None:
    """Deletes the plan and all its monthly installment transactions."""
    for t in db.scalars(select(Transaction).where(Transaction.installment_plan_id == plan.id)):
        db.delete(t)
    db.delete(plan)


def migrate_installments_monthly(db: Session) -> int:
    """Old layout (full purchase amount as one transaction, older installments as plan only) -> one installment per month.

    Runs once. The date and category of older installments from statements are taken from the document drafts.
    """
    from .models import AppSetting, Document

    key = "migration:installments_monthly"
    if db.get(AppSetting, key):
        return 0
    # Installment rows in statement drafts: (card, monthly amount, count) -> row
    rows: dict[tuple, dict] = {}
    for doc in db.scalars(select(Document).where(Document.status == "done")):
        draft = (doc.result or {}).get("draft") or {}
        st = draft.get("statement") or {}
        for r in draft.get("rows", []):
            if r.get("installment_count"):
                card_id = r.get("card_id") or draft.get("card_id")
                cents = finance.to_cents(r.get("line_amount") or r["amount"])
                rows.setdefault((card_id, cents, r["installment_count"]), {**r, "stmt_end": st.get("period_end")})
    n = 0
    for plan in db.scalars(select(InstallmentPlan)).all():
        base = db.scalar(select(Transaction).where(Transaction.installment_plan_id == plan.id).order_by(Transaction.id))
        card = db.get(CreditCard, plan.card_id)
        if base is not None:
            plan.category_id = plan.category_id or base.category_id
            plan.user_id = plan.user_id or base.user_id
            sync_installments(db, plan, base=base)
        else:
            src = rows.get((plan.card_id, plan.monthly_cents, plan.count)) or {}
            plan.category_id = plan.category_id or src.get("category_id")
            plan.user_id = plan.user_id or (card_user(db, plan.card_id) if card else None)
            no = src.get("installment_no") or 1
            purchase = _parse_date(src.get("date"))
            stmt_month = finance.month_start(_parse_date(src.get("stmt_end")) or finance.add_months(plan.first_month, no - 1))
            day = purchase.day if purchase else 1
            anchor = finance.clamp_day(stmt_month.year, stmt_month.month, day)
            sync_installments(db, plan, start_no=no, anchor=anchor)
        n += 1
    db.add(AppSetting(key=key, value="1"))
    db.commit()
    return n


def installment_posting_date(row_date: date, no: int, period_end: date | None) -> date:
    """The date a statement installment line posts in this period.

    Some banks (e.g. Garanti, Is Bankasi) print the posting date on the line; others (e.g. VakifBank) often print the original purchase date.
    If the date is within the statement period it is the posting date; otherwise it is the purchase date and is moved (no-1) months forward.
    Order date = posting date - (no-1) months.
    """
    if period_end is None or period_end - timedelta(days=35) < row_date <= period_end + timedelta(days=3):
        return row_date
    return finance.add_months(row_date, no - 1)


def realign_statement_installments(db: Session) -> int:
    """Moves older installment series from statements to their correct posting dates (runs once).

    Category and 'confirmed' flags are kept.
    """
    from .models import AppSetting, Document

    key = "migration:installments_anchor_v2"
    if db.get(AppSetting, key):
        return 0
    rows: dict[tuple, tuple[dict, str | None]] = {}
    for doc in db.scalars(select(Document).where(Document.status == "done")):
        draft = (doc.result or {}).get("draft") or {}
        st = draft.get("statement") or {}
        for r in draft.get("rows", []):
            if r.get("installment_count") and r.get("mode") == "installment_only":
                card_id = r.get("card_id") or draft.get("card_id")
                cents = finance.to_cents(r.get("line_amount") or r["amount"])
                rows.setdefault((card_id, cents, r["installment_count"]), (r, st.get("period_end")))
    n = 0
    for plan in db.scalars(select(InstallmentPlan)).all():
        txs = list(db.scalars(select(Transaction).where(Transaction.installment_plan_id == plan.id)))
        if not txs or min(t.installment_no or 1 for t in txs) == 1:
            continue  # the purchase itself is recorded (installment #1): the date is already correct
        src = rows.get((plan.card_id, plan.monthly_cents, plan.count))
        if not src:
            continue
        r, period_end = src
        no = r.get("installment_no") or min(t.installment_no for t in txs)
        anchor = installment_posting_date(_parse_date(r["date"]), no, _parse_date(period_end))
        confirmed = any(t.category_confirmed for t in txs)
        if plan.category_id is None:
            plan.category_id = next((t.category_id for t in txs if t.category_id), None)
        for t in sync_installments(db, plan, start_no=no, anchor=anchor):
            t.category_confirmed = confirmed
        n += 1
    db.add(AppSetting(key=key, value="1"))
    db.commit()
    return n
