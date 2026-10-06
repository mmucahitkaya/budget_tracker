"""Exchange rates and conversion to the household base currency.

FxRate.rate = units of BASE currency per 1 unit of `currency`.
Sources: ECB reference rates via frankfurter.app (any base), or the CBRT (TCMB) when the Turkey region pack is
active and the base currency is TRY.
"""
import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import prefs
from .models import FxRate

log = logging.getLogger(__name__)

FRANKFURTER = "https://api.frankfurter.app"


# ---- TCMB (Turkey region pack, base TRY) ------------------------------------------


def _tcmb_url(d: date | None) -> str:
    if d is None:
        return "https://www.tcmb.gov.tr/kurlar/today.xml"
    return f"https://www.tcmb.gov.tr/kurlar/{d:%Y%m}/{d:%d%m%Y}.xml"


def parse_tcmb_xml(xml_text: str, wanted: Iterable[str] | None = None) -> tuple[date, dict[str, float]]:
    """Parses a TCMB daily XML into (date, {code: TRY per 1 unit}). `wanted` limits the currencies (None = all)."""
    wanted_set = set(wanted) if wanted is not None else None
    root = ET.fromstring(xml_text)
    # Tarih="04.10.2026" (date attribute)
    day, month, year = root.attrib["Tarih"].split(".")
    rates: dict[str, float] = {}
    for cur in root.findall("Currency"):
        code = cur.attrib.get("CurrencyCode")
        if not code or (wanted_set is not None and code not in wanted_set):
            continue
        unit = float(cur.findtext("Unit") or 1)
        selling = cur.findtext("ForexSelling") or cur.findtext("BanknoteSelling")
        if selling:
            rates[code] = float(selling) / unit
    return date(int(year), int(month), int(day)), rates


def _fetch_tcmb(target: date | None, wanted: list[str]) -> tuple[date, dict[str, float]] | None:
    r = httpx.get(_tcmb_url(target), timeout=15)
    if r.status_code != 200:
        return None
    return parse_tcmb_xml(r.text, wanted)


# ---- Frankfurter / ECB (generic) ----------------------------------------------------


def frankfurter_url(base: str, symbols: Iterable[str], d: date | None) -> str:
    when = d.isoformat() if d else "latest"
    return f"{FRANKFURTER}/{when}?base={base}&symbols={','.join(symbols)}"


def parse_frankfurter(data: dict) -> tuple[date, dict[str, float]]:
    """{"date": "2026-10-02", "rates": {"USD": 1.07}} (1 BASE = 1.07 USD) -> (date, {"USD": 1/1.07})."""
    rate_date = date.fromisoformat(data["date"])
    rates = {code: 1 / float(v) for code, v in (data.get("rates") or {}).items() if v}
    return rate_date, rates


def _fetch_frankfurter(target: date | None, base: str, wanted: list[str]) -> tuple[date, dict[str, float]] | None:
    r = httpx.get(frankfurter_url(base, wanted, target), timeout=15)
    if r.status_code != 200:
        return None
    return parse_frankfurter(r.json())


def uses_tcmb() -> bool:
    return prefs.region() == "tr" and prefs.base() == "TRY"


def wanted_currencies(db: Session) -> list[str]:
    """Enabled currencies + currencies used in transactions, minus the base."""
    from .models import Transaction

    base = prefs.base()
    out = [c for c in prefs.currencies() if c != base]
    for (cur,) in db.execute(select(Transaction.currency).where(Transaction.currency != base).distinct()):
        if cur and cur not in out and cur in prefs.SUPPORTED_CURRENCIES:
            out.append(cur)
    return out


def fetch_rates(db: Session, d: date | None = None) -> bool:
    """Fetches and stores rates for the base currency. On weekends/holidays it tries up to 7 days back."""
    base = prefs.base()
    wanted = wanted_currencies(db)
    if not wanted:
        return False
    tcmb = uses_tcmb()
    attempts = [None] if d is None else [d - timedelta(days=i) for i in range(7)]
    for target in attempts:
        try:
            got = _fetch_tcmb(target, wanted) if tcmb else _fetch_frankfurter(target, base, wanted)
        except Exception as e:  # network error etc.
            log.warning("Could not fetch FX rates (%s): %s", target, e)
            continue
        if not got:
            continue
        rate_date, rates = got
        if not rates:
            continue
        # The requested day (if a holiday) also gets the previous business day's rate
        for store_date in {rate_date, d or rate_date}:
            for code, rate in rates.items():
                db.merge(FxRate(date=store_date, currency=code, rate=rate))
        db.commit()
        return True
    return False


def get_rate(db: Session, currency: str, on: date) -> float | None:
    if currency == prefs.base():
        return 1.0
    row = db.scalar(
        select(FxRate).where(FxRate.currency == currency, FxRate.date <= on).order_by(FxRate.date.desc()).limit(1)
    )
    if row is None:
        row = db.scalar(select(FxRate).where(FxRate.currency == currency).order_by(FxRate.date.asc()).limit(1))
    return row.rate if row else None


class Converter:
    """Caching converter so report calculations don't query the same date over and over.

    Amounts without a known rate can't be added to totals; instead of silently counting as 0 they are
    collected in `missing` and shown as "total incomplete" in responses.
    """

    def __init__(self, db: Session):
        self.db = db
        self._cache: dict[tuple[str, date], float | None] = {}
        self.missing: dict[str, int] = {}

    def to_base_cents(self, cents: int, currency: str, on: date) -> int:
        if not cents:
            return 0
        if currency == prefs.base():
            return cents
        key = (currency, on)
        if key not in self._cache:
            self._cache[key] = get_rate(self.db, currency, on)
        rate = self._cache[key]
        if not rate:
            self.missing[currency] = self.missing.get(currency, 0) + cents
            return 0
        return round(cents * rate)

    def dict_to_base_cents(self, amounts: dict[str, int], on: date) -> int:
        """Sum of a {currency: cents} dict in base currency."""
        return sum(self.to_base_cents(v, c, on) for c, v in (amounts or {}).items())

    def missing_list(self) -> list[dict]:
        return [{"currency": c, "amount": round(v / 100, 2)} for c, v in sorted(self.missing.items())]


def ensure_rate_for(db: Session, currency: str, on: date) -> None:
    """When adding a foreign-currency transaction, fetch that day's rate if missing."""
    if currency == prefs.base():
        return
    exists = db.scalar(
        select(FxRate.rate).where(FxRate.currency == currency, FxRate.date <= on, FxRate.date >= on - timedelta(days=4))
    )
    if exists is None:
        fetch_rates(db, on)


def backfill_missing_rates(db: Session, limit: int = 30) -> int:
    """Backfills missing rates from the archive for the dates of foreign-currency transactions."""
    from .models import Transaction

    pairs = db.execute(
        select(Transaction.currency, Transaction.date).where(Transaction.currency != prefs.base()).distinct()
    ).all()
    fetched = 0
    for currency, on in pairs:
        if fetched >= limit:
            break
        near = db.scalar(
            select(FxRate.rate).where(FxRate.currency == currency, FxRate.date <= on, FxRate.date >= on - timedelta(days=4))
        )
        if near is None and fetch_rates(db, on):
            fetched += 1
    return fetched
