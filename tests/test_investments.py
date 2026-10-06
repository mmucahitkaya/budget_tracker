from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import investments, prefs, prices
from app.main import app
from app.models import AssetLot, AssetPrice, SavingsGoal

FAKE = {"GRA": 6500.0, "CEYREKALTIN": 10500.0, "GUMUS": 97.0, "USD": 49.0}


def _raise(exc):
    raise exc


@pytest.fixture
def client(db, monkeypatch):
    # Region pack "tr" with a TRY base (also set by conftest; repeated so these tests are self-contained)
    prefs.save(db, base_currency="TRY", currencies=["TRY", "USD", "EUR"], region="tr", setup_done=True)
    monkeypatch.setattr(prices, "fetch_truncgil", lambda attempts=3, timeout=15: dict(FAKE))
    monkeypatch.setattr(prices, "fetch_quote", lambda symbol, timeout=15: (300.0, "TRY") if symbol == "THYAO.IS" else _raise(prices.SymbolNotFound(symbol)))
    monkeypatch.setattr(prices, "fetch_tcmb_all", lambda: {"GBP": 62.0})
    import app.routers.investments as r
    r._last_refresh = 0
    return TestClient(app)


def test_parse_truncated_truncgil_response():
    raw = '{"Update_Date":"x","USD":{"Buying":49.1,"Selling":49.2},"GRA":{"Buying":6572.85,"Selling":6573' 
    assert prices.parse_truncgil(raw) == {"USD": 49.1}


def lot(side, d, q, p, i):
    return AssetLot(id=i, side=side, date=d, quantity=q, unit_price_cents=p * 100)


def test_average_cost_and_realized():
    lots = [lot("buy", date(2026, 1, 1), 10, 5000, 1), lot("buy", date(2026, 2, 1), 10, 6000, 2),
            lot("sell", date(2026, 3, 1), 5, 7000, 3)]
    qty, cost = investments.position(lots)
    assert qty == 15 and cost == 15 * 5500 * 100  # average 5,500
    assert investments.realized(lots) == 5 * (7000 - 5500) * 100


def test_cannot_sell_more_than_held():
    with pytest.raises(investments.LotError):
        investments.position([lot("buy", date(2026, 1, 1), 2, 100, 1), lot("sell", date(2026, 1, 2), 3, 100, 2)])


def test_gold_with_lots_and_pl(db, client):
    a = client.post("/api/investments/assets", json={"kind": "gold", "code": "GRA"}).json()
    assert a["name"] == "Gram gold (24k)" and a["unit"] == "gram" and a["price"] == 6500
    a = client.post(f"/api/investments/assets/{a['id']}/lots", json={"date": "2026-01-10", "quantity": 10, "unit_price": 5000}).json()
    assert (a["quantity"], a["value"], a["cost"], a["pl"], a["pl_pct"]) == (10, 65000, 50000, 15000, 0.3)
    r = client.post(f"/api/investments/assets/{a['id']}/lots", json={"side": "sell", "date": "2026-02-01", "quantity": 20, "unit_price": 6000})
    assert r.status_code == 400  # selling more than held
    lot_id = a["lots"][0]["id"]
    client.post(f"/api/investments/assets/{a['id']}/lots", json={"side": "sell", "date": "2026-02-01", "quantity": 4, "unit_price": 6000})
    assert client.delete(f"/api/investments/lots/{lot_id}").status_code == 400  # deleting the buy would leave the sale uncovered


def test_stock_validation(db, client):
    assert client.post("/api/investments/assets", json={"kind": "stock", "code": "XXXXX"}).status_code == 400
    assert client.post("/api/investments/assets", json={"kind": "stock", "code": "th"}).status_code == 400
    s = client.post("/api/investments/assets", json={"kind": "stock", "code": "thyao.is"}).json()
    assert (s["code"], s["price"]) == ("THYAO", 300)


def test_unknown_catalog_code_rejected(db, client):
    assert client.post("/api/investments/assets", json={"kind": "gold", "code": "PLATIN"}).status_code == 400


def test_fund_manual_value(db, client):
    assert client.post("/api/investments/assets", json={"kind": "fund", "name": "TTE"}).status_code == 400
    f = client.post("/api/investments/assets", json={"kind": "fund", "name": "Tech Equity Fund", "value": 12000, "cost": 10000}).json()
    assert (f["value"], f["pl"], f["pl_pct"]) == (12000, 2000, 0.2)
    assert client.post(f"/api/investments/assets/{f['id']}/lots", json={"date": "2026-01-01", "quantity": 1, "unit_price": 1}).status_code == 400
    f = client.put(f"/api/investments/assets/{f['id']}", json={"kind": "fund", "name": "", "value": 12500, "cost": 10000}).json()
    assert f["value"] == 12500 and f["name"] == "Tech Equity Fund"


