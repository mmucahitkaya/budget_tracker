"""Investment buys/sells via Telegram: "10g gold 650", "bought 500 euros 540 usd", "AAPL 10 shares",
and (region pack "tr") the Turkish forms "10gr altın", "3 çeyrek 15000tl", "500 dolar aldım 24600tl", "THYAO 10 lot".

Amounts are in the household's base currency.

Buying gold isn't an expense; these messages are recorded as buys/sells under Investments instead.
Since a misreading would break the valuation, what was understood is shown first and saved on confirmation.
"""
import re
import secrets
import time
import unicodedata
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, finance, investments, prefs, prices
from .models import Asset, AssetLot, User


GIFT_NOTE = "Wedding gifts"


def _plain(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower().replace("ı", "i").replace("İ", "i"))
    return "".join(ch for ch in t if not unicodedata.combining(ch))


# (pattern, kind, code) — first match wins; more specific ones first.
# Region pack "tr": Turkish coins and karats (Grand Bazaar catalog).
TR_METALS = [
    (r"\bceyrek", "gold", "CEYREKALTIN"),
    (r"\byarim\b", "gold", "YARIMALTIN"),
    (r"\bcumhuriyet", "gold", "CUMHURIYETALTINI"),
    (r"\bata\b", "gold", "ATAALTIN"),
    (r"\btam\b", "gold", "TAMALTIN"),
    (r"\bbilezik|\b22\s*ayar", "gold", "YIA"),
    (r"\b18\s*ayar", "gold", "18AYARALTIN"),
    (r"\b14\s*ayar", "gold", "14AYARALTIN"),
    (r"\bgumus|\bsilver\b", "silver", "GUMUS"),
    (r"\bpaladyum|\bpalladium\b", "metal", "PAL"),
    (r"\bplatin|\bplatinum\b", "metal", "GPL"),
    (r"\baltin|\bgold\b", "gold", "GRA"),
]
METALS = TR_METALS  # backwards-compatible name
# Generic catalog: gram by default, troy ounces when "oz"/"ounce" is mentioned
OUNCE = re.compile(r"\d\s*(?:oz|ozt)\b|\b(?:oz|ozt|ounces?|troy)\b")
GENERIC_METALS = [
    (r"\bsilver\b|\bgumus|\bsilber|\bargent\b|\bplata\b", "silver", "XAG"),
    (r"\bpalladium\b|\bpaladyum", "metal", "XPD"),
    (r"\bplatinum\b|\bplatin", "metal", "XPT"),
    (r"\bgold\b|\baltin|\boro\b", "gold", "XAU"),
]
FX_WORDS = [
    (r"\$|\bdolar|\bdollars?\b|\busd\b", "USD"),
    (r"€|\beuro?s?\b|\beur\b|\bavro\b", "EUR"),
    (r"\bsterlin|\bpounds?\b|\bgbp\b|£", "GBP"),
    (r"\bfrank|\bfrancs?\b|\bchf\b", "CHF"),
    (r"\byen\b|\bjpy\b|¥", "JPY"),
    (r"\blira\b|\btl\b|\btry\b|₺", "TRY"),
]
# Currency symbols accepted next to a total in the base currency
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "TRY": "₺", "INR": "₹", "KRW": "₩", "ILS": "₪", "PHP": "₱"}
BASE_WORDS = {"TRY": ["tl", "lira"], "USD": ["dollars?", "bucks"], "EUR": ["euros?"], "GBP": ["pounds?", "quid"]}
TRADE = re.compile(r"\b(aldim|aldik|alim|sattim|sattik|satis|bozdurdum|bozdurduk|bought|buy|sold|sell|exchanged)\b")
SELL = re.compile(r"\b(sattim|sattik|satis|bozdurdum|bozdurduk|sold|sell|exchanged)\b")
NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,4})?|\d+(?:[.,]\d{1,4})?"


def _total_re() -> re.Pattern:
    """Total amount in the base currency: "15000tl", "₺15.000", "650 usd", "$650", "650 dollars"."""
    base = prefs.base()
    words = [base.lower(), *BASE_WORDS.get(base, [])]
    sym = re.escape(SYMBOLS.get(base, "")) if base in SYMBOLS else None
    after = "|".join(words) + (f"|{sym}" if sym else "")
    pat = rf"(?:{sym}\s*)?({NUM})\s*(?:{after})(?![a-z])" if sym else rf"({NUM})\s*(?:{after})(?![a-z])"
    if sym:
        pat += rf"|{sym}\s*({NUM})"
    return re.compile(pat)


