"""Investments: precious metals, foreign currency, stocks and funds (valued in the base currency)."""
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Literal

from .. import audit, investments, prefs, prices
from ..auth import current_user
from ..db import get_db
from ..models import Asset, AssetLot, SavingsGoal, User

router = APIRouter(prefix="/api/investments", tags=["investments"])

Kind = Literal["gold", "silver", "metal", "fx", "stock", "fund"]
_last_refresh = 0.0


class AssetIn(BaseModel):
    kind: Kind
    code: str = Field("", max_length=12)
    name: str = Field("", max_length=80)
    owner_id: int | None = None
    goal_id: int | None = None
    note: str = Field("", max_length=200)
    location: str = Field("", max_length=40)  # empty = physical
    # Funds only
    value: float | None = Field(None, ge=0, le=1_000_000_000)
    cost: float | None = Field(None, ge=0, le=1_000_000_000)
    archived: bool = False


class LotIn(BaseModel):
    side: Literal["buy", "sell"] = "buy"
    date: date
    quantity: float = Field(gt=0, le=1_000_000_000)
    unit_price: float = Field(ge=0, le=1_000_000_000)
    note: str = Field("", max_length=200)
    client_ref: str | None = Field(None, max_length=64)


def _check_refs(db: Session, body: AssetIn) -> None:
    if body.owner_id is not None and db.get(User, body.owner_id) is None:
        raise HTTPException(400, "Person not found")
    if body.goal_id is not None:
        g = db.get(SavingsGoal, body.goal_id)
        if g is None or g.archived:
            raise HTTPException(400, "Savings goal not found")


def _normalize(db: Session, body: AssetIn) -> tuple[str, str]:
    """(code, name) — validates according to the kind."""
    code = body.code.strip().upper()
    if body.kind == "fund":
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Fund name is required")
        if body.value is None:
            raise HTTPException(400, "Enter the current value of the fund")
        return code, name
    if body.kind == "stock":
        code = prices.normalize_ticker(code)
        if not code:
            raise HTTPException(400, "Enter a valid ticker (e.g. AAPL, SAP.DE)")
        try:
            price = prices.fetch_stock(db, code)
        except prices.SymbolNotFound:
            raise HTTPException(400, f"{code} was not found")
        except Exception:
            price = None  # network issue / no FX rate: don't block saving, the price comes later
        if price is not None:
            prices.store(db, "stock", code, price, prices.stock_source(code))
        return code, body.name.strip() or code
    item = prices.catalog_item(body.kind, code, active_only=True)
    if item is None:
        raise HTTPException(400, "Asset code is not in the list")
    return code, body.name.strip() or item["name"]


@router.get("")
def get_investments(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return investments.overview(db)


@router.get("/catalog")
def get_catalog(_: User = Depends(current_user)):
    """The active asset catalog (depends on the region pack and base currency)."""
    return {"currency": prefs.base(), "region": prefs.region(), "catalog": prices.catalog()}


@router.post("/assets")
def create_asset(body: AssetIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _check_refs(db, body)
    code, name = _normalize(db, body)
    a = Asset(kind=body.kind, code=code, name=name, owner_id=body.owner_id, goal_id=body.goal_id, note=body.note,
              location=body.location.strip())
    if body.kind == "fund":
        a.manual_value_cents = round(body.value * 100)
        a.manual_value_date = date.today()
        a.manual_cost_cents = round(body.cost * 100) if body.cost is not None else None
    db.add(a)
    db.flush()
    if body.kind in ("gold", "silver", "metal", "fx") and prices.latest(db, body.kind, code) is None:
        prices.refresh(db, {(body.kind, code)})
    audit.record(db, user.id, "asset", a, "create")
    db.commit()
    return investments.asset_out(db, a)


@router.put("/assets/{asset_id}")
def update_asset(asset_id: int, body: AssetIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404)
    if body.kind != a.kind:
        raise HTTPException(400, "Asset kind can't be changed; add a new asset")
    _check_refs(db, body)
    before = audit.snapshot(a)
    a.owner_id, a.goal_id, a.note, a.archived = body.owner_id, body.goal_id, body.note, body.archived
    a.location = body.location.strip()
    if body.name.strip():
        a.name = body.name.strip()
    if a.kind == "fund":
        if body.value is not None and round(body.value * 100) != a.manual_value_cents:
            a.manual_value_cents = round(body.value * 100)
            a.manual_value_date = date.today()
        a.manual_cost_cents = round(body.cost * 100) if body.cost is not None else None
    audit.record(db, user.id, "asset", a, "update", before)
    db.commit()
    return investments.asset_out(db, a)


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Archived if it has buy/sell records (history is kept), otherwise deleted."""
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404)
    has_lots = db.scalar(select(AssetLot.id).where(AssetLot.asset_id == a.id).limit(1)) is not None
    if has_lots:
        before = audit.snapshot(a)
        a.archived = True
        audit.record(db, user.id, "asset", a, "update", before)
    else:
        audit.record(db, user.id, "asset", a, "delete", audit.snapshot(a))
        db.delete(a)
    db.commit()
    return {"ok": True, "archived": has_lots}


@router.post("/assets/{asset_id}/lots")
def add_lot(asset_id: int, body: LotIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = db.get(Asset, asset_id)
    if not a or a.archived:
        raise HTTPException(404)
    if a.kind == "fund":
        raise HTTPException(400, "For funds, edit the current value instead of adding buys/sells")
    if body.date > date.today():
        raise HTTPException(400, "Future-dated entries aren't allowed")
    if body.client_ref:
        existing = db.scalar(select(AssetLot).where(AssetLot.client_ref == body.client_ref))
        if existing:
            # Resubmission: returns the asset the entry is actually linked to
            return investments.asset_out(db, db.get(Asset, existing.asset_id))
    lot = AssetLot(asset_id=a.id, side=body.side, date=body.date, quantity=body.quantity,
                   unit_price_cents=round(body.unit_price * 100), user_id=user.id, note=body.note,
                   client_ref=body.client_ref)
    db.add(lot)
    db.flush()
    try:
        investments.position(list(db.scalars(select(AssetLot).where(AssetLot.asset_id == a.id))))
    except investments.LotError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    audit.record(db, user.id, "lot", lot, "create")
    db.commit()
    return investments.asset_out(db, a)


@router.delete("/lots/{lot_id}")
def delete_lot(lot_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lot = db.get(AssetLot, lot_id)
    if not lot:
        raise HTTPException(404)
    a = db.get(Asset, lot.asset_id)
    rest = [x for x in db.scalars(select(AssetLot).where(AssetLot.asset_id == a.id)) if x.id != lot.id]
    try:
        investments.position(rest)  # after deleting a buy, later sells must not exceed holdings
    except investments.LotError as e:
        raise HTTPException(400, f"This buy can't be deleted: {e}")
    audit.record(db, user.id, "lot", lot, "delete", audit.snapshot(lot))
    db.delete(lot)
    db.commit()
    return investments.asset_out(db, a)


@router.post("/refresh")
def refresh_prices(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Refreshes prices now (at most once a minute)."""
    global _last_refresh
    if time.time() - _last_refresh < 60:
        raise HTTPException(429, "Prices were just updated; try again in a minute")
    _last_refresh = time.time()
    result = prices.refresh(db, investments.needed_prices(db))
    investments.snapshot(db)
    return result
