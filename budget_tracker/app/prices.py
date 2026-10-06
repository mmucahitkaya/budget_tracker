"""Investment prices, valued in the household's base currency.

Generic catalog (any region): gold/silver/platinum/palladium from Yahoo futures (USD per troy ounce), foreign
currencies from ECB rates (Frankfurter) and any Yahoo ticker (AAPL, SAP.DE, THYAO.IS) in its quote currency, all
converted to the base currency.

Region pack "tr" (Turkey): Turkish gold coins and Grand Bazaar prices (truncgil, TRY, buying side), TCMB rates as
the FX fallback and Borsa Istanbul as the default exchange for bare tickers (THYAO → THYAO.IS).

Sources are unofficial; if a response is broken or unreachable, the last known price is used with its date.
"""
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import prefs
from .models import AssetPrice, FxRate

log = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"}
TRUNCGIL = "https://finans.truncgil.com/v4/today.json"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
TCMB = "https://www.tcmb.gov.tr/kurlar/today.xml"
FRANKFURTER = "https://api.frankfurter.app/latest?base={base}&symbols={symbols}"

TROY_OUNCE_G = 31.1034768

# ---- Catalogs ----------------------------------------------------------------------

# (code, name, unit, gross grams per unit, fineness) — Turkish Mint standards
TR_GOLD = [
    ("GRA", "Gram gold (24k)", "gram", 1.0, 0.995),
    ("CEYREKALTIN", "Quarter gold coin", "piece", 1.754, 0.916),
    ("YARIMALTIN", "Half gold coin", "piece", 3.508, 0.916),
    ("TAMALTIN", "Full gold coin", "piece", 7.016, 0.916),
    ("CUMHURIYETALTINI", "Republic gold coin", "piece", 7.216, 0.916),
    ("ATAALTIN", "Ata gold coin", "piece", 7.216, 0.916),
    ("YIA", "22k gold bangle", "gram", 1.0, 0.916),
    ("18AYARALTIN", "18k gold", "gram", 1.0, 0.750),
    ("14AYARALTIN", "14k gold", "gram", 1.0, 0.585),
]
TR_SILVER = [("GUMUS", "Gram silver", "gram", 1.0, 1.0)]
TR_METALS = [("PAL", "Gram palladium", "gram", 1.0, 1.0), ("GPL", "Gram platinum", "gram", 1.0, 1.0)]
TR_FX = ["USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "SAR", "AED", "SEK", "NOK", "DKK", "CNY", "RUB"]

GENERIC_GOLD = [("XAU_G", "Gold, gram", "gram", 1.0, 1.0), ("XAU_OZ", "Gold, troy ounce", "oz", TROY_OUNCE_G, 1.0)]
GENERIC_SILVER = [("XAG_G", "Silver, gram", "gram", 1.0, 1.0), ("XAG_OZ", "Silver, troy ounce", "oz", TROY_OUNCE_G, 1.0)]
GENERIC_METALS = [("XPT_G", "Platinum, gram", "gram", 1.0, 1.0), ("XPD_G", "Palladium, gram", "gram", 1.0, 1.0)]
# Generic metal code → Yahoo futures symbol (USD per troy ounce)
FUTURES = {"XAU_G": "GC=F", "XAU_OZ": "GC=F", "XAG_G": "SI=F", "XAG_OZ": "SI=F", "XPT_G": "PL=F", "XPD_G": "PA=F"}

CURRENCY_NAMES = {
    "AUD": "Australian dollar", "BGN": "Bulgarian lev", "BRL": "Brazilian real", "CAD": "Canadian dollar",
    "CHF": "Swiss franc", "CNY": "Chinese yuan", "CZK": "Czech koruna", "DKK": "Danish krone", "EUR": "Euro",
    "GBP": "British pound", "HKD": "Hong Kong dollar", "HUF": "Hungarian forint", "IDR": "Indonesian rupiah",
    "ILS": "Israeli shekel", "INR": "Indian rupee", "ISK": "Icelandic króna", "JPY": "Japanese yen",
    "KRW": "South Korean won", "MXN": "Mexican peso", "MYR": "Malaysian ringgit", "NOK": "Norwegian krone",
    "NZD": "New Zealand dollar", "PHP": "Philippine peso", "PLN": "Polish złoty", "RON": "Romanian leu",
    "SEK": "Swedish krona", "SGD": "Singapore dollar", "THB": "Thai baht", "TRY": "Turkish lira", "USD": "US dollar",
    "ZAR": "South African rand", "SAR": "Saudi riyal", "AED": "UAE dirham", "RUB": "Russian ruble",
}

# "Gold" reference item per metal kind (overview: equivalent at the plain gram price)
TR_BASE_ITEMS = {"gold": "GRA", "silver": "GUMUS"}
GENERIC_BASE_ITEMS = {"gold": "XAU_G", "silver": "XAG_G"}


def _items(rows) -> list[dict]:
    return [{"code": c, "name": n, "unit": u, "gram": g, "purity": p} for c, n, u, g, p in rows]


def _fx_items(codes) -> list[dict]:
    base = prefs.base()
    return [{"code": c, "name": CURRENCY_NAMES.get(c, c), "unit": c} for c in codes if c != base]


def is_tr() -> bool:
    return prefs.region() == "tr"


def catalog() -> dict:
    """The active catalog (depends on the region pack and base currency)."""
    if is_tr():
        return {"gold": _items(TR_GOLD), "silver": _items(TR_SILVER), "metal": _items(TR_METALS), "fx": _fx_items(TR_FX)}
    return {
        "gold": _items(GENERIC_GOLD),
        "silver": _items(GENERIC_SILVER),
        "metal": _items(GENERIC_METALS),
        "fx": _fx_items(prefs.SUPPORTED_CURRENCIES),
    }


_ALL_METALS = {
    ("gold", r[0]): r for r in TR_GOLD + GENERIC_GOLD
} | {("silver", r[0]): r for r in TR_SILVER + GENERIC_SILVER} | {("metal", r[0]): r for r in TR_METALS + GENERIC_METALS}


def catalog_item(kind: str, code: str, active_only: bool = False) -> dict | None:
    """Catalog entry; items of the other catalog are still found (assets created before a region change)
    unless active_only is set."""
    for item in catalog().get(kind, []):
        if item["code"] == code:
            return item
    if active_only:
        return None
    row = _ALL_METALS.get((kind, code))
    if row:
        return _items([row])[0]
    if kind == "fx" and code in CURRENCY_NAMES:
        return {"code": code, "name": CURRENCY_NAMES[code], "unit": code}
    return None


def base_item_code(kind: str) -> str | None:
    """Reference "plain gram" item of a metal kind in the active catalog."""
    return (TR_BASE_ITEMS if is_tr() else GENERIC_BASE_ITEMS).get(kind)


def unit_for(kind: str, code: str) -> str:
    if kind == "stock":
        return "piece"
    item = catalog_item(kind, code)
    return item["unit"] if item else ""


def price_key(kind: str, code: str) -> str:
    return f"{kind}:{code}"


# ---- Tickers -----------------------------------------------------------------------

TICKER = re.compile(r"[A-Z0-9^][A-Z0-9.\-=^]{0,11}")
BARE_BIST = re.compile(r"[A-Z0-9]{3,6}")


def normalize_ticker(code: str) -> str | None:
    """Stored stock code, or None if invalid. With region "tr", Borsa Istanbul tickers are stored bare
    ("thyao.is" → "THYAO"); otherwise the Yahoo symbol is stored as is ("sap.de" → "SAP.DE")."""
    code = (code or "").strip().upper()
    if is_tr():
        code = code.removesuffix(".IS")
        if "." not in code:
            return code if BARE_BIST.fullmatch(code) else None
    return code if TICKER.fullmatch(code) else None


def yahoo_symbol(code: str) -> str:
    """Yahoo symbol for a stored stock code: with region "tr" a bare ticker is on Borsa Istanbul."""
    if is_tr() and "." not in code and "=" not in code and "^" not in code:
        return f"{code}.IS"
    return code


def stock_source(code: str) -> str:
    return "Borsa Istanbul (Yahoo)" if yahoo_symbol(code).endswith(".IS") else "Yahoo Finance"


# ---- Sources -----------------------------------------------------------------------


def parse_truncgil(raw: str) -> dict[str, float]:
    """Code → buying price (TRY). Returns the readable items even if the response was cut off."""
    try:
        data = json.loads(raw)
        return {k: float(v["Buying"]) for k, v in data.items() if isinstance(v, dict) and v.get("Buying") is not None}
    except (json.JSONDecodeError, ValueError, TypeError):
        out = {}
        for m in re.finditer(r'"([A-Z0-9]+)":\{([^{}]*)\}', raw):
            b = re.search(r'"Buying":\s*([0-9.]+)', m.group(2))
            if b:
                out[m.group(1)] = float(b.group(1))
        return out


def fetch_truncgil(attempts: int = 3, timeout: float = 15) -> dict[str, float]:
    best: dict[str, float] = {}
    for i in range(attempts):
        try:
            r = httpx.get(TRUNCGIL, headers=UA, timeout=timeout)
            prices = parse_truncgil(r.text)
            if len(prices) > len(best):
                best = prices
            if "GRA" in best and "USD" in best and "GUMUS" in best:
                break
        except httpx.HTTPError as e:
            log.warning("Could not fetch gold/FX price: %s", e)
        time.sleep(1 + i)
    return best


def fetch_tcmb_all() -> dict[str, float]:
    """TCMB (central bank) FX buying rates in TRY per unit."""
    try:
        root = ET.fromstring(httpx.get(TCMB, timeout=15).text)
    except Exception as e:  # network/XML error
        log.warning("Could not fetch TCMB rates: %s", e)
        return {}
    out = {}
    for cur in root.findall("Currency"):
        code, unit = cur.attrib.get("CurrencyCode"), float(cur.findtext("Unit") or 1)
        buying = cur.findtext("ForexBuying") or cur.findtext("BanknoteBuying")
        if code and buying:
            out[code] = float(buying) / unit
    return out


def fetch_fx(base: str, codes: list[str], timeout: float = 15) -> dict[str, float]:
    """Value of 1 unit of each currency in `base` (ECB rates via Frankfurter; TCMB when the base is TRY)."""
    codes = sorted({c for c in codes if c and c != base})
    if not codes:
        return {}
    if base == "TRY":
        tcmb = fetch_tcmb_all()
        return {c: tcmb[c] for c in codes if c in tcmb}
    try:
        r = httpx.get(FRANKFURTER.format(base=base, symbols=",".join(codes)), headers=UA, timeout=timeout)
        r.raise_for_status()
        rates = r.json().get("rates") or {}
    except Exception as e:  # network/JSON error
        log.warning("Could not fetch ECB rates: %s", e)
        return {}
    return {c: 1 / float(v) for c, v in rates.items() if c in codes and v}


class SymbolNotFound(Exception):
    pass


# Yahoo quotes some exchanges in minor units
MINOR_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ILA": ("ILS", 100), "ZAc": ("ZAR", 100)}


def fetch_quote(symbol: str, timeout: float = 15) -> tuple[float, str]:
    """(latest price, quote currency as reported, e.g. "GBp") of a Yahoo symbol (~15 min delayed)."""
    r = httpx.get(YAHOO.format(symbol=symbol), headers=UA, timeout=timeout)
    if r.status_code == 404:
        raise SymbolNotFound(symbol)
    r.raise_for_status()
    result = (r.json().get("chart") or {}).get("result")
    if not result:
        raise SymbolNotFound(symbol)
    meta = result[0]["meta"]
    if meta.get("regularMarketPrice") is None:
        raise SymbolNotFound(symbol)
    return float(meta["regularMarketPrice"]), meta.get("currency") or "USD"


def _major(price: float, currency: str) -> tuple[float, str]:
    """Quotes in minor units (pence, agorot, cents) → major units."""
    if currency in MINOR_UNITS:
        cur, div = MINOR_UNITS[currency]
        return price / div, cur
    return price, currency.upper()


# ---- Base-currency conversion --------------------------------------------------------


class NoRate(Exception):
    pass


def rate_to_base(db: Session, currency: str, timeout: float = 15) -> float:
    """Value of 1 unit of `currency` in the base currency; fetches and stores the rate if no recent one exists."""
    base = prefs.base()
    currency = currency.upper()
    if currency == base:
        return 1.0
    today = date.today()
    recent = db.scalar(
        select(FxRate.rate)
        .where(FxRate.currency == currency, FxRate.date <= today, FxRate.date >= today - timedelta(days=4))
        .order_by(FxRate.date.desc())
        .limit(1)
    )
    if recent:
        return recent
    fetched = fetch_fx(base, [currency], timeout)
    if fetched.get(currency):
        db.merge(FxRate(date=today, currency=currency, rate=fetched[currency]))
        db.flush()
        return fetched[currency]
    older = db.scalar(select(FxRate.rate).where(FxRate.currency == currency).order_by(FxRate.date.desc()).limit(1))
    if older:
        return older
    raise NoRate(currency)


def to_base(db: Session, amount: float, currency: str, timeout: float = 15) -> float:
    return amount * rate_to_base(db, currency, timeout)


def fetch_stock(db: Session, code: str, timeout: float = 15) -> float:
    """Latest price of a stock in the base currency."""
    price, currency = _major(*fetch_quote(yahoo_symbol(code), timeout))
    return to_base(db, price, currency, timeout)


def fetch_metal(db: Session, code: str, timeout: float = 15, _cache: dict | None = None) -> float:
    """Generic metal price in the base currency (Yahoo futures, USD per troy ounce)."""
    symbol = FUTURES[code]
    cache = _cache if _cache is not None else {}
    if symbol not in cache:
        cache[symbol] = _major(*fetch_quote(symbol, timeout))
    per_oz, currency = cache[symbol]
    per_unit = per_oz if code.endswith("_OZ") else per_oz / TROY_OUNCE_G
    return to_base(db, per_unit, currency, timeout)


# ---- Storage -----------------------------------------------------------------------


def store(db: Session, kind: str, code: str, price: float, source: str) -> None:
    """Stores today's price (base currency per unit)."""
    db.merge(
        AssetPrice(
            key=price_key(kind, code),
            date=date.today(),
            price_cents=round(price * 100),
            source=source,
            fetched_at=datetime.now(),
        )
    )


def latest(db: Session, kind: str, code: str) -> AssetPrice | None:
    return db.scalar(
        select(AssetPrice).where(AssetPrice.key == price_key(kind, code)).order_by(AssetPrice.date.desc()).limit(1)
    )


def price_on(db: Session, kind: str, code: str, on: date) -> AssetPrice | None:
    return db.scalar(
        select(AssetPrice)
        .where(AssetPrice.key == price_key(kind, code), AssetPrice.date <= on)
        .order_by(AssetPrice.date.desc())
        .limit(1)
    )


def _refresh_tr(db: Session, pairs: set[tuple[str, str]], attempts: int, timeout: float) -> tuple[int, list[str]]:
    """Grand Bazaar (TRY) prices with TCMB as the FX fallback, converted to the base currency."""
    updated, failed = 0, []
    prices = fetch_truncgil(attempts, timeout)
    try:
        factor = rate_to_base(db, "TRY", timeout)
    except NoRate:
        return 0, sorted(c for _, c in pairs)
    tcmb = None
    for kind, code in pairs:
        if code in prices:
            store(db, kind, code, prices[code] * factor, "Grand Bazaar (truncgil)")
            updated += 1
        elif kind == "fx":
            tcmb = tcmb if tcmb is not None else fetch_tcmb_all()
            if code in tcmb:
                store(db, kind, code, tcmb[code] * factor, "TCMB")
                updated += 1
            else:
                failed.append(code)
        else:
            failed.append(code)
    return updated, failed


def refresh(db: Session, needed: set[tuple[str, str]], attempts: int = 3, timeout: float = 15) -> dict:
    """Updates prices for the needed (kind, code) pairs. Returns: {updated: n, failed: [code]}."""
    updated, failed = 0, []
    base = prefs.base()
    tr = is_tr()
    tr_codes = {r[0] for r in TR_GOLD + TR_SILVER + TR_METALS}
    tr_pairs = {(k, c) for k, c in needed if (k in ("gold", "silver", "metal") and c in tr_codes) or (tr and k == "fx")}
    generic_fx = sorted({c for k, c in needed if k == "fx" and not tr})
    metals = sorted({(k, c) for k, c in needed if k in ("gold", "silver", "metal") and c in FUTURES})
    unknown = [c for k, c in needed if k in ("gold", "silver", "metal") and c not in FUTURES and c not in tr_codes]
    failed += unknown

    # Base currency itself as an FX asset is always worth 1
    for kind, code in [p for p in tr_pairs | {("fx", c) for c in generic_fx} if p[0] == "fx" and p[1] == base]:
        store(db, kind, code, 1.0, "Base currency")
        updated += 1
    tr_pairs = {p for p in tr_pairs if not (p[0] == "fx" and p[1] == base)}
    generic_fx = [c for c in generic_fx if c != base]

    if tr_pairs:
        n, f = _refresh_tr(db, tr_pairs, attempts, timeout)
        updated, failed = updated + n, failed + f
    if generic_fx:
        rates = fetch_fx(base, generic_fx, timeout)
        for code in generic_fx:
            if rates.get(code):
                db.merge(FxRate(date=date.today(), currency=code, rate=rates[code]))
                store(db, "fx", code, rates[code], "ECB (Frankfurter)" if base != "TRY" else "TCMB")
                updated += 1
            else:
                failed.append(code)
    quotes: dict = {}
    for kind, code in metals:
        try:
            store(db, kind, code, fetch_metal(db, code, timeout, quotes), "Yahoo Finance (futures)")
            updated += 1
        except Exception as e:
            log.warning("Could not fetch metal price (%s): %s", code, e)
            failed.append(code)
    for kind, code in needed:
        if kind != "stock":
            continue
        try:
            store(db, kind, code, fetch_stock(db, code, timeout), stock_source(code))
            updated += 1
        except Exception as e:
            log.warning("Could not fetch stock price (%s): %s", code, e)
            failed.append(code)
    db.commit()
    return {"updated": updated, "failed": sorted(failed)}


def split_asset_code(asset_code: str) -> tuple[str, str]:
    """"gold:XAU_G" → ("gold", "XAU_G")."""
    kind, _, code = (asset_code or "").partition(":")
    return kind, code