def test_fx_falls_back_to_tcmb(db, client):
    g = client.post("/api/investments/assets", json={"kind": "fx", "code": "GBP"}).json()
    assert g["price"] == 62 and g["price_source"] == "TCMB"


def test_overview_totals_owner_and_goal(db, client):
    db.add(SavingsGoal(name="Emergency fund", currency="TRY", target_cents=10_000_000, emergency=True))
    db.commit()
    me = client.get("/api/me").json()["me"]["id"]
    gold = client.post("/api/investments/assets", json={"kind": "gold", "code": "CEYREKALTIN", "owner_id": me, "goal_id": 1}).json()
    client.post(f"/api/investments/assets/{gold['id']}/lots", json={"date": "2026-01-01", "quantity": 2, "unit_price": 9000})
    client.post("/api/investments/assets", json={"kind": "fund", "name": "Money market", "value": 5000})
    o = client.get("/api/investments").json()
    assert o["value"] == 26000 and o["by_owner"] == [{"user_id": me, "value": 21000.0}, {"user_id": None, "value": 5000.0}]
    sav = client.get("/api/savings").json()
    goal = sav["goals"][0]
    assert (goal["invested"], goal["funded"], goal["remaining"]) == (21000, 21000, 79000)
    assert sav["investments"] == 26000 and sav["net_worth"] == 26000


def test_refresh_snapshot_and_rate_limit(db, client):
    a = client.post("/api/investments/assets", json={"kind": "silver", "code": "GUMUS"}).json()
    client.post(f"/api/investments/assets/{a['id']}/lots", json={"date": "2026-01-01", "quantity": 100, "unit_price": 80})
    assert client.post("/api/investments/refresh").json()["updated"] == 1
    assert client.post("/api/investments/refresh").status_code == 429
    hist = client.get("/api/investments").json()["history"]
    assert hist[-1] == {"date": date.today().isoformat(), "value": 9700.0, "cost": 8000.0}


def test_stale_price_flag(db, client):
    a = client.post("/api/investments/assets", json={"kind": "gold", "code": "GRA"}).json()
    client.post(f"/api/investments/assets/{a['id']}/lots", json={"date": "2026-01-01", "quantity": 1, "unit_price": 5000})
    p = db.query(AssetPrice).one()
    p.fetched_at = datetime.now() - timedelta(days=5)
    db.commit()
    assert client.get("/api/investments").json()["assets"][0]["stale"] is True


def test_household_payday_setting(db, client):
    assert client.get("/api/settings").json()["household_payday"] == 1
    assert client.put("/api/settings", json={"household_payday": 15}).json()["household_payday"] == 15
    assert client.get("/api/settings").json()["household_payday"] == 15
    assert client.put("/api/settings", json={"household_payday": 31}).status_code == 422


def test_gold_gram_totals(db, client):
    for code, q in (("GRA", 10), ("CEYREKALTIN", 4), ("TAMALTIN", 1)):
        a = client.post("/api/investments/assets", json={"kind": "gold", "code": code}).json()
        client.post(f"/api/investments/assets/{a['id']}/lots", json={"date": "2026-01-01", "quantity": q, "unit_price": 1})
    o = client.get("/api/investments").json()
    g = o["metals"]["gold"]
    # gross: 10 + 4×1.754 + 7.016 = 24.032 g ; pure: 9.95 + 6.4265 + 6.4267 = 22.803 g
    assert g["gross_gram"] == 24.032 and g["pure_gram"] == pytest.approx(22.803, abs=0.002)
    quarter = next(a for a in o["assets"] if a["code"] == "CEYREKALTIN")
    assert quarter["gross_gram"] == 7.016 and quarter["pure_gram"] == pytest.approx(6.427, abs=0.001)
    # gram gold price 6,500 → pure grams / 0.995 × 6,500
    assert g["base_equivalent"] == pytest.approx(22.803 / 0.995 * 6500, rel=1e-3)
    assert "silver" not in o["metals"]


