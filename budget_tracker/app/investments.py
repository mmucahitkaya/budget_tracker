"""Investment calculations: quantity held, average cost, profit/loss, current value and history.

All amounts (lot prices, values, costs) are in the household's base currency.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import prefs, prices
from .fx import Converter
from .models import Asset, AssetLot, PortfolioSnapshot, SavingsGoal

KIND_LABELS = {"gold": "Gold", "silver": "Silver", "metal": "Other metal", "fx": "Foreign currency", "stock": "Stock", "fund": "Fund"}
STALE_AFTER = timedelta(days=3)  # markets are closed on weekends; a price older than 3 days counts as "stale"


class LotError(ValueError):
    pass


def position(lots: list[AssetLot], until: date | None = None) -> tuple[float, int]:
    """(quantity held, remaining cost in cents) — average cost method.

    A sale is deducted from cost at the average cost up to that point. Selling more than held raises LotError.
    """
    qty, cost = 0.0, 0.0
    for lot in sorted(lots, key=lambda x: (x.date, x.id or 0)):
        if until and lot.date > until:
            break
        if lot.side == "buy":
            qty += lot.quantity
            cost += lot.quantity * lot.unit_price_cents
        else:
            if lot.quantity > qty + 1e-9:
                raise LotError(f"Cannot sell {lot.quantity:g} on {lot.date:%Y-%m-%d} when only {qty:g} is held")
            avg = cost / qty if qty else 0
            qty -= lot.quantity
            cost -= avg * lot.quantity
    if abs(qty) < 1e-9:
        qty, cost = 0.0, 0.0
    return qty, round(cost)


def realized(lots: list[AssetLot]) -> int:
    """Realized profit/loss from sales (cents)."""
    qty, cost, gain = 0.0, 0.0, 0.0
    for lot in sorted(lots, key=lambda x: (x.date, x.id or 0)):
        if lot.side == "buy":
            qty += lot.quantity
            cost += lot.quantity * lot.unit_price_cents
        elif qty:
            avg = cost / qty
            gain += (lot.unit_price_cents - avg) * lot.quantity
            qty -= lot.quantity
            cost -= avg * lot.quantity
    return round(gain)


def asset_out(db: Session, a: Asset, today: date | None = None) -> dict:
    today = today or date.today()
    lots = list(db.scalars(select(AssetLot).where(AssetLot.asset_id == a.id)))
    out = {
        "id": a.id,
        "kind": a.kind,
        "kind_label": KIND_LABELS.get(a.kind, a.kind),
        "code": a.code,
        "name": a.name,
        "unit": prices.unit_for(a.kind, a.code),
        "owner_id": a.owner_id,
        "goal_id": a.goal_id,
        "note": a.note,
        "location": a.location or "",
        "archived": a.archived,
        "lots": [
            {
                "id": lot.id,
                "side": lot.side,
                "date": lot.date.isoformat(),
                "quantity": lot.quantity,
                "unit_price": lot.unit_price_cents / 100,
                "total": round(lot.quantity * lot.unit_price_cents) / 100,
                "user_id": lot.user_id,
                "note": lot.note,
            }
            for lot in sorted(lots, key=lambda x: (x.date, x.id), reverse=True)
        ],
    }
    if a.kind == "fund":
        value = a.manual_value_cents or 0
        cost = a.manual_cost_cents
        out.update(
            quantity=None,
            price=None,
            price_date=a.manual_value_date.isoformat() if a.manual_value_date else None,
            price_source="entered manually",
            stale=bool(a.manual_value_date and today - a.manual_value_date > timedelta(days=45)),
            value=value / 100,
            cost=cost / 100 if cost is not None else None,
            pl=(value - cost) / 100 if cost else None,
            pl_pct=round((value - cost) / cost, 4) if cost else None,
            realized=0,
            missing_price=False,
        )
        return out
    qty, cost = position(lots)
    item = prices.catalog_item(a.kind, a.code) if a.kind in ("gold", "silver", "metal") else None
    if item:
        # Total weight regardless of coin/karat: gross grams and pure (fine) grams
        out.update(gross_gram=round(qty * item["gram"], 3), pure_gram=round(qty * item["gram"] * item["purity"], 3))
    p = prices.latest(db, a.kind, a.code)
    value = round(qty * p.price_cents) if p else 0
    out.update(
        quantity=round(qty, 6),
        price=p.price_cents / 100 if p else None,
        price_date=p.fetched_at.isoformat(timespec="minutes") if p else None,
        price_source=p.source if p else None,
        stale=bool(p and datetime.now() - p.fetched_at > STALE_AFTER),
        value=value / 100,
        cost=cost / 100,
        pl=(value - cost) / 100 if p and cost else None,
        pl_pct=round((value - cost) / cost, 4) if p and cost else None,
        realized=realized(lots) / 100,
        missing_price=p is None and qty > 0,
    )
    return out


def overview(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    assets = [asset_out(db, a, today) for a in db.scalars(select(Asset).order_by(Asset.kind, Asset.name))]
    active = [a for a in assets if not a["archived"]]
    by_kind, by_owner = defaultdict(float), defaultdict(float)
    for a in active:
        by_kind[a["kind"]] += a["value"]
        by_owner[a["owner_id"]] += a["value"]
    value = sum(a["value"] for a in active)
    cost = sum(a["cost"] or 0 for a in active if a["pl"] is not None)
    priced_value = sum(a["value"] for a in active if a["pl"] is not None)
    metals = {}
    for kind in ("gold", "silver"):
        rows = [a for a in active if a["kind"] == kind and a.get("gross_gram") is not None]
        ref = prices.base_item_code(kind)
        if not rows or not ref:
            continue
        pure = sum(a["pure_gram"] for a in rows)
        base_price = prices.latest(db, kind, ref)
        base_item = prices.catalog_item(kind, ref)
        metals[kind] = {
            "gross_gram": round(sum(a["gross_gram"] for a in rows), 3),
            "pure_gram": round(pure, 3),
            "value": round(sum(a["value"] for a in rows), 2),
            # Equivalent at the gram price of gold of the same purity (to see the coin workmanship premium)
            "base_equivalent": round(pure / base_item["purity"] * base_price.price_cents / 100, 2) if base_price else None,
            "base_name": base_item["name"],
        }
    return {
        "assets": assets,
        "metals": metals,
        "value": round(value, 2),
        "cost": round(cost, 2),
        "pl": round(priced_value - cost, 2) if cost else None,
        "pl_pct": round((priced_value - cost) / cost, 4) if cost else None,
        "by_kind": [{"kind": k, "label": KIND_LABELS[k], "value": round(v, 2)} for k, v in sorted(by_kind.items(), key=lambda x: -x[1])],
        "by_owner": [{"user_id": k, "value": round(v, 2)} for k, v in sorted(by_owner.items(), key=lambda x: -x[1])],
        "missing_price": [a["name"] for a in active if a["missing_price"]],
        "history": history(db),
        "catalog": prices.catalog(),
        "currency": prefs.base(),
    }


def total_value_cents(db: Session, goal_id: int | None = None) -> int:
    """Base-currency value of active investments (only those linked to that goal if goal_id is given)."""
    q = select(Asset).where(Asset.archived.is_(False))
    if goal_id is not None:
        q = q.where(Asset.goal_id == goal_id)
    return round(sum(asset_out(db, a)["value"] * 100 for a in db.scalars(q)))


def values_by_goal(db: Session) -> dict[int | None, int]:
    """Base-currency value of active investments by goal (None = not linked); each asset is computed once."""
    out: dict[int | None, int] = defaultdict(int)
    for a in db.scalars(select(Asset).where(Asset.archived.is_(False))):
        out[a.goal_id] += round(asset_out(db, a)["value"] * 100)
    return dict(out)


def goal_value_cents(db: Session, goal: SavingsGoal, today: date, by_goal: dict[int | None, int] | None = None) -> int:
    """Value of goal-linked investments in the goal's currency."""
    base_cents = by_goal.get(goal.id, 0) if by_goal is not None else total_value_cents(db, goal.id)
    if goal.currency == prefs.base() or not base_cents:
        return base_cents
    rate_cents = Converter(db).to_base_cents(100, goal.currency, today)  # 1 unit = ? base cents
    return round(base_cents * 100 / rate_cents) if rate_cents else 0


