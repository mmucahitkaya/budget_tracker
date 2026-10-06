from datetime import date as Date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, finance, prefs, savings
from ..auth import current_user
from ..db import get_db
from ..models import Account, AccountMovement, GoalAllocation, SavingsGoal, User
from ..schemas import Currency

router = APIRouter(prefix="/api/savings", tags=["savings"])


class Input(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class AccountIn(Input):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["cash", "bank", "savings"]
    currency: Currency = Field(default_factory=prefs.base)
    opening_balance: Decimal = Field(
        default=0, ge=0, le=1_000_000_000, decimal_places=2
    )
    opening_date: Date
    owner_id: int | None = None
    archived: bool = False


class MovementIn(Input):
    kind: Literal["transfer", "deposit", "withdrawal", "adjustment"]
    source_id: int | None = None
    target_id: int | None = None
    amount: Decimal = Field(gt=0, le=1_000_000_000, decimal_places=2)
    received_amount: Decimal | None = Field(
        None, gt=0, le=1_000_000_000, decimal_places=2
    )
    date: Date
    note: str = Field("", max_length=300)
    client_ref: str = Field(min_length=8, max_length=64)

    @model_validator(mode="after")
    def endpoints(self):
        if self.source_id == self.target_id:
            raise ValueError("Source and destination must be different")
        if self.kind == "transfer" and not (self.source_id and self.target_id):
            raise ValueError("Select two accounts for a transfer")
        if self.kind == "deposit" and (self.source_id or not self.target_id):
            raise ValueError("Select a destination account for a deposit")
        if self.kind == "withdrawal" and (self.target_id or not self.source_id):
            raise ValueError("Select a source account for a withdrawal")
        if self.kind == "adjustment" and bool(self.source_id) == bool(self.target_id):
            raise ValueError("For an adjustment, select only the increased or decreased account")
        return self


class GoalIn(Input):
    name: str = Field(min_length=1, max_length=100)
    currency: Currency = Field(default_factory=prefs.base)
    target: Decimal = Field(gt=0, le=1_000_000_000, decimal_places=2)
    target_date: Date | None = None
    monthly: Decimal = Field(default=0, ge=0, le=1_000_000_000, decimal_places=2)
    reminder_day: int | None = Field(None, ge=1, le=31)
    owner_id: int | None = None
    emergency: bool = False
    archived: bool = False


class AllocationIn(Input):
    account_id: int
    amount: Decimal = Field(ge=-1_000_000_000, le=1_000_000_000, decimal_places=2)
    client_ref: str = Field(min_length=8, max_length=64)


def owner(db, uid):
    if uid is not None and not db.get(User, uid):
        raise HTTPException(400, "User not found")


def account(db, aid):
    a = db.get(Account, aid) if aid else None
    if not a or a.archived:
        raise HTTPException(400, "Active account not found")
    return a


def finish(db):
    try:
        savings.validate_ledger(db)
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e


@router.get("")
def get_overview(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return savings.overview(db)


@router.post("/accounts")
def create_account(
    body: AccountIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    savings.write_lock(db)
    owner(db, body.owner_id)
    if body.opening_date > Date.today():
        raise HTTPException(400, "Opening date can't be in the future")
    a = Account(
        name=body.name,
        kind=body.kind,
        currency=body.currency,
        opening_cents=finance.to_cents(body.opening_balance),
        opening_date=body.opening_date,
        owner_id=body.owner_id,
        archived=body.archived,
    )
    db.add(a)
    audit.record(db, user.id, "account", a, "create")
    finish(db)
    return savings.account_out(db, a, Date.today())


@router.put("/accounts/{aid}")
def update_account(
    aid: int,
    body: AccountIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    savings.write_lock(db)
    a = db.get(Account, aid)
    if not a:
        raise HTTPException(404)
    owner(db, body.owner_id)
    # Opening entries and account currency/kind remain immutable to preserve history.
    if (a.kind, a.currency, a.opening_cents, a.opening_date) != (
        body.kind,
        body.currency,
        finance.to_cents(body.opening_balance),
        body.opening_date,
    ):
        raise HTTPException(
            400, "Opening details can't be changed; add a balance adjustment entry"
        )
    before = audit.snapshot(a)
    a.name, a.owner_id, a.archived = body.name, body.owner_id, body.archived
    audit.record(db, user.id, "account", a, "update", before)
    finish(db)
    return savings.account_out(db, a, Date.today())


@router.post("/movements")
def create_movement(
    body: MovementIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    savings.write_lock(db)
    old = db.scalar(
        select(AccountMovement).where(AccountMovement.client_ref == body.client_ref)
    )
    if old:
        expected = (
            body.kind,
            body.source_id,
            body.target_id,
            finance.to_cents(body.amount) if body.source_id else 0,
            finance.to_cents(
                body.received_amount
                if body.received_amount is not None
                else body.amount
            )
            if body.target_id
            else 0,
            body.date,
            body.note,
            user.id,
        )
        if (
            old.kind,
            old.source_id,
            old.target_id,
            old.source_cents,
            old.target_cents,
            old.date,
            old.note,
            old.user_id,
        ) != expected:
            raise HTTPException(
                409, "This request key was already used for a different entry"
            )
        return {"id": old.id}
    if body.date > Date.today():
        raise HTTPException(
            400, "Don't add future entries to the balance; use a contribution plan"
        )
    source = account(db, body.source_id) if body.source_id else None
    target = account(db, body.target_id) if body.target_id else None
    amount = finance.to_cents(body.amount)
    received = (
        finance.to_cents(body.received_amount)
        if body.received_amount is not None
        else amount
    )
    if source and target:
        if source.currency != target.currency and body.received_amount is None:
            raise HTTPException(
                400, "For a currency transfer, enter the actual amount received"
            )
        if source.currency == target.currency and received != amount:
            raise HTTPException(
                400, "Transfer amounts in the same currency must be equal"
            )
    elif body.received_amount is not None:
        raise HTTPException(400, "Received amount is only used for transfers")
    m = AccountMovement(
        kind=body.kind,
        source_id=body.source_id,
        target_id=body.target_id,
        source_cents=amount if source else 0,
        target_cents=received if target else 0,
        date=body.date,
        note=body.note,
        user_id=user.id,
        client_ref=body.client_ref,
    )
    db.add(m)
    audit.record(db, user.id, "movement", m, "create")
    finish(db)
    return {"id": m.id}


@router.delete("/movements/{mid}")
def delete_movement(
    mid: int, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    savings.write_lock(db)
    m = db.get(AccountMovement, mid)
    if not m:
        raise HTTPException(404)
    audit.record(db, user.id, "movement", m, "delete", audit.snapshot(m))
    db.delete(m)
    finish(db)
    return {"ok": True}


@router.post("/goals")
def create_goal(
    body: GoalIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    savings.write_lock(db)
    owner(db, body.owner_id)
    g = SavingsGoal(
        name=body.name,
        currency=body.currency,
        target_cents=finance.to_cents(body.target),
        target_date=body.target_date,
        monthly_cents=finance.to_cents(body.monthly),
        reminder_day=body.reminder_day,
        owner_id=body.owner_id,
        emergency=body.emergency,
        archived=body.archived,
    )
    db.add(g)
    audit.record(db, user.id, "goal", g, "create")
    finish(db)
    return savings.goal_out(db, g, Date.today())


@router.put("/goals/{gid}")
def update_goal(
    gid: int,
    body: GoalIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    savings.write_lock(db)
    g = db.get(SavingsGoal, gid)
    if not g:
        raise HTTPException(404)
    owner(db, body.owner_id)
    allocated = sum(
        a.amount_cents
        for a in db.scalars(select(GoalAllocation).where(GoalAllocation.goal_id == gid))
    )
    has_history = db.scalar(
        select(GoalAllocation.id).where(GoalAllocation.goal_id == gid).limit(1)
    )
    if has_history and body.currency != g.currency:
        raise HTTPException(
            409, "The currency of a goal with allocation history can't be changed"
        )
    if allocated and body.archived:
        raise HTTPException(409, "First release the money allocated to the goal")
    before = audit.snapshot(g)
    for key in (
        "name",
        "currency",
        "target_date",
        "reminder_day",
        "owner_id",
        "emergency",
        "archived",
    ):
        setattr(g, key, getattr(body, key))
    g.target_cents, g.monthly_cents = (
        finance.to_cents(body.target),
        finance.to_cents(body.monthly),
    )
    audit.record(db, user.id, "goal", g, "update", before)
    finish(db)
    return savings.goal_out(db, g, Date.today())


@router.post("/goals/{gid}/allocations")
def allocate(
    gid: int,
    body: AllocationIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    savings.write_lock(db)
    old = db.scalar(
        select(GoalAllocation).where(GoalAllocation.client_ref == body.client_ref)
    )
    if old:
        if (old.goal_id, old.account_id, old.amount_cents, old.user_id) != (
            gid,
            body.account_id,
            finance.to_cents(body.amount),
            user.id,
        ):
            raise HTTPException(
                409, "This request key was already used for a different allocation"
            )
        return {"id": old.id}
    g = db.get(SavingsGoal, gid)
    a = account(db, body.account_id)
    if not g or g.archived:
        raise HTTPException(404, "Active goal not found")
    if a.currency != g.currency or a.kind != "savings":
        raise HTTPException(400, "Select a savings account in the same currency as the goal")
    if not body.amount:
        raise HTTPException(400, "Can't allocate a zero amount")
    entry = GoalAllocation(
        goal_id=gid,
        account_id=a.id,
        amount_cents=finance.to_cents(body.amount),
        date=Date.today(),
        user_id=user.id,
        client_ref=body.client_ref,
    )
    db.add(entry)
    audit.record(db, user.id, "allocation", entry, "create")
    finish(db)
    return {"id": entry.id}