def test_palladium_metal(db, client, monkeypatch):
    monkeypatch.setattr(prices, "fetch_truncgil", lambda attempts=3, timeout=15: {"PAL": 1867.68})
    a = client.post("/api/investments/assets", json={"kind": "metal", "code": "PAL"}).json()
    a = client.post(f"/api/investments/assets/{a['id']}/lots", json={"date": "2026-01-01", "quantity": 0.3, "unit_price": 0}).json()
    assert (a["name"], a["unit"], a["value"], a["kind_label"]) == ("Gram palladium", "gram", 560.3, "Other metal")


def test_asset_location(db, client):
    f = client.post("/api/investments/assets", json={"kind": "fund", "name": "HPH", "value": 100, "location": " Example Bank "}).json()
    assert f["location"] == "Example Bank"
    f = client.put(f"/api/investments/assets/{f['id']}", json={"kind": "fund", "value": 100, "location": "", "note": "Savings"}).json()
    assert (f["location"], f["note"]) == ("", "Savings")


def test_stock_code_and_jewelry_parsing():
    from app.telegram_invest import parse_investment
    # Turkish input keywords stay supported
    assert parse_investment("bugun 10 lot thyao aldim")["code"] == "THYAO"
    assert parse_investment("THYAO 10 lot aldım 300 tl")["code"] == "THYAO"
    assert parse_investment("dun 5 lot hisse aldim asels", {"ASELS"})["code"] == "ASELS"
    assert parse_investment("3 adet altin kupe aldim 9000 tl") is None
    assert parse_investment("gümüş kolye 1500") is None
    assert parse_investment("3 ceyrek 15000tl")["code"] == "CEYREKALTIN"


def test_lot_retry_returns_owning_asset(db, client):
    a = client.post("/api/investments/assets", json={"kind": "gold", "code": "GRA"}).json()
    lot = {"date": "2026-01-01", "quantity": 1, "unit_price": 6000, "client_ref": "r1"}
    client.post(f"/api/investments/assets/{a['id']}/lots", json=lot)
    b = client.post("/api/investments/assets", json={"kind": "gold", "code": "GRA"}).json()
    out = client.post(f"/api/investments/assets/{b['id']}/lots", json=lot).json()
    assert out["id"] == a["id"] and out["quantity"] == 1


def test_fund_cost_zero_kept(db, client):
    f = client.post("/api/investments/assets", json={"kind": "fund", "name": "Gift fund", "value": 500, "cost": 0}).json()
    assert f["cost"] == 0


# ---- Generic catalog (no region pack) ----------------------------------------------------

QUOTES = {"GC=F": (3110.34768, "USD"), "SI=F": (31.1034768, "USD"), "PL=F": (933.104304, "USD"),
          "PA=F": (1000.0, "USD"), "AAPL": (200.0, "USD"), "SAP.DE": (250.0, "EUR"), "VOD.L": (7000.0, "GBp")}
# value of 1 unit in USD
USD_RATES = {"EUR": 1.10, "GBP": 1.30, "TRY": 0.025, "JPY": 0.0067}


@pytest.fixture
def generic(db, monkeypatch):
    prefs.save(db, base_currency="USD", currencies=["USD", "EUR"], region="", setup_done=True)
    calls = {"fx": 0}

    def fake_fx(base, codes, timeout=15):
        calls["fx"] += 1
        assert base == prefs.base()
        if base == "USD":
            return {c: USD_RATES[c] for c in codes if c in USD_RATES}
        if base == "EUR":  # cross rates via USD
            return {c: (USD_RATES.get(c, 1.0) if c != "USD" else 1.0) / USD_RATES["EUR"] for c in codes}
        return {}

    monkeypatch.setattr(prices, "fetch_fx", fake_fx)
    monkeypatch.setattr(prices, "fetch_quote", lambda symbol, timeout=15: QUOTES[symbol] if symbol in QUOTES else _raise(prices.SymbolNotFound(symbol)))
    monkeypatch.setattr(prices, "fetch_truncgil", lambda attempts=3, timeout=15: _raise(AssertionError("truncgil used without region tr")))
    import app.routers.investments as r
    r._last_refresh = 0
    c = TestClient(app)
    c.calls = calls
    return c


def test_generic_catalog(db, generic):
    cat = generic.get("/api/investments/catalog").json()
    assert cat["currency"] == "USD" and cat["region"] == ""
    codes = {k: [i["code"] for i in v] for k, v in cat["catalog"].items()}
    assert codes["gold"] == ["XAU_G", "XAU_OZ"] and codes["silver"] == ["XAG_G", "XAG_OZ"]
    assert codes["metal"] == ["XPT_G", "XPD_G"]
    assert "USD" not in codes["fx"] and "EUR" in codes["fx"] and "JPY" in codes["fx"]
    assert "CEYREKALTIN" not in codes["gold"]
    assert generic.get("/api/investments").json()["catalog"] == cat["catalog"]
    # Turkish coins aren't offered without the region pack
    assert generic.post("/api/investments/assets", json={"kind": "gold", "code": "CEYREKALTIN"}).status_code == 400


