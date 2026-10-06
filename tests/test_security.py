"""Tests for security fixes (CSV, linking, allowed users, Telegram deletion)."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import services, telegram
from app.config import settings
from app.main import app
from app.models import TelegramMessage, User


@pytest.fixture
def client(db):
    return TestClient(app)


def test_csv_export_neutralizes_formulas(db, client):
    from datetime import date

    services.create_transaction(db, kind="expense", amount_cents=100, currency="TRY", on=date(2026, 10, 1),
                                category_id=1, user_id=None, payment_method="cash", card_id=None,
                                merchant="=1+1", note="@SUM(A1)")
    db.commit()
    body = client.get("/api/transactions/export.csv").text
    assert "'=1+1" in body and "'@SUM(A1)" in body
    assert ";=1+1" not in body


def test_new_link_code_revokes_previous(db):
    a = telegram.new_link_code(1)
    b = telegram.new_link_code(1)
    assert a not in telegram._link_codes and b in telegram._link_codes


def test_link_attempts_are_rate_limited(db, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "x")
    monkeypatch.setattr(telegram, "send", lambda chat, text, buttons=None: sent.append(text))
    telegram._failed_links.clear()
    user = User(ha_user_id="u", name="Alice")
    db.add(user)
    db.commit()
    code = telegram.new_link_code(user.id)
    for i in range(telegram.MAX_FAILED_LINKS):
        telegram._handle_message({"chat": {"id": 7, "type": "private"}, "text": f"/link {i:06d}"})
    # Even the correct code is rejected for an hour
    telegram._handle_message({"chat": {"id": 7, "type": "private"}, "text": f"/link {code}"})
    db.refresh(user)
    assert user.telegram_chat_id is None
    assert len(sent) == telegram.MAX_FAILED_LINKS


def test_allowed_users(db, client, monkeypatch):
    monkeypatch.setattr(settings, "allowed_users", ["alice", "Zoë"])
    # HA sends the header as UTF-8 bytes (non-ASCII name on purpose)
    ok = client.get("/api/me", headers=[(b"x-dev-user-id", b"a"), (b"x-dev-user-name", "Zoë".encode())])
    assert ok.status_code == 200
    denied = client.get("/api/me", headers={"x-dev-user-id": "k", "x-dev-user-name": "Kiosk"})
    assert denied.status_code == 403
    assert db.query(User).filter_by(ha_user_id="k").count() == 0  # no record is created for a rejected user


def test_purge_deletes_due_messages(db, monkeypatch):
    calls = []
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "x")
    monkeypatch.setattr(telegram, "_call", lambda method, http_timeout=30, **p: calls.append((method, p)) or True)
    db.add(TelegramMessage(chat_id=1, message_id=10, delete_after=datetime.now() - timedelta(minutes=1)))
    db.add(TelegramMessage(chat_id=1, message_id=11, delete_after=datetime.now() + timedelta(hours=5)))
    db.commit()
    assert telegram.purge_due() == 1
    assert calls == [("deleteMessage", {"chat_id": 1, "message_id": 10})]
    assert [m.message_id for m in db.query(TelegramMessage)] == [11]


def test_sent_messages_are_scheduled(db, monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "x")
    monkeypatch.setattr(telegram, "_call", lambda method, http_timeout=30, **p: {"message_id": 99})
    telegram.send(5, "hello")
    m = db.query(TelegramMessage).one()
    assert m.message_id == 99 and timedelta(hours=23) < m.delete_after - datetime.now() <= timedelta(hours=24)
    monkeypatch.setattr(telegram.settings, "telegram_auto_delete_hours", 0)
    telegram.send(5, "permanent")
    assert db.query(TelegramMessage).count() == 1


def test_bot_respects_allowed_users_by_username(db, monkeypatch):
    monkeypatch.setattr(settings, "allowed_users", ["alice", "bob"])
    bob = User(ha_user_id="s", name="Bob", username="bob", telegram_chat_id=42)
    kiosk = User(ha_user_id="k", name="Kiosk", username="kiosk", telegram_chat_id=43)
    db.add_all([bob, kiosk])
    db.commit()
    assert telegram._user_for_chat(db, 42).id == bob.id
    assert telegram._user_for_chat(db, 43) is None
