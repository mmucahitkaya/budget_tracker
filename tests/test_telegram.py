from datetime import date

import pytest

from app import telegram
from app.models import Document, Transaction, User


@pytest.fixture
def bot(db, monkeypatch):
    sent = []
    calls = []
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "x")
    monkeypatch.setattr(telegram, "send", lambda chat, text, buttons=None: sent.append((chat, text, buttons)))
    monkeypatch.setattr(telegram, "_call", lambda method, http_timeout=30, **p: calls.append((method, p)) or {})
    user = User(ha_user_id="u1", name="Alice")
    db.add(user)
    db.commit()
    return {"sent": sent, "calls": calls, "user": user, "db": db}


def msg(text, chat=111, chat_type="private"):
    return {"chat": {"id": chat, "type": chat_type}, "text": text}


def test_unlinked_users_are_ignored_and_linking_works(bot):
    telegram._handle_message(msg("/summary"))
    assert "linked" in bot["sent"][-1][1]  # no summary given, linking requested
    telegram._handle_message(msg("/link 000000"))
    assert "invalid" in bot["sent"][-1][1]
    code = telegram.new_link_code(bot["user"].id)
    telegram._handle_message(msg(f"/link {code}"))
    bot["db"].refresh(bot["user"])
    assert bot["user"].telegram_chat_id == 111
    # The app's "Open in Telegram" link: /start link_<code>
    code2 = telegram.new_link_code(bot["user"].id)
    telegram._handle_message(msg(f"/start link_{code2}", chat=333))
    bot["db"].refresh(bot["user"])
    assert bot["user"].telegram_chat_id == 333
    # Codes are single-use
    telegram._handle_message(msg(f"/link {code}", chat=222))
    assert "invalid" in bot["sent"][-1][1]


def test_group_chats_are_left(bot):
    telegram._handle_message(msg("/summary", chat=-500, chat_type="group"))
    assert bot["calls"][-1] == ("leaveChat", {"chat_id": -500})
    assert not bot["sent"]


def test_quick_entry_and_undo(bot):
    bot["user"].telegram_chat_id = 111
    bot["db"].commit()
    telegram._handle_message(msg("1.250,50 migros"))
    tx = bot["db"].query(Transaction).one()
    assert tx.amount_cents == 125050 and tx.merchant == "Migros" and tx.date == date.today()
    chat, text, buttons = bot["sent"][-1]
    assert "1,250.50 TRY" in text and buttons[0][1][1] == f"undo:{tx.id}"
    telegram._handle_callback({"id": "c", "data": f"undo:{tx.id}", "message": {"chat": {"id": 111}, "message_id": 1, "text": text}})
    assert bot["db"].query(Transaction).count() == 0


def test_receipt_save_button(bot):
    bot["user"].telegram_chat_id = 111
    doc = Document(filename="f.jpg", stored_path="/x", mime="image/jpeg", status="review", telegram_chat_id=111)
    doc.result = {"draft": {"doc_type": "receipt", "card_id": None, "statement": None, "rows": [{
        "include": False, "mode": "transaction", "kind": "expense", "date": "2026-10-01", "amount": 99.9,
        "currency": "TRY", "merchant": "Migros", "category_id": 1, "payment_method": "cash", "card_id": None,
        "installment_count": None, "installment_no": None, "duplicate_of": 5, "items": None}]}}
    bot["db"].add(doc)
    bot["db"].commit()
    telegram.document_done(doc)
    assert "Save anyway" in bot["sent"][-1][2][0][0][0]
    telegram._handle_callback({"id": "c", "data": f"save:{doc.id}", "message": {"chat": {"id": 111}, "message_id": 1, "text": "x"}})
    assert bot["db"].query(Transaction).one().amount_cents == 9990


def test_unlinked_callbacks_rejected(bot):
    telegram._handle_callback({"id": "c", "data": "undo:1", "message": {"chat": {"id": 999}, "message_id": 1, "text": "x"}})
    assert bot["calls"][-1][1]["text"] == "Unauthorized"


def test_get_updates_passes_telegram_timeout(monkeypatch):
    """getUpdates' long-poll 'timeout' parameter must not clash with the HTTP timeout."""
    seen = {}

    class Resp:
        def json(self):
            return {"ok": True, "result": []}

    def fake_post(url, json, timeout):
        seen.update(json=json, http_timeout=timeout)
        return Resp()

    monkeypatch.setattr(telegram.httpx, "post", fake_post)
    assert telegram._call("getUpdates", http_timeout=60, timeout=50, offset=3) == []
    assert seen == {"json": {"timeout": 50, "offset": 3}, "http_timeout": 60}