def test_generic_metals_priced_from_futures(db, generic):
    g = generic.post("/api/investments/assets", json={"kind": "gold", "code": "XAU_G"}).json()
    assert (g["name"], g["unit"], g["price"], g["price_source"]) == ("Gold, gram", "gram", 100.0, "Yahoo Finance (futures)")
    oz = generic.post("/api/investments/assets", json={"kind": "gold", "code": "XAU_OZ"}).json()
    assert (oz["unit"], oz["price"]) == ("oz", 3110.35)
    generic.post(f"/api/investments/assets/{oz['id']}/lots", json={"date": "2026-01-01", "quantity": 2, "unit_price": 2500})
    s = generic.post("/api/investments/assets", json={"kind": "silver", "code": "XAG_G"}).json()
    assert s["price"] == 1.0
    pt = generic.post("/api/investments/assets", json={"kind": "metal", "code": "XPT_G"}).json()
    assert (pt["name"], pt["price"]) == ("Platinum, gram", 30.0)
    o = generic.get("/api/investments").json()
    gold = o["metals"]["gold"]
    assert gold["gross_gram"] == pytest.approx(62.207, abs=0.001) and gold["base_name"] == "Gold, gram"
    assert gold["base_equivalent"] == pytest.approx(62.207 * 100, rel=1e-4)
    assert o["currency"] == "USD"


def test_generic_metals_converted_to_eur(db, generic):
    prefs.save(db, base_currency="EUR", currencies=["EUR", "USD"])
    g = generic.post("/api/investments/assets", json={"kind": "gold", "code": "XAU_G"}).json()
    assert g["price"] == pytest.approx(100 / 1.10, abs=0.01)
    assert "XAU_G" not in generic.post("/api/investments/refresh").json()["failed"]


def test_generic_fx_assets(db, generic):
    e = generic.post("/api/investments/assets", json={"kind": "fx", "code": "EUR"}).json()
    assert (e["name"], e["unit"], e["price"], e["price_source"]) == ("Euro", "EUR", 1.1, "ECB (Frankfurter)")
    generic.post(f"/api/investments/assets/{e['id']}/lots", json={"date": "2026-01-01", "quantity": 1000, "unit_price": 1.05})
    j = generic.post("/api/investments/assets", json={"kind": "fx", "code": "JPY"}).json()
    assert j["price"] == 0.01  # rounded to cents
    assert generic.post("/api/investments/assets", json={"kind": "fx", "code": "USD"}).status_code == 400  # base itself
    assert generic.post("/api/investments/assets", json={"kind": "fx", "code": "XYZ"}).status_code == 400
    r = generic.post("/api/investments/refresh").json()
    assert r == {"updated": 2, "failed": []}
    a = generic.get("/api/investments").json()["assets"]
    eur = next(x for x in a if x["code"] == "EUR")
    assert (eur["value"], eur["cost"], eur["pl"]) == (1100, 1050, 50)


def test_generic_stocks_any_exchange(db, generic):
    a = generic.post("/api/investments/assets", json={"kind": "stock", "code": "aapl"}).json()
    assert (a["code"], a["price"], a["price_source"], a["unit"]) == ("AAPL", 200, "Yahoo Finance", "piece")
    sap = generic.post("/api/investments/assets", json={"kind": "stock", "code": "sap.de"}).json()
    assert (sap["code"], sap["price"]) == ("SAP.DE", 275.0)  # 250 EUR × 1.10
    vod = generic.post("/api/investments/assets", json={"kind": "stock", "code": "VOD.L"}).json()
    assert vod["price"] == 91.0  # 7000 pence = 70 GBP × 1.30
    # Without the "tr" region a bare ticker isn't sent to Borsa Istanbul
    assert generic.post("/api/investments/assets", json={"kind": "stock", "code": "THYAO"}).status_code == 400
    assert generic.post("/api/investments/assets", json={"kind": "stock", "code": "BAD TICKER"}).status_code == 400
    # The FX rate is fetched once and reused (stored in fx_rates)
    n = generic.calls["fx"]
    generic.post("/api/investments/refresh")
    assert generic.calls["fx"] == n