# Unit price: "tanesi 10500", "gramı 6500", "birim 10500", "each 10500", "per gram 6500"
UNIT = re.compile(rf"\b(?:tanesi|tane|grami|gram fiyati|birim|adedi|lotu|each|per gram|per share|per unit|unit price)\s*:?\s*({NUM})")
STOCK = re.compile(r"\b([A-Z]{4,5}(?:\.[A-Z]{1,3}\b)?)\b")
# Common words that must not be mistaken for stock codes (unaccented, upper case)
NOT_STOCK = {
    "HISSE", "ALDIM", "ALDIK", "ALIM", "SATIM", "SATIS", "FIYAT", "BUGUN", "DUN", "ADET", "TANE", "TANES",
    "LOTU", "BIRIM", "SATTI", "TOPLA", "LIRA", "DOLAR", "EURO", "BORSA", "YARIN", "SABAH", "AKSAM", "HEPSI",
    "BUNU", "BUNLA", "SONRA", "ONCE", "DAHA", "BIRAZ", "COK", "AZ", "ICIN", "KADAR", "ILE", "HEMEN", "YENI",
    "SHARE", "STOCK", "SOLD", "SELL", "PRICE", "TODAY", "EACH", "UNIT", "TOTAL", "WORTH", "FROM", "WITH", "THESE",
    "SHARES", "BOUGHT", "OF", "AT", "IN", "ON", "FOR", "THE", "AND", "MY", "SOME", "MORE", "EUROS", "EURO", "DOLLARS",
    "USD", "EUR", "GBP", "TRY", "TL", "PER", "BUY", "GOT",
}
# "THYAO 10 lot" / "10 shares THYAO": the word right next to the share count
STOCK_NEAR_LOT = re.compile(
    rf"\b([a-z]{{2,5}}(?:\.[a-z]{{1,3}})?)\s+(?:{NUM})\s*(?:lot|hisse|shares?)\b"
    rf"|(?:{NUM})\s*(?:lot|hisse|shares?)\s+(?:of\s+)?([a-z]{{2,5}}(?:\.[a-z]{{1,3}})?)(?![a-z.])"
)
# Jewelry/items that must not be mistaken for investments (expenses: "altın küpe aldım", "silver necklace")
JEWELRY = re.compile(r"\b(kupe|kolye|yuzuk|zincir|bileklik|broş|bros|saat|tespih|kalem|catal|kasik|tepsi|cerceve|hediye"
                     r"|(?:earrings?|necklace|rings?|chain|bracelet|watch|pen|spoon|fork|tray|frame|gift)\b)")


def _num(s: str) -> float:
    return finance.parse_amount(s)


def _stock_code(plain: str, known: set[str]) -> str | None:
    """Stock code in the message: known codes first, then the word next to the share count, then the first candidate."""
    words = [w for w in STOCK.findall(plain.upper()) if w not in NOT_STOCK]
    for w in words:
        if w in known:
            return w
    for m in STOCK_NEAR_LOT.finditer(plain):
        w = (m.group(1) or m.group(2)).upper()
        if w not in NOT_STOCK:
            return w
    return words[0] if words else None


def _metal(plain: str) -> tuple[str, str] | None:
    """(kind, code) of a precious metal mentioned in the message, using the active catalog."""
    if prices.is_tr():
        for pat, k, c in TR_METALS:
            if re.search(pat, plain):
                return k, c
        return None
    for pat, k, prefix in GENERIC_METALS:
        if re.search(pat, plain):
            if prefix in ("XPT", "XPD"):
                return k, f"{prefix}_G"
            return k, f"{prefix}_OZ" if OUNCE.search(plain) else f"{prefix}_G"
    return None