def test_statement_without_card_creates_card(bot):
    from app.models import CardStatement, CreditCard

    bot["user"].telegram_chat_id = 111
    doc = Document(filename="e.pdf", stored_path="/x", mime="application/pdf", status="review", telegram_chat_id=111)
    doc.result = {
        "parsed": {"bank": "T. Garanti Bankası A.Ş.", "card_last4": "1019"},
        "draft": {"doc_type": "statement", "card_id": None,
                  "statement": {"period_end": "2026-09-26", "due_date": "2026-10-06", "currency": "TRY", "totals": {"TRY": 500},
                                "min_payment": 100},
                  "rows": [{"include": True, "mode": "transaction", "kind": "expense", "date": "2026-09-10", "amount": 500,
                            "currency": "TRY", "merchant": "A101", "category_id": 1, "payment_method": "card", "card_id": None,
                            "installment_count": None, "installment_no": None, "duplicate_of": None, "items": None}]},
    }
    bot["db"].add(doc)
    bot["db"].commit()
    telegram.document_done(doc)
    assert bot["sent"][-1][2][0][0][1] == f"newcard:{doc.id}"
    telegram._handle_callback({"id": "c", "data": f"newcard:{doc.id}", "message": {"chat": {"id": 111}, "message_id": 1, "text": "x"}})
    card = bot["db"].query(CreditCard).one()
    assert (card.name, card.last4, card.statement_day, card.due_day) == ("Garanti", "1019", 26, 6)
    assert card.debts.get("TRY") == 50000 and bot["db"].query(CardStatement).count() == 1
    assert bot["db"].query(Transaction).one().card_id == card.id


@pytest.mark.parametrize(
    "text, amount, currency, desc, cash, yesterday",
    [
        ("cafer usta kebap 500 lira", 500, "TRY", "Cafer usta kebap", False, False),
        ("Cafer Usta kebap 500", 500, "TRY", "Cafer Usta kebap", False, False),
        ("500 tl cafer usta", 500, "TRY", "Cafer usta", False, False),
        ("₺1.250,50 migros", 1250.5, "TRY", "Migros", False, False),
        ("dün a101 230 nakit", 230, "TRY", "A101", True, True),
        ("çay 2 tane 30 lira", 30, "TRY", "Çay 2 tane", False, False),
        ("netflix 20 dolar", 20, "USD", "Netflix", False, False),
        ("15 euro müze", 15, "EUR", "Müze", False, False),
        ("yesterday groceries 230 cash", 230, "TRY", "Groceries", True, True),
        ("netflix 20 dollars", 20, "USD", "Netflix", False, False),
        ("$15 museum", 15, "USD", "Museum", False, False),
    ],
)
def test_parse_quick(db, text, amount, currency, desc, cash, yesterday):
    from datetime import timedelta

    q = telegram.parse_quick(text)
    assert (q["amount"], q["currency"], q["desc"], q["cash"]) == (amount, currency, desc, cash)
    assert q["date"] == date.today() - timedelta(days=1 if yesterday else 0)


@pytest.mark.parametrize(
    "text, amount, currency, desc",
    [
        ("coffee 4.50", 4.5, "USD", "Coffee"),
        ("lunch 1,250.50", 1250.5, "USD", "Lunch"),
        ("£5 sandwich", 5, "GBP", "Sandwich"),
        ("museum 15 eur", 15, "EUR", "Museum"),
        ("kebap 500 tl", 500, "TRY", "Kebap"),
        ("taxi 12 chf", 12, "CHF", "Taxi"),
    ],
)
def test_parse_quick_generic_base(db, text, amount, currency, desc):
    from app import prefs

    prefs.save(db, base_currency="USD", currencies=["USD", "EUR", "GBP"], region="")
    q = telegram.parse_quick(text)
    assert (q["amount"], q["currency"], q["desc"]) == (amount, currency, desc)
    assert telegram._money(1234.5) == "1,234.50 USD"


def test_parse_quick_rejects_non_entries():
    assert telegram.parse_quick("merhaba") is None
    assert telegram.parse_quick("hello") is None
    assert telegram.parse_quick("500") is None


