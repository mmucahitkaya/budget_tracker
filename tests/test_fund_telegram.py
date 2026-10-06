from datetime import date

from app import telegram, telegram_invest
from app.models import Asset, SentNotice, User


def _setup(db):
    bob = User(ha_user_id="s", name="Bob", username="bob", telegram_chat_id=222)
    alice = User(ha_user_id="m", name="alice", username="alice", telegram_chat_id=111)
    db.add_all([bob, alice])
    db.flush()
    hph = Asset(kind="fund", code="HPH", name="HPH · Example Bank", owner_id=bob.id,
                manual_value_cents=3328435, manual_value_date=date(2026, 10, 5), manual_cost_cents=2147062)
    db.add(hph)
    db.commit()
    return bob, alice, hph


def test_direct_fund_update_and_undo(db):
    bob, alice, hph = _setup(db)
    assert telegram_invest.match_fund_update(db, bob, "HPH 34150,20") == (hph, 34150.20)
    assert telegram_invest.match_fund_update(db, bob, "fund HPH 34150.20") == (hph, 34150.20)
    assert telegram_invest.match_fund_update(db, bob, "fon hph 34.150,20 tl") == (hph, 34150.20)
    # Someone else's fund and non-fund messages don't count as updates
    assert telegram_invest.match_fund_update(db, alice, "HPH 34150") is None
    assert telegram_invest.match_fund_update(db, bob, "kebap 500") is None
    assert telegram_invest.match_fund_update(db, bob, "HPH") is None

    text, undo = telegram_invest.update_fund(db, bob, hph, 34150.20)
    assert hph.manual_value_cents == 3415020 and hph.manual_value_date == date.today()
    assert "34,150.20" in text and "Vs. cost" in text
    telegram_invest.undo_fund(db, bob, undo)
    assert hph.manual_value_cents == 3328435 and hph.manual_value_date == date(2026, 10, 5)


def test_monthly_reminder_once_per_owner(db, monkeypatch):
    bob, alice, hph = _setup(db)
    sent = []
    monkeypatch.setattr(telegram, "enabled", lambda: True)
    monkeypatch.setattr(telegram, "send", lambda chat, text, buttons=None: sent.append((chat, text)))
    monkeypatch.setattr(telegram, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    assert telegram.send_fund_reminders(date(2026, 11, 1)) == 1
    assert sent[0][0] == 222 and "HPH" in sent[0][1] and "HPH 34150.20" in sent[0][1]
    assert telegram.send_fund_reminders(date(2026, 11, 1)) == 0  # not again in the same month
    assert db.query(SentNotice).count() == 1