def parse_investment(text: str, known_stocks: set[str] | None = None) -> dict | None:
    """{kind, code, side, quantity, unit_price|None, total|None} if this is an investment message, else None."""
    plain = _plain(text)
    if JEWELRY.search(plain):
        return None  # buying gold/silver jewelry is recorded as an expense
    kind = code = None
    # Stock: "THYAO 10 lot" / "10 shares THYAO" / "hisse"
    if re.search(r"\b(lot|hisse|shares?)\b", plain):
        code = _stock_code(plain, known_stocks or set())
        if code:
            kind, code = "stock", prices.normalize_ticker(code) or code
    if kind is None:
        metal = _metal(plain)
        if metal:
            kind, code = metal
    if kind is None and TRADE.search(plain):
        base = prefs.base()
        for pat, c in FX_WORDS:
            if c != base and re.search(pat, plain):
                kind, code = "fx", c
                break
    if kind is None:
        return None
    # The word "tam" alone: without gold context (e.g. "tam 5 kilo") it is not an investment
    if code == "TAMALTIN" and not re.search(r"\baltin|\bceyrek|\byarim", plain) and not re.search(r"\d+\s*tam\b", plain):
        return None

    work = plain
    total = unit_price = None
    t = _total_re().search(work)
    if t:
        total = _num(t.group(1) or t.group(2))
        work = work[: t.start()] + " " + work[t.end():]
    u = UNIT.search(work)
    if u:
        unit_price = _num(u.group(1))
        work = work[: u.start()] + " " + work[u.end():]
    # Quantity: first remaining number ("10gr", "3 ceyrek", "500 euros"); karat numbers (22/18/14 ayar) excluded
    work = re.sub(r"\b(22|18|14)\s*ayar", " ", work)
    nums = re.findall(rf"(?<![\d.,])({NUM})", work)
    if not nums:
        return None
    quantity = _num(nums[0])
    if total is None and unit_price is None and len(nums) >= 2:
        total = _num(nums[1])  # "3 ceyrek 15000" → second number is the total
    if quantity <= 0:
        return None
    if total is not None and unit_price is None:
        unit_price = total / quantity
    return {
        "kind": kind,
        "code": code,
        "side": "sell" if SELL.search(plain) else "buy",
        "quantity": quantity,
        "unit_price": round(unit_price, 4) if unit_price is not None else None,
    }


# ---- Pending confirmation ------------------------------------------------------------

_pending: dict[str, tuple[dict, float]] = {}
PENDING_TTL = 600


def remember(payload: dict) -> str:
    now = time.time()
    for k, (_, exp) in list(_pending.items()):
        if exp < now:
            del _pending[k]
    token = secrets.token_hex(4)
    _pending[token] = (payload, now + PENDING_TTL)
    return token


def take(token: str) -> dict | None:
    entry = _pending.pop(token, None)
    return entry[0] if entry and entry[1] >= time.time() else None


def describe(db: Session, q: dict) -> tuple[str, dict]:
    """Summary shown to the user and the data to save (current price if none given)."""
    item = prices.catalog_item(q["kind"], q["code"])
    name = item["name"] if item else q["code"]
    unit = prices.unit_for(q["kind"], q["code"])
    price = q["unit_price"]
    note = ""
    if price is None:
        latest = prices.latest(db, q["kind"], q["code"])
        # Don't block the bot loop: single attempt, short timeout
        if latest is None and q["kind"] != "stock":
            prices.refresh(db, {(q["kind"], q["code"])}, attempts=1, timeout=8)
            latest = prices.latest(db, q["kind"], q["code"])
        if latest is None and q["kind"] == "stock":
            try:
                prices.store(db, "stock", q["code"], prices.fetch_stock(db, q["code"], timeout=8), prices.stock_source(q["code"]))
                db.commit()
                latest = prices.latest(db, "stock", q["code"])
            except Exception:
                latest = None
        if latest is None:
            return "", {}
        price = latest.price_cents / 100
        note = " (current buy price, since no price was given)"
    qty_txt = f"{q['quantity']:g}"
    gram = ""
    if item and item.get("gram") and unit != "gram":
        gram = f" ≈ {q['quantity'] * item['gram']:.2f} g"
    side = "purchase" if q["side"] == "buy" else "sale"
    text = (
        f"🪙 <b>{name} {side}</b>\n"
        f"{qty_txt} {unit}{gram} × {_tl(price)} = <b>{_tl(price * q['quantity'])}</b>{note}"
    )
    return text, {**q, "unit_price": price, "name": name}


def _tl(v: float) -> str:
    """Human-readable amount in the base currency, e.g. "1,234.00 USD"."""
    return finance.format_money(round(v * 100), prefs.base())


def apply(db: Session, user: User, data: dict, on: date) -> str:
    """Finds the asset (the user's or a shared one) or creates it, and adds the buy/sell."""
    # Gifted jewelry (note: GIFT_NOTE) has no cost basis; keep new purchases separate from it
    base = select(Asset).where(
        Asset.kind == data["kind"], Asset.code == data["code"], Asset.archived.is_(False), Asset.note != GIFT_NOTE,
        Asset.location == "",  # Purchases entered via Telegram count as physical; keep them separate from bank-held ones
    )
    asset = db.scalar(base.where(Asset.owner_id == user.id)) or db.scalar(base.where(Asset.owner_id.is_(None)))
    if asset is None:
        if data["side"] == "sell":
            raise investments.LotError(f"You have no {data['name']} recorded")
        asset = Asset(kind=data["kind"], code=data["code"], name=data["name"], owner_id=user.id)
        db.add(asset)
        db.flush()
        audit.record(db, user.id, "asset", asset, "create")
    lot = AssetLot(asset_id=asset.id, side=data["side"], date=on, quantity=data["quantity"],
                   unit_price_cents=round(data["unit_price"] * 100), user_id=user.id, note="Telegram")
    db.add(lot)
    db.flush()
    lots = list(db.scalars(select(AssetLot).where(AssetLot.asset_id == asset.id)))
    try:
        qty, _ = investments.position(lots)
    except investments.LotError:
        db.rollback()
        raise
    audit.record(db, user.id, "lot", lot, "create")
    db.commit()
    out = investments.asset_out(db, asset)
    unit = out["unit"]
    return f"✅ {asset.name}: holding {qty:g} {unit} · current value {_tl(out['value'])}"