def test_quick_entry_keyword_category_and_buttons(bot):
    from app.models import Category, MerchantRule

    bot["user"].telegram_chat_id = 111
    bot["db"].commit()
    telegram._handle_message(msg("cafer usta kebap 500 lira"))
    tx = bot["db"].query(Transaction).one()
    rest = bot["db"].query(Category).filter_by(name="Restaurants & Cafes").one()
    assert tx.category_id == rest.id and tx.amount_cents == 50000 and tx.payment_method == "cash"

    # Unknown merchant → category buttons; the choice is learned
    telegram._handle_message(msg("ahmet usta 300"))
    chat, text, buttons = bot["sent"][-1]
    assert "Pick a category" in text
    tx2 = bot["db"].query(Transaction).order_by(Transaction.id.desc()).first()
    market = bot["db"].query(Category).filter_by(name="Groceries").one()
    assert f"cat:{tx2.id}:{market.id}" in [d for row in buttons for _, d in row]
    telegram._handle_callback({"id": "c", "data": f"cat:{tx2.id}:{market.id}", "message": {"chat": {"id": 111}, "message_id": 1, "text": text}})
    bot["db"].refresh(tx2)
    assert tx2.category_id == market.id
    assert bot["db"].query(MerchantRule).filter_by(pattern="ahmet usta").one().category_id == market.id
    telegram._handle_message(msg("ahmet usta 120"))  # now automatically Groceries
    assert bot["db"].query(Transaction).order_by(Transaction.id.desc()).first().category_id == market.id


def test_quick_entry_uses_named_card(bot):
    from app.models import CreditCard

    bot["user"].telegram_chat_id = 111
    card = CreditCard(name="Bonus alice", last4="1019", statement_day=26, due_day=6)
    bot["db"].add(card)
    bot["db"].commit()
    telegram._handle_message(msg("kebap 500 bonus"))
    tx = bot["db"].query(Transaction).one()
    assert tx.card_id == card.id and tx.payment_method == "card" and tx.merchant == "Kebap"


def test_quick_entry_records_message_time_and_statement_links_card(bot):
    from datetime import datetime

    from app import services
    from app.models import CreditCard

    bot["user"].telegram_chat_id = 111
    card = CreditCard(name="Bonus", bank="Garanti", last4="1019", statement_day=26, due_day=6)
    bot["db"].add(card)
    bot["db"].commit()
    sent = datetime(2026, 9, 14, 20, 45)
    m = msg("cafer usta kebap 500 lira")
    m["date"] = int(sent.replace(tzinfo=telegram.settings.tz).timestamp())
    telegram._handle_message(m)
    tx = bot["db"].query(Transaction).one()
    assert tx.date == sent.date() and tx.occurred_at == sent and tx.card_id is None
    assert "20:45" in bot["sent"][-1][1]

    # Statement arrived: same amount, next day → not duplicated, existing entry is linked to the card
    doc = Document(filename="e.pdf", stored_path="/x", mime="application/pdf", status="review")
    bot["db"].add(doc)
    bot["db"].commit()
    parsed = {"doc_type": "statement", "card_last4": "1019", "period_end": "2026-09-26", "due_date": "2026-10-06",
              "currency": "TRY", "totals": [{"currency": "TRY", "amount": 500}], "items": [{"date": "2026-09-15", "description": "CAFER USTA KEBAP SALONU",
              "amount": 500, "currency": "TRY", "is_refund": False, "installment_no": None, "installment_count": None,
              "category": "Restaurants & Cafes"}]}
    draft = services.build_draft(bot["db"], doc, parsed)
    assert draft["rows"][0]["duplicate_of"] == tx.id and draft["rows"][0]["include"] is False
    assert services.commit_draft(bot["db"], doc, draft, bot["user"].id) == 0
    bot["db"].refresh(tx)
    assert bot["db"].query(Transaction).count() == 1
    assert (tx.card_id, tx.payment_method, tx.merchant) == (card.id, "card", "Cafer usta kebap")


@pytest.mark.parametrize(
    "text, kind, code, side, qty, unit_price",
    [
        ("10gr altın", "gold", "GRA", "buy", 10, None),
        ("10 gram altın aldım", "gold", "GRA", "buy", 10, None),
        ("3 çeyrek 15000tl", "gold", "CEYREKALTIN", "buy", 3, 5000),
        ("3 ceyrek 15.000 tl", "gold", "CEYREKALTIN", "buy", 3, 5000),
        ("2 çeyrek tanesi 10500", "gold", "CEYREKALTIN", "buy", 2, 10500),
        ("1 tam altın 42000", "gold", "TAMALTIN", "buy", 1, 42000),
        ("5 gr bilezik sattım 30000tl", "gold", "YIA", "sell", 5, 6000),
        ("2 adet 22 ayar 12000 tl", "gold", "YIA", "buy", 2, 6000),
        ("100 gr gümüş", "silver", "GUMUS", "buy", 100, None),
        ("500 dolar aldım 24600tl", "fx", "USD", "buy", 500, 49.2),
        ("200 euro bozdurdum", "fx", "EUR", "sell", 200, None),
        ("THYAO 10 lot 2950tl", "stock", "THYAO", "buy", 10, 295),
        ("10g gold", "gold", "GRA", "buy", 10, None),
        ("100 g silver", "silver", "GUMUS", "buy", 100, None),
        ("bought 500 dollars 24600tl", "fx", "USD", "buy", 500, 49.2),
        ("sold 200 euros", "fx", "EUR", "sell", 200, None),
        ("THYAO 10 shares 2950tl", "stock", "THYAO", "buy", 10, 295),
    ],
)
def test_parse_investment(db, text, kind, code, side, qty, unit_price):
    from app.telegram_invest import parse_investment

    r = parse_investment(text)
    assert (r["kind"], r["code"], r["side"], r["quantity"]) == (kind, code, side, qty)
    assert r["unit_price"] == (pytest.approx(unit_price) if unit_price else None)