def snapshot(db: Session, on: date | None = None) -> int:
    """Records today's asset values (updates them if called again on the same day)."""
    on = on or date.today()
    n = 0
    for a in db.scalars(select(Asset).where(Asset.archived.is_(False))):
        out = asset_out(db, a, on)
        db.merge(PortfolioSnapshot(date=on, asset_id=a.id, value_cents=round(out["value"] * 100),
                                   cost_cents=round((out["cost"] or 0) * 100)))
        n += 1
    db.commit()
    return n


def history(db: Session, days: int = 365) -> list[dict]:
    since = date.today() - timedelta(days=days)
    totals: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    for s in db.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.date >= since)):
        totals[s.date][0] += s.value_cents
        totals[s.date][1] += s.cost_cents
    return [{"date": d.isoformat(), "value": v / 100, "cost": c / 100} for d, (v, c) in sorted(totals.items())]


def needed_prices(db: Session) -> set[tuple[str, str]]:
    from .models import RecurringPayment

    out = {(a.kind, a.code) for a in db.scalars(select(Asset).where(Asset.archived.is_(False), Asset.kind != "fund"))}
    for r in db.scalars(select(RecurringPayment).where(RecurringPayment.active.is_(True), RecurringPayment.asset_code != "")):
        out.add(prices.split_asset_code(r.asset_code))
    return out