def test_generic_goal_in_other_currency(db, generic):
    db.add(SavingsGoal(name="Trip", currency="EUR", target_cents=500_000))
    db.commit()
    a = generic.post("/api/investments/assets", json={"kind": "stock", "code": "AAPL", "goal_id": 1}).json()
    generic.post(f"/api/investments/assets/{a['id']}/lots", json={"date": "2026-01-01", "quantity": 11, "unit_price": 150})
    from app.models import FxRate
    db.merge(FxRate(date=date.today(), currency="EUR", rate=1.10))
    db.commit()
    goal = db.get(SavingsGoal, 1)
    assert investments.goal_value_cents(db, goal, date.today()) == 200_000  # 2,200 USD = 2,000 EUR


def test_region_tr_with_non_try_base(db, monkeypatch):
    """Region pack "tr" with a EUR base: Grand Bazaar TRY prices and BIST quotes are converted to EUR."""
    prefs.save(db, base_currency="EUR", currencies=["EUR", "TRY"], region="tr", setup_done=True)
    monkeypatch.setattr(prices, "fetch_truncgil", lambda attempts=3, timeout=15: dict(FAKE))
    monkeypatch.setattr(prices, "fetch_fx", lambda base, codes, timeout=15: {"TRY": 0.02} if base == "EUR" and "TRY" in codes else {})
    monkeypatch.setattr(prices, "fetch_quote", lambda symbol, timeout=15: (300.0, "TRY") if symbol == "THYAO.IS" else _raise(prices.SymbolNotFound(symbol)))
    c = TestClient(app)
    q = c.post("/api/investments/assets", json={"kind": "gold", "code": "CEYREKALTIN"}).json()
    assert q["price"] == 210.0 and q["price_source"] == "Grand Bazaar (truncgil)"  # 10,500 TRY × 0.02
    s = c.post("/api/investments/assets", json={"kind": "stock", "code": "THYAO"}).json()
    assert (s["code"], s["price"], s["price_source"]) == ("THYAO", 6.0, "Borsa Istanbul (Yahoo)")
    usd = c.post("/api/investments/assets", json={"kind": "fx", "code": "USD"}).json()
    assert usd["price"] == 0.98  # 49 TRY × 0.02
    assert c.post("/api/investments/assets", json={"kind": "gold", "code": "XAU_G"}).status_code == 400


def test_generic_telegram_parsing(db):
    from app.telegram_invest import parse_investment
    prefs.save(db, base_currency="USD", currencies=["USD", "EUR"], region="", setup_done=True)
    assert parse_investment("bought 10g gold 1000 usd") == {"kind": "gold", "code": "XAU_G", "side": "buy", "quantity": 10.0, "unit_price": 100.0}
    oz = parse_investment("bought 2 oz gold $6000")
    assert (oz["code"], oz["quantity"], oz["unit_price"]) == ("XAU_OZ", 2.0, 3000.0)
    assert parse_investment("sold 100g silver")["code"] == "XAG_G"
    assert parse_investment("bought 5g platinum")["code"] == "XPT_G"
    eur = parse_investment("bought 500 euros 540 usd")
    assert (eur["kind"], eur["code"], eur["unit_price"]) == ("fx", "EUR", 1.08)
    assert parse_investment("bought 500 dollars") is None  # the base currency isn't an investment
    assert parse_investment("bought 10 shares of AAPL at 200 each")["code"] == "AAPL"
    assert parse_investment("bought 3 shares sap.de")["code"] == "SAP.DE"
    assert parse_investment("gold necklace 300") is None


def test_generic_telegram_describe_and_apply(db, monkeypatch):
    from app import telegram_invest
    from app.models import User
    prefs.save(db, base_currency="EUR", currencies=["EUR", "USD"], region="", setup_done=True)
    monkeypatch.setattr(prices, "fetch_quote", lambda symbol, timeout=15: QUOTES[symbol])
    monkeypatch.setattr(prices, "fetch_fx", lambda base, codes, timeout=15: {"USD": 0.9} if "USD" in codes else {})
    user = User(ha_user_id="u", name="U", username="u")
    db.add(user)
    db.commit()
    q = telegram_invest.parse_investment("bought 2 shares AAPL")
    text, data = telegram_invest.describe(db, q)
    assert data["unit_price"] == 180.0 and "360.00 EUR" in text
    reply = telegram_invest.apply(db, user, data, date.today())
    assert "holding 2 piece" in reply and "360.00 EUR" in reply