@pytest.mark.parametrize(
    "text", ["netflix 20 dolar", "cafer usta kebap 500 lira", "tam 5 kilo elma 300", "migros 845,90", "netflix 20 dollars", "gold ring 5000"]
)
def test_expenses_are_not_investments(text):
    from app.telegram_invest import parse_investment

    assert parse_investment(text) is None


def test_bot_investment_flow(bot, monkeypatch):
    from app import prices
    from app.models import Asset, AssetLot

    monkeypatch.setattr(prices, "fetch_truncgil", lambda attempts=3, timeout=15: {"CEYREKALTIN": 10500.0, "GRA": 6500.0})
    bot["user"].telegram_chat_id = 111
    bot["db"].commit()
    telegram._handle_message(msg("3 çeyrek 15000tl"))
    chat, text, buttons = bot["sent"][-1]
    name = prices.catalog_item("gold", "CEYREKALTIN")["name"]
    assert f"{name} purchase" in text and "5,000.00 TRY" in text and "15,000.00 TRY" in text
    assert bot["db"].query(Transaction).count() == 0  # not saved as an expense
    token = buttons[0][0][1].split(":")[1]
    telegram._handle_callback({"id": "c", "data": f"inv:{token}", "message": {"chat": {"id": 111}, "message_id": 1, "text": text}})
    a = bot["db"].query(Asset).one()
    assert (a.code, a.owner_id) == ("CEYREKALTIN", bot["user"].id)
    assert bot["db"].query(AssetLot).one().unit_price_cents == 500000
    # No price: current price; selling more than held is rejected
    telegram._handle_message(msg("10gr altın"))
    assert "6,500.00 TRY" in bot["sent"][-1][1] and "current buy price" in bot["sent"][-1][1]
    telegram._handle_message(msg("5 çeyrek sattım 60000tl"))
    tok = bot["sent"][-1][2][0][0][1].split(":")[1]
    telegram._handle_callback({"id": "c", "data": f"inv:{tok}", "message": {"chat": {"id": 111}, "message_id": 2, "text": "x"}})
    assert bot["db"].query(AssetLot).count() == 1


def test_bot_purchase_skips_wedding_gift_asset(bot, monkeypatch):
    from app import prices
    from app.models import Asset, AssetLot
    from app.telegram_invest import GIFT_NOTE

    monkeypatch.setattr(prices, "fetch_truncgil", lambda attempts=3, timeout=15: {"CEYREKALTIN": 10500.0})
    bot["user"].telegram_chat_id = 111
    gift = Asset(kind="gold", code="CEYREKALTIN", name="Quarter gold · wedding", note=GIFT_NOTE)
    bot["db"].add(gift)
    bot["db"].commit()
    telegram._handle_message(msg("2 çeyrek 21000tl"))
    tok = bot["sent"][-1][2][0][0][1].split(":")[1]
    telegram._handle_callback({"id": "c", "data": f"inv:{tok}", "message": {"chat": {"id": 111}, "message_id": 1, "text": "x"}})
    lot = bot["db"].query(AssetLot).one()
    assert lot.asset_id != gift.id and bot["db"].query(Asset).count() == 2


def test_parse_palladium(db):
    from app.telegram_invest import parse_investment

    assert parse_investment("0,3 gr paladyum")["code"] == "PAL"


def test_statement_warning(db):
    from app.telegram import statement_warning
    rows = [{"date": "2026-06-19", "amount": 100, "line_amount": 100, "kind": "expense", "currency": "TRY"} for _ in range(5)]
    w = statement_warning({"statement": {"currency": "TRY", "totals": {"TRY": 10682.14}}, "rows": rows})
    assert "don't add up" in w and "same date" in w
    ok = [{"date": f"2026-08-{10 + i}", "amount": 100, "line_amount": 100, "kind": "expense", "currency": "TRY"} for i in range(5)]
    assert statement_warning({"statement": {"currency": "TRY", "period_spending": 500}, "rows": ok}) is None