# ---- Fund updates --------------------------------------------------------------------
# Fund prices can't be fetched automatically, so the owner updates them monthly via Telegram:
# "HPH 34150.20" (fund code + current value, in the base currency).

FUND_UPDATE = re.compile(rf"^\s*(?:(?:fon|fund)\s+)?([A-Za-zÇĞİÖŞÜçğıöşü0-9]{{2,8}})\s*[:=]?\s*({NUM})\s*(?:tl|lira|[a-z]{{3}}|[₺$€£¥])?\s*$", re.I)


def user_funds(db: Session, user: User) -> list[Asset]:
    """Active funds owned by the user or shared."""
    return list(
        db.scalars(
            select(Asset)
            .where(Asset.kind == "fund", Asset.archived.is_(False))
            .where((Asset.owner_id == user.id) | (Asset.owner_id.is_(None)))
            .order_by(Asset.name)
        )
    )


def match_fund_update(db: Session, user: User, text: str) -> tuple[Asset, float] | None:
    """(fund, new value) if the message is a fund update like "HPH 34150.20"; otherwise None."""
    m = FUND_UPDATE.match(text)
    if not m:
        return None
    code = m.group(1).upper().replace("İ", "I")
    for fund in user_funds(db, user):
        if fund.code and fund.code.upper() == code:
            return fund, _num(m.group(2))
    return None


def update_fund(db: Session, user: User, fund: Asset, value: float) -> tuple[str, dict]:
    """Updates the fund value. Returns: (message, undo info)."""
    before = audit.snapshot(fund)
    old_value, old_date = fund.manual_value_cents, fund.manual_value_date
    fund.manual_value_cents = round(value * 100)
    fund.manual_value_date = date.today()
    audit.record(db, user.id, "asset", fund, "update", before)
    db.commit()
    out = investments.asset_out(db, fund)
    lines = [f"✅ <b>{fund.name}</b>: {_tl((old_value or 0) / 100)} → <b>{_tl(value)}</b>"]
    if old_value:
        diff = value - old_value / 100
        since = f" (since {old_date:%Y-%m-%d})" if old_date else ""
        lines.append(f"Change: {'+' if diff >= 0 else '−'}{_tl(abs(diff))}{since}")
    if out["pl"] is not None:
        lines.append(f"Vs. cost: {'+' if out['pl'] >= 0 else '−'}{_tl(abs(out['pl']))} ({out['pl_pct'] * 100:.1f}%)")
    undo = {"fund_id": fund.id, "value": old_value, "date": old_date.isoformat() if old_date else None}
    return "\n".join(lines), undo


def undo_fund(db: Session, user: User, data: dict) -> str:
    fund = db.get(Asset, data["fund_id"])
    if not fund:
        return "Fund not found"
    before = audit.snapshot(fund)
    fund.manual_value_cents = data["value"]
    fund.manual_value_date = date.fromisoformat(data["date"]) if data["date"] else None
    audit.record(db, user.id, "asset", fund, "update", before)
    db.commit()
    return f"↩️ {fund.name} reverted to the previous value: {_tl((data['value'] or 0) / 100)}"


def fund_reminder(db: Session, user: User) -> str | None:
    funds = user_funds(db, user)
    if not funds:
        return None
    lines = ["📈 <b>Monthly fund update</b>", "Send the current value of your funds:"]
    for f in funds:
        when = f.manual_value_date.strftime("%Y-%m-%d") if f.manual_value_date else "never"
        lines.append(f"• {f.name}: {_tl((f.manual_value_cents or 0) / 100)} (last: {when})")
    example = funds[0].code or "HPH"
    lines.append(f"\nExample: <code>{example} 34150.20</code> (one message per fund)")
    return "\n".join(lines)


def known_stock_codes(db: Session) -> set[str]:
    """Known stock codes (assets and price history): preferred when picking a code from a message."""
    from .models import AssetPrice

    codes = {a.code for a in db.scalars(select(Asset).where(Asset.kind == "stock"))}
    codes |= {k.partition(":")[2] for k in db.scalars(select(AssetPrice.key).where(AssetPrice.key.like("stock:%")).distinct())}
    return codes
