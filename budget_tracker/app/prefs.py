"""Household preferences: base currency, enabled currencies, locale, optional region pack.

Stored in app_settings as "pref:<name>" rows with JSON values. A module-level cache keeps the current values so
code without a db session (schema defaults, model column defaults, format_money) can read the base currency.
"""
import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

DEFAULTS: dict = {
    "setup_done": False,
    "base_currency": "USD",
    "currencies": ["USD", "EUR", "GBP"],
    "locale": "en-US",
    "region": "",
    "investments": True,
}

SUPPORTED_CURRENCIES = [
    "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "IDR", "ILS", "INR", "ISK",
    "JPY", "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB", "TRY", "USD", "ZAR",
]
LOCALES = ["en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "it-IT", "nl-NL", "pt-BR", "tr-TR"]
REGIONS = {"": "None", "tr": "Turkey"}

PREFIX = "pref:"

_cache: dict = {}


def _normalize(p: dict) -> dict:
    out = {**DEFAULTS, **{k: v for k, v in p.items() if k in DEFAULTS}}
    base = out["base_currency"]
    if base not in SUPPORTED_CURRENCIES:
        base = out["base_currency"] = DEFAULTS["base_currency"]
    curs = [c for c in (out.get("currencies") or []) if c in SUPPORTED_CURRENCIES]
    seen: list[str] = [base]
    for c in curs:
        if c not in seen:
            seen.append(c)
    out["currencies"] = seen
    if out["locale"] not in LOCALES:
        out["locale"] = DEFAULTS["locale"]
    if out["region"] not in REGIONS:
        out["region"] = ""
    out["setup_done"] = bool(out["setup_done"])
    out["investments"] = bool(out["investments"])
    return out


def _read(db: Session) -> dict:
    from .models import AppSetting

    stored: dict = {}
    for row in db.scalars(select(AppSetting).where(AppSetting.key.like(PREFIX + "%"))):
        name = row.key[len(PREFIX):]
        if name not in DEFAULTS:
            continue
        try:
            stored[name] = json.loads(row.value)
        except (TypeError, ValueError):
            continue
    return _normalize(stored)


def load(db: Session) -> None:
    """Reads the stored preferences into the module cache."""
    _cache.clear()
    _cache.update(_read(db))


def get(db: Session) -> dict:
    """Current preferences (also refreshes the cache)."""
    load(db)
    return dict(_cache, currencies=list(_cache["currencies"]))


def _validate_currency(c) -> str:
    if not isinstance(c, str) or c.upper() not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Unsupported currency: {c}")
    return c.upper()


def save(db: Session, **changes) -> dict:
    """Validates and persists the given preferences. Raises ValueError on invalid input.

    Changing the base currency deletes all stored FX rates (they are expressed in the old base).
    """
    from .models import AppSetting, FxRate

    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown preference: {', '.join(sorted(unknown))}")
    current = _read(db)
    new = dict(current)
    if "base_currency" in changes:
        new["base_currency"] = _validate_currency(changes["base_currency"])
    if "currencies" in changes:
        curs = changes["currencies"]
        if not isinstance(curs, (list, tuple)):
            raise ValueError("currencies must be a list")
        new["currencies"] = [_validate_currency(c) for c in curs]
    if "locale" in changes:
        if changes["locale"] not in LOCALES:
            raise ValueError(f"Unsupported locale: {changes['locale']}")
        new["locale"] = changes["locale"]
    if "region" in changes:
        region = changes["region"] or ""
        if region not in REGIONS:
            raise ValueError(f"Unknown region: {region}")
        new["region"] = region
    for flag in ("setup_done", "investments"):
        if flag in changes:
            new[flag] = bool(changes[flag])
    new = _normalize(new)
    for name, value in new.items():
        db.merge(AppSetting(key=PREFIX + name, value=json.dumps(value)))
    if new["base_currency"] != current["base_currency"]:
        db.execute(delete(FxRate))
        # Asset prices are stored in the base currency: refetch them in the new one
        from .models import AssetPrice

        db.execute(delete(AssetPrice))
    db.commit()
    _cache.clear()
    _cache.update(new)
    return dict(new, currencies=list(new["currencies"]))


def _c() -> dict:
    return _cache or _normalize({})


def base() -> str:
    return _c()["base_currency"]


def currencies() -> list[str]:
    return list(_c()["currencies"])


def region() -> str:
    return _c()["region"]


def locale() -> str:
    return _c()["locale"]


def investments_enabled() -> bool:
    return _c()["investments"]
