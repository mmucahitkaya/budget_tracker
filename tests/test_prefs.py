"""Household preferences, settings API and base-currency FX (generic, non-Turkey setup)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import finance, fx, prefs, schemas, services
from app.main import app
from app.models import CardStatement, CreditCard, FxRate, Transaction


@pytest.fixture
def usd(db):
    prefs.save(db, base_currency="USD", currencies=["USD", "EUR"], region="", locale="en-US")
    return db


def test_defaults_before_setup(db):
    from app.models import AppSetting

    db.query(AppSetting).delete()
    db.commit()
    p = prefs.get(db)
    assert p["base_currency"] == "USD" and p["currencies"] == ["USD", "EUR", "GBP"]
    assert p["setup_done"] is False and p["region"] == "" and p["locale"] == "en-US" and p["investments"] is True
    assert prefs.base() == "USD"


def test_save_validates_and_keeps_base_first(db):
    p = prefs.save(db, base_currency="EUR", currencies=["usd", "EUR", "CHF"], locale="de-DE", region="")
    assert p["base_currency"] == "EUR" and p["currencies"] == ["EUR", "USD", "CHF"]
    assert prefs.base() == "EUR" and prefs.currencies() == ["EUR", "USD", "CHF"] and prefs.locale() == "de-DE"
    for bad in ({"base_currency": "XXX"}, {"currencies": ["EUR", "BTC"]}, {"locale": "xx-XX"}, {"region": "zz"},
                {"colour": "red"}):
        with pytest.raises(ValueError):
            prefs.save(db, **bad)
    assert prefs.base() == "EUR"  # nothing changed by the failed saves
    prefs.load(db)
    assert prefs.get(db)["currencies"] == ["EUR", "USD", "CHF"]  # persisted


def test_base_change_clears_rates(db):
    db.add(FxRate(date=date(2026, 10, 1), currency="USD", rate=40.0))
    db.commit()
    prefs.save(db, investments=False)  # same base: rates stay
    assert db.query(FxRate).count() == 1 and prefs.investments_enabled() is False
    prefs.save(db, base_currency="EUR")
    assert db.query(FxRate).count() == 0


def test_settings_api(db):
    client = TestClient(app)
    s = client.get("/api/settings").json()
    assert s["base_currency"] == "TRY" and s["region"] == "tr" and s["setup_done"] is True
    assert "JPY" in s["supported_currencies"] and "de-DE" in s["locales"]
    assert s["regions"] == [{"code": "", "name": "None"}, {"code": "tr", "name": "Turkey"}]
    assert s["household_payday"] == 1 and s["spend_buffer"] == 0
    r = client.put("/api/settings", json={"base_currency": "eur", "currencies": ["USD"], "locale": "de-DE", "region": "",
                                          "spend_buffer": 50})
    assert r.status_code == 200
    s = r.json()
    assert (s["base_currency"], s["currencies"], s["locale"], s["region"]) == ("EUR", ["EUR", "USD"], "de-DE", "")
    assert s["spend_buffer"] == 50 and s["household_payday"] == 1
    assert client.put("/api/settings", json={"base_currency": "XYZ"}).status_code == 400
    assert client.put("/api/settings", json={"region": "mars"}).status_code == 400
    assert client.get("/api/settings").json()["base_currency"] == "EUR"


def test_defaults_follow_base(usd):
    assert schemas.TransactionIn(amount=1, date="2026-10-01").currency == "USD"
    assert schemas.PaymentIn(amount=1, date="2026-10-01").currency == "USD"
    assert schemas.TransactionIn(amount=1, date="2026-10-01", currency="chf").currency == "CHF"
    with pytest.raises(ValueError):
        schemas.TransactionIn(amount=1, date="2026-10-01", currency="ABC")
    tx = Transaction(kind="expense", amount_cents=100, date=date(2026, 10, 1))
    usd.add(tx)
    usd.commit()
    assert tx.currency == "USD"
    assert finance.format_money(123456) == "1,234.56 USD"
    assert finance.format_money(500, "EUR") == "5.00 EUR"


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def test_frankfurter_rates(usd, monkeypatch):
    usd.add(Transaction(kind="expense", amount_cents=100, currency="JPY", date=date(2026, 10, 1)))
    usd.commit()
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        if "2026-10-04" in url:  # Sunday: no data
            return _Resp(404, {})
        return _Resp(200, {"amount": 1.0, "base": "USD", "date": "2026-10-02", "rates": {"EUR": 0.8, "JPY": 150.0}})

    monkeypatch.setattr(fx.httpx, "get", fake_get)
    assert fx.fetch_rates(usd, date(2026, 10, 4))
    assert calls[0] == "https://api.frankfurter.app/2026-10-04?base=USD&symbols=EUR,JPY"
    assert "tcmb" not in "".join(calls)
    rate = usd.get(FxRate, (date(2026, 10, 2), "EUR")).rate
    assert rate == pytest.approx(1.25)  # 1 EUR = 1.25 USD
    assert usd.get(FxRate, (date(2026, 10, 4), "EUR")) is not None  # requested day gets the previous business day's rate
    conv = fx.Converter(usd)
    assert conv.to_base_cents(1000, "EUR", date(2026, 10, 5)) == 1250
    assert conv.to_base_cents(1000, "USD", date(2026, 10, 5)) == 1000
    assert conv.to_base_cents(15000, "JPY", date(2026, 10, 5)) == 100
    assert conv.to_base_cents(1000, "GBP", date(2026, 10, 5)) == 0 and conv.missing_list() == [{"currency": "GBP", "amount": 10.0}]


def test_parse_frankfurter():
    d, rates = fx.parse_frankfurter({"date": "2026-10-02", "rates": {"USD": 1.07}})
    assert d == date(2026, 10, 2) and rates["USD"] == pytest.approx(1 / 1.07)


def test_turkey_pack_uses_tcmb(db, monkeypatch):
    urls = []
    xml = """<Tarih_Date Tarih="02.10.2026"><Currency CurrencyCode="USD"><Unit>1</Unit><ForexSelling>41.5</ForexSelling></Currency>
    <Currency CurrencyCode="EUR"><Unit>1</Unit><ForexSelling>48.25</ForexSelling></Currency></Tarih_Date>"""

    class R:
        status_code, text = 200, xml

    monkeypatch.setattr(fx.httpx, "get", lambda url, timeout=None: urls.append(url) or R())
    assert fx.fetch_rates(db)
    assert urls == ["https://www.tcmb.gov.tr/kurlar/today.xml"]
    assert db.get(FxRate, (date(2026, 10, 2), "USD")).rate == 41.5


def test_card_debts_in_any_currency(usd):
    client = TestClient(app)
    card = client.post("/api/cards", json={"name": "Visa", "statement_day": 26, "due_day": 6, "limit": 5000,
                                           "debts": {"USD": 1000, "EUR": 100}}).json()
    assert card["debts"] == {"USD": 1000, "EUR": 100}
    assert "debt_try" not in card
    # No EUR rate yet: only the USD debt counts toward the available limit
    assert card["available"] == 4000
    usd.add(FxRate(date=date(2026, 1, 1), currency="EUR", rate=1.2))
    usd.commit()
    assert client.get("/api/cards").json()[0]["available"] == 3880
    st = client.post(f"/api/cards/{card['id']}/statements", json={"period_end": "2026-09-26", "due_date": "2026-10-06",
                                                                  "totals": {"USD": 800, "eur": 50}, "min_payment": 80}).json()
    assert st["currency"] == "USD" and st["totals"] == {"USD": 800, "EUR": 50} and st["remaining"] == {"USD": 800, "EUR": 50}
    out = client.get("/api/cards").json()[0]
    assert out["debts"] == {"USD": 800, "EUR": 50}
    client.post(f"/api/cards/{card['id']}/payments", json={"amount": 50, "currency": "EUR", "date": "2026-10-01",
                                                           "statement_id": st["id"]})
    s = client.get("/api/cards").json()[0]["statements"][0]
    assert s["paid"] == {"EUR": 50} and s["remaining"] == {"USD": 800, "EUR": 0} and s["is_paid"] is False
    client.post(f"/api/cards/statements/{st['id']}/paid?paid=true")
    out = client.get("/api/cards").json()[0]
    assert out["debts"] == {} and out["statements"][0]["is_paid"] is True


def test_statement_draft_totals_from_parser(usd):
    from app.ai_parser import normalize_result
    from app.models import Document

    card = CreditCard(name="Amex", last4="1001", statement_day=15, due_day=25)
    usd.add(card)
    doc = Document(filename="s.pdf", stored_path="/x", mime="application/pdf", status="review")
    usd.add(doc)
    usd.commit()
    parsed = normalize_result({"doc_type": "statement", "card_last4": "1001", "period_end": "2026-10-15",
                               "due_date": "2026-10-25", "currency": "$",
                               "totals": [{"currency": "USD", "amount": 300}, {"currency": "€", "amount": 20}, "x"],
                               "min_payment": 35, "items": []})
    assert parsed["currency"] == "USD" and parsed["totals"] == [{"currency": "USD", "amount": 300.0},
                                                                {"currency": "EUR", "amount": 20.0}]
    draft = services.build_draft(usd, doc, parsed)
    assert draft["statement"]["totals"] == {"USD": 300.0, "EUR": 20.0} and draft["statement"]["currency"] == "USD"
    client = TestClient(app)
    body = {"draft": {"doc_type": "statement", "card_id": card.id, "rows": [], "statement": draft["statement"]}}
    assert client.post(f"/api/documents/{doc.id}/confirm", json=body).status_code == 200
    usd.expire_all()
    st = usd.query(CardStatement).one()
    assert st.totals == {"USD": 30000, "EUR": 2000} and st.currency == "USD" and st.min_payment_cents == 3500
    assert usd.get(CreditCard, card.id).debts == {"USD": 30000, "EUR": 2000}
