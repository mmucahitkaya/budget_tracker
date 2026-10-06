"""Telegram bot: send receipts/statements, quick expense entry, summaries and notifications.

The bot connects to Telegram itself (long polling); HA does not need to be exposed to the internet.
Only users linked with a code obtained from the app can use the bot.
"""
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import func, select

from . import audit, finance, investments, prefs, telegram_invest
from .config import UPLOAD_DIR, settings
from .db import SessionLocal
from .models import Asset, Category, CreditCard, Document, TelegramMessage, Transaction, User

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
_link_codes: dict[str, tuple[int, float]] = {}  # code → (user id, expiry)
_bot_username: str | None = None

def help_text() -> str:
    """Bot help; examples follow the household's region pack."""
    from . import prefs

    if prefs.region() == "tr":
        quick = "<code>kebap 500</code>, <code>benzin 1250,50</code>, <code>dün market 230 nakit</code>, <code>netflix 20 dolar</code>"
        invest = "<code>10gr altın</code>, <code>3 çeyrek 15000tl</code>, <code>THYAO 10 lot 2950tl</code>"
        fund = "HPH 34150,20"
    else:
        quick = "<code>coffee 4.50</code>, <code>gas 45</code>, <code>yesterday groceries 62.30 cash</code>, <code>netflix 15.49</code>"
        invest = "<code>2 oz gold</code>, <code>AAPL 5 shares 190</code>, <code>bought 500 eur</code>"
        fund = "MYFUND 12500"
    return (
        "📸 Send a 📷 receipt photo or a 📄 card statement PDF and I'll read it and send you a summary.\n"
        "Tip: send long receipts <b>as a file</b> so I can read them clearly.\n\n"
        f"✍️ Quick entry: {quick}\n"
        f"🪙 Investments: {invest}\n"
        "📊 /summary — this month's summary\n"
        "🧾 /recent — recent transactions\n"
        "📅 /upcoming — upcoming payments\n"
        f"📈 /fund — update fund values (or just send <code>{fund}</code>)"
    )


def enabled() -> bool:
    return bool(settings.telegram_bot_token)


def bot_username() -> str | None:
    return _bot_username


def _call(method: str, http_timeout: float = 30, **params) -> dict | None:
    """Bot API call. The HTTP timeout is separate so it doesn't clash with Telegram's own 'timeout' parameter."""
    try:
        r = httpx.post(f"{API}/bot{settings.telegram_bot_token}/{method}", json=params, timeout=http_timeout)
        data = r.json()
        if not data.get("ok"):
            log.warning("Telegram %s error: %s", method, data.get("description"))
            return None
        return data.get("result")
    except Exception as e:
        # The error message may contain the URL (bot token)
        log.warning("Telegram %s call failed: %s", method, str(e).replace(settings.telegram_bot_token, "***"))
        return None


def send(chat_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
    params: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {
            "inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]
        }
    msg = _call("sendMessage", **params)
    if isinstance(msg, dict) and msg.get("message_id"):
        schedule_delete(chat_id, msg["message_id"])


# ---- Auto-delete ----------------------------------------------------------------


def schedule_delete(chat_id: int, message_id: int) -> None:
    """Schedules the message for deletion after telegram_auto_delete_hours (0 = off)."""
    hours = settings.telegram_auto_delete_hours
    if hours <= 0:
        return
    db = SessionLocal()
    try:
        db.add(TelegramMessage(chat_id=chat_id, message_id=message_id, delete_after=datetime.now() + timedelta(hours=hours)))
        db.commit()
    finally:
        db.close()


def delete_message(chat_id: int, message_id: int) -> None:
    _call("deleteMessage", chat_id=chat_id, message_id=message_id)


def purge_due() -> int:
    """Deletes messages that are due. Telegram doesn't allow deleting messages older than 48 hours;
    those that can't be deleted are removed from the list as well."""
    if not enabled():
        return 0
    db = SessionLocal()
    try:
        due = db.scalars(select(TelegramMessage).where(TelegramMessage.delete_after <= datetime.now()).limit(500)).all()
        for m in due:
            delete_message(m.chat_id, m.message_id)
            db.delete(m)
        db.commit()
        return len(due)
    finally:
        db.close()


def broadcast(title: str, message: str) -> None:
    """Also sends app notifications to all linked Telegram accounts."""
    if not enabled():
        return
    db = SessionLocal()
    try:
        chats = [u.telegram_chat_id for u in db.scalars(select(User).where(User.telegram_chat_id.is_not(None)))]
    finally:
        db.close()
    for chat_id in chats:
        send(chat_id, f"<b>{_esc(title)}</b>\n{_esc(message)}")


LINK_TTL = 600  # seconds
MAX_FAILED_LINKS = 5  # per chat, per hour
_failed_links: dict[int, list[float]] = {}


def new_link_code(user_id: int) -> str:
    """One valid code per user: generating a new one invalidates the old one."""
    now = time.time()
    for code, (uid, exp) in list(_link_codes.items()):
        if exp < now or uid == user_id:
            del _link_codes[code]
    code = f"{secrets.randbelow(1_000_000):06d}"
    while code in _link_codes:
        code = f"{secrets.randbelow(1_000_000):06d}"
    _link_codes[code] = (user_id, now + LINK_TTL)
    return code


def _link_blocked(chat_id: int) -> bool:
    now = time.time()
    recent = [t for t in _failed_links.get(chat_id, []) if now - t < 3600]
    _failed_links[chat_id] = recent
    return len(recent) >= MAX_FAILED_LINKS


# ---- Formatting ---------------------------------------------------------------


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _money(amount: float, currency: str | None = None) -> str:
    """Human-readable amount for bot messages, e.g. "1,234.00 USD" (defaults to the base currency)."""
    return finance.format_money(finance.to_cents(amount), currency or prefs.base())


def _money_multi(amounts: dict[str, float], primary: str | None = None) -> str:
    """Amounts in several currencies, primary first: "1,234.00 USD + 50.00 EUR"."""
    primary = primary or prefs.base()
    parts = [(c, v) for c, v in sorted(amounts.items(), key=lambda x: (x[0] != primary, x[0])) if v]
    return " + ".join(_money(v, c) for c, v in parts) or _money(0, primary)


def _date(s: str | date) -> str:
    d = date.fromisoformat(s) if isinstance(s, str) else s
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{months[d.month - 1]} {d.day}"


# ---- Document result ----------------------------------------------------------


def document_done(doc: Document) -> None:
    """When a document received via Telegram has been processed, sends the result and confirmation buttons."""
    if not enabled() or not doc.telegram_chat_id:
        return
    chat = doc.telegram_chat_id
    if doc.telegram_message_id and settings.telegram_auto_delete_hours > 0:
        # We have the document now: don't leave the photo/PDF sitting in Telegram
        delete_message(chat, doc.telegram_message_id)
    if doc.status == "error":
        send(chat, f"⚠️ Couldn't read the document: {_esc(doc.error or 'unknown error')}", [[("🔁 Retry", f"retry:{doc.id}")]])
        return
    draft = (doc.result or {}).get("draft") or {}
    rows = draft.get("rows", [])
    db = SessionLocal()
    try:
        cats = {c.id: c for c in db.scalars(select(Category))}
        card = db.get(CreditCard, draft["card_id"]) if draft.get("card_id") else None
    finally:
        db.close()

    if draft.get("doc_type") == "receipt" and rows:
        r = rows[0]
        cat = cats.get(r.get("category_id"))
        lines = [
            f"🧾 <b>{_esc(r.get('merchant') or 'Receipt')}</b> · {_date(r['date'])}",
            f"<b>{_money(r['amount'], r['currency'])}</b> · {cat.icon + ' ' + cat.name if cat else 'Uncategorized'}",
        ]
        if r.get("payment_method") == "card":
            lines.append(f"💳 {_esc(card.name) if card else 'Credit card'}" + (f" · {r['installment_count']} installments" if r.get("installment_count") else ""))
        if r.get("items"):
            lines.append(f"{len(r['items'])} items")
        if r.get("duplicate_of"):
            lines.append("⚠️ A transaction with the same amount may already be recorded.")
        save_label = "✅ Save anyway" if r.get("duplicate_of") else "✅ Save"
        send(chat, "\n".join(lines), [[(save_label, f"save:{doc.id}"), ("🗑 Delete", f"discard:{doc.id}")]])
        return

    if draft.get("doc_type") == "statement":
        st = draft.get("statement") or {}
        included = [r for r in rows if r.get("include")]
        primary = st.get("currency") or prefs.base()
        spend = sum(r.get("line_amount", r["amount"]) for r in rows
                    if r["kind"] == "expense" and r.get("currency", primary) == primary)
        lines = [
            f"📄 <b>Statement</b> · {_esc(card.name) if card else 'no matching card'}",
            f"Statement balance: <b>{_money_multi(st.get('totals') or {}, primary)}</b>",
            f"Due: {_date(st['due_date'])}" + (f" · minimum {_money(st['min_payment'], primary)}" if st.get("min_payment") else ""),
            f"{len(rows)} lines read ({_money(spend, primary)}), {len(included)} will be saved.",
        ]
        if warn := statement_warning(draft):
            lines.append(warn)
        matched = sum(1 for r in rows if r.get("match"))
        suggested = sum(1 for r in rows if not r.get("match") and r.get("match_candidates"))
        if matched:
            lines.append(f"🔗 {matched} lines matched entries you added earlier (they won't be duplicated, just linked to the card).")
        if suggested:
            lines.append(f"🔍 {suggested} lines have a possible match; you can decide on the review screen in the app.")
        if not card:
            parsed = (doc.result or {}).get("parsed") or {}
            lines.append(
                f"⚠️ This card isn't in the app yet (…{_esc(parsed.get('card_last4') or '?')}). "
                "I can create it from the statement details (bank, last 4 digits, statement and due day) and save."
            )
            send(chat, "\n".join(lines), [[("➕ Create card and save", f"newcard:{doc.id}")], [("🗑 Delete", f"discard:{doc.id}")]])
            return
        if statement_warning(draft):
            lines.append("It's recommended to review it under Documents in the app before saving.")
            save = "⚠️ Save anyway"
        else:
            lines.append("You can also review each line in the app.")
            save = "✅ Save all"
        send(chat, "\n".join(lines), [[(save, f"save:{doc.id}"), ("🗑 Delete", f"discard:{doc.id}")]])


def statement_warning(draft: dict) -> str | None:
    """Warning shown to the user if the read looks suspicious (line total doesn't match / all dates identical)."""
    st = draft.get("statement") or {}
    primary = st.get("currency") or prefs.base()
    rows = [r for r in draft.get("rows", []) if r.get("currency", primary) == primary]
    # Lines parsed rule-based from a text PDF are reliable; the statement balance also includes previous balance/payments
    target = st.get("period_spending") or (None if draft.get("source") == "text" else (st.get("totals") or {}).get(primary))
    net = sum(r.get("line_amount", r["amount"]) * (1 if r["kind"] == "expense" else -1) for r in rows)
    gross = sum(r.get("line_amount", r["amount"]) for r in rows if r["kind"] == "expense")
    problems = []
    if target and min(abs(net - target), abs(gross - target)) > max(1.0, target * 0.01):
        problems.append(f"lines read ({_money(net, primary)}) don't add up to the statement total ({_money(target, primary)})")
    dates = {r["date"] for r in rows}
    if len(rows) >= 5 and len(dates) == 1:
        problems.append("all lines have the same date, dates may not have been read")
    if st.get("dates_estimated"):
        problems.append(f"statement/due date couldn't be read, used {_date(st['period_end'])} / {_date(st['due_date'])} from the card settings")
    return f"⚠️ Suspicious read: {'; '.join(problems)}." if problems else None


# ---- Incoming messages -------------------------------------------------------


# Currency symbols/words → ISO code. An amount without one is in the household base currency.
CURRENCY_WORDS = {
    "₺": "TRY", "tl": "TRY", "lira": "TRY",
    "$": "USD", "dolar": "USD", "dollar": "USD", "dollars": "USD", "bucks": "USD",
    "€": "EUR", "euro": "EUR", "euros": "EUR", "avro": "EUR",
    "£": "GBP", "pound": "GBP", "pounds": "GBP", "quid": "GBP",
    "¥": "JPY", "yen": "JPY", "₹": "INR", "rupees": "INR", "zł": "PLN", "zloty": "PLN",
    "francs": "CHF", "franken": "CHF", "fr": "CHF", "kr": "SEK",
    **{c.lower(): c for c in prefs.SUPPORTED_CURRENCIES},
}
_CUR_ALT = "|".join(re.escape(w) for w in sorted(CURRENCY_WORDS, key=len, reverse=True))
_PRE_ALT = "|".join(re.escape(w) for w in ("₺", "$", "€", "£", "¥", "₹"))
# Amount anywhere in the message: "500", "500 lira", "₺500", "1.250,50 tl", "1,250.50 usd", "20 dolar", "$20", "£5", "20 dollars"
AMOUNT_RE = re.compile(
    rf"(?<![\w.,])(?P<pre>{_PRE_ALT})?\s*"
    r"(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    rf"\s*(?P<cur>{_CUR_ALT})?(?![\w])",
    re.I,
)
# Turkish and English filler words that aren't part of the description
FILLER = re.compile(
    r"\b(dün|dun|bugün|bugun|nakit|nakitle|kartla|kredi kartı|kredi karti|kart|ile|için|icin|ödedim|odedim|harcadım|harcadim"
    r"|yesterday|today|in cash|cash|by card|credit card|card|paid|spent)\b",
    re.I,
)
# Keywords (Turkish and English) → seed category name
KEYWORD_CATEGORIES = {
    "Restaurants & Cafes": "kebap kebab döner doner köfte kofte lahmacun pide pizza burger hamburger restoran restaurant lokanta "
    "cafe kafe kahve çay simit yemek tatlı pastane börek mantı dürüm çiğ starbucks kahvaltı "
    "coffee tea lunch dinner breakfast brunch food diner bakery sandwich sushi taco mcdonalds",
    "Groceries": "market migros a101 bim şok carrefour carrefoursa bakkal manav fırın ekmek kasap "
    "groceries grocery supermarket walmart costco aldi lidl kroger safeway bread butcher",
    "Transport & Fuel": "benzin mazot motorin akaryakıt yakıt petrol opet shell otopark taksi uber bitaksi otobüs metro köprü hgs ogs "
    "fuel gas gasoline diesel parking taxi lyft bus train subway toll",
    "Health": "eczane ilaç doktor hastane diş pharmacy medicine doctor hospital dentist",
    "Personal Care": "berber kuaför barber haircut salon",
    "Entertainment": "sinema konser tiyatro cinema movie movies concert theater",
    "Utilities": "fatura elektrik doğalgaz internet turkcell vodafone bill electricity water phone",
    "Subscriptions": "netflix spotify youtube subscription",
    "Clothing": "ayakkabı kıyafet tişört pantolon mont shoes clothes shirt pants jacket",
}
QUICK_CATEGORIES = ["Restaurants & Cafes", "Groceries", "Transport & Fuel", "Home & Living", "Entertainment", "Other Expense"]


def parse_quick(text: str) -> dict | None:
    """Quick expense from free text: amount (anywhere), currency, description, date and payment hints."""
    matches = [m for m in AMOUNT_RE.finditer(text)]
    if not matches:
        return None
    # An amount with a currency takes priority; otherwise the last number ("a101 2 ekmek 30" → 30)
    m = next((x for x in reversed(matches) if x.group("cur") or x.group("pre")), matches[-1])
    try:
        amount = _parse_amount(m.group("num"))
    except ValueError:
        return None
    if amount <= 0:
        return None
    cur = (m.group("cur") or m.group("pre") or "").lower()
    lower = text.lower()
    desc = (text[: m.start()] + " " + text[m.end():]).strip()
    desc = FILLER.sub(" ", desc)
    desc = re.sub(r"\s{2,}", " ", desc).strip(" ,.-")
    if not desc:
        return None
    return {
        "amount": amount,
        "currency": CURRENCY_WORDS.get(cur, prefs.base()),
        "desc": desc[:1].upper() + desc[1:],
        "date": date.today() - timedelta(days=1) if re.search(r"\b(d[üu]n|yesterday)\b", lower) else date.today(),
        "cash": bool(re.search(r"\b(nakit|cash)", lower)),
        "words": set(re.findall(r"\w+", lower)),
    }


def keyword_category(db, desc: str) -> int | None:
    words = set(re.findall(r"\w+", desc.lower()))
    for name, keys in KEYWORD_CATEGORIES.items():
        if words & set(keys.split()):
            cid = db.scalar(select(Category.id).where(Category.name == name, Category.archived.is_(False)))
            if cid:
                return cid
    return None


def _category_keyboard(db, tx_id: int) -> list[list[tuple[str, str]]]:
    cats = {c.name: c for c in db.scalars(select(Category).where(Category.kind == "expense", Category.archived.is_(False)))}
    buttons = [(f"{cats[n].icon} {n}", f"cat:{tx_id}:{cats[n].id}") for n in QUICK_CATEGORIES if n in cats]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([("↩️ Undo", f"undo:{tx_id}")])
    return rows


def _parse_amount(s: str) -> float:
    # English thousands separators: "1,250.50" / "12,000"
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", s):
        return float(s.replace(",", ""))
    return finance.parse_amount(s)


def _user_for_chat(db, chat_id: int) -> User | None:
    from .auth import is_allowed

    user = db.scalar(select(User).where(User.telegram_chat_id == chat_id))
    # A person removed from the allowed users list can't use the bot either
    return user if user and is_allowed(user.name, user.username or "") else None


def _handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    if msg["chat"].get("type") != "private":
        # Doesn't work in groups; leave if added to one
        _call("leaveChat", chat_id=chat_id)
        return
    text = (msg.get("text") or "").strip()
    db = SessionLocal()
    try:
        user = _user_for_chat(db, chat_id)

        if text.startswith(("/link", "/start link", "/bagla", "/start bagla")):
            if _link_blocked(chat_id):
                return  # too many failed attempts: don't respond for an hour
            code = re.sub(r"\D", "", text.split()[-1])  # "/link 123456" or "/start link_123456"
            entry = _link_codes.pop(code, None)
            if not entry or entry[1] < time.time():
                _failed_links.setdefault(chat_id, []).append(time.time())
                send(chat_id, "The code is invalid or has expired. Get a new code in the app under More → Telegram.")
                return
            for other in db.scalars(select(User).where(User.telegram_chat_id == chat_id)):
                other.telegram_chat_id = None
            linked = db.get(User, entry[0])
            linked.telegram_chat_id = chat_id
            db.commit()
            send(chat_id, f"✅ Linked: <b>{_esc(linked.name)}</b>\n\n{help_text()}")
            return

        if user is None:
            send(
                chat_id,
                "This bot is linked to a private budget app. To use it, send the code from "
                "<b>More → Telegram</b> in the app like this: <code>/link 123456</code>.",
            )
            return

        if msg.get("photo") or msg.get("document"):
            _receive_file(db, user, chat_id, msg)
            return
        if msg.get("message_id"):
            schedule_delete(chat_id, msg["message_id"])
        if text in ("/start", "/help", "/yardim"):
            send(chat_id, help_text())
        elif text.startswith(("/summary", "/ozet")):
            send(chat_id, _summary_text(db))
        elif text.startswith(("/recent", "/son")):
            send(chat_id, _recent_text(db))
        elif text.startswith(("/upcoming", "/yaklasan")):
            send(chat_id, _upcoming_text(db))
        elif text.startswith(("/unlink", "/ayir", "/cik")):
            user.telegram_chat_id = None
            db.commit()
            send(chat_id, "Unlinked.")
        elif fu := telegram_invest.match_fund_update(db, user, text):
            fund, value = fu
            summary, undo = telegram_invest.update_fund(db, user, fund, value)
            token = telegram_invest.remember(undo)
            send(chat_id, summary, [[("↩️ Undo", f"fundundo:{token}")]])
        elif text.startswith(("/fund", "/fon")):
            send(chat_id, telegram_invest.fund_reminder(db, user) or "You don't have any funds recorded.")
        elif inv := telegram_invest.parse_investment(text, telegram_invest.known_stock_codes(db)):
            # Buying gold/currency/stocks isn't an expense: it goes to Investments after confirmation.
            # If misread, "Save as expense" switches to the regular expense flow.
            sent_at = datetime.fromtimestamp(msg.get("date") or time.time(), settings.tz)
            summary, data = telegram_invest.describe(db, inv)
            expense = parse_quick(text)
            meta = {"text": text, "ts": msg.get("date") or time.time()}
            if not data:
                buttons = [[("💸 Save as expense", f"invexp:{telegram_invest.remember(meta)}")]] if expense else None
                send(chat_id, "Couldn't get a price for this asset. Check the code or include the price (e.g. <code>10g gold 65000tl</code>).", buttons)
            else:
                token = telegram_invest.remember({**data, **meta, "date": sent_at.date().isoformat()})
                rows = [[("✅ Save", f"inv:{token}"), ("✖️ Cancel", f"invx:{token}")]]
                if expense:
                    rows.append([("💸 Not an investment, save as expense", f"invexp:{token}")])
                send(chat_id, summary + "\nAdd to Investments?", rows)
        elif q := parse_quick(text):
            _quick_from_text(db, user, chat_id, q, msg.get("date") or time.time())
        else:
            send(chat_id, help_text())
    finally:
        db.close()


def _receive_file(db, user: User, chat_id: int, msg: dict) -> None:
    from .routers.documents import enqueue

    if msg.get("photo"):
        file_id = msg["photo"][-1]["file_id"]  # highest resolution
        mime, name = "image/jpeg", "telegram-photo.jpg"
    else:
        d = msg["document"]
        mime = d.get("mime_type") or ""
        name = d.get("file_name") or "document"
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"):
            send(chat_id, "You can only send photos (JPEG/PNG/HEIC) or PDFs.")
            return
        if d.get("file_size", 0) > 20 * 1024 * 1024:
            send(chat_id, "The file is too large (max 20 MB).")
            return
        file_id = d["file_id"]
    info = _call("getFile", file_id=file_id)
    if not info:
        send(chat_id, "Couldn't download the file, please try again.")
        return
    try:
        r = httpx.get(f"{API}/file/bot{settings.telegram_bot_token}/{info['file_path']}", timeout=60)
        r.raise_for_status()
    except Exception:
        send(chat_id, "Couldn't download the file, please try again.")
        return
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic", "image/heif": ".heif", "application/pdf": ".pdf"}[mime]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(r.content)
    from .routers.documents import MAX_QUEUE, _valid_content

    if not _valid_content(path, mime):
        path.unlink(missing_ok=True)
        send(chat_id, "Couldn't open the file or it looks corrupted.")
        return
    waiting = db.scalar(select(func.count(Document.id)).where(Document.status.in_(["pending", "processing"])))
    if waiting >= MAX_QUEUE:
        path.unlink(missing_ok=True)
        send(chat_id, "Too many documents are waiting to be read; please send it again in a bit.")
        return
    caption = (msg.get("caption") or "").lower()
    kind = (
        "statement" if "ekstre" in caption or "statement" in caption or mime == "application/pdf"
        else "receipt" if "fiş" in caption or "fis" in caption or "receipt" in caption
        else "auto"
    )
    doc = Document(
        kind=kind,
        filename=name,
        stored_path=str(path),
        mime=mime,
        uploaded_by=user.id,
        telegram_chat_id=chat_id,
        telegram_message_id=msg.get("message_id"),
    )
    if msg.get("message_id"):
        schedule_delete(chat_id, msg["message_id"])  # deleted when due even if it can't be read
    db.add(doc)
    db.commit()
    enqueue(doc.id)
    send(chat_id, "⏳ Reading… " + ("Statements can take a few minutes." if mime == "application/pdf" else "About a minute."))


def _quick_from_text(db, user: User, chat_id: int, q: dict, ts: float) -> None:
    """The moment the message was sent (local time) is recorded as the expense time."""
    sent_at = datetime.fromtimestamp(ts, settings.tz).replace(tzinfo=None)
    q["date"] = sent_at.date() if q["date"] == date.today() else q["date"]
    q["occurred_at"] = sent_at if q["date"] == sent_at.date() else None
    _quick_entry(db, user, chat_id, q)


def _quick_entry(db, user: User, chat_id: int, q: dict) -> None:
    from . import services

    category_id = finance.rule_category(db, q["desc"]) or keyword_category(db, q["desc"])
    cards = db.scalars(select(CreditCard).where(CreditCard.archived.is_(False))).all()
    # If the message mentions a card name, that card; if "cash" was written, cash; otherwise the user's only card
    named = [c for c in cards if c.name and set(re.findall(r"\w+", c.name.lower())) & q["words"]]
    own = [c for c in cards if c.owner_id == user.id]
    card = None if q["cash"] else (named[0] if len(named) == 1 else own[0] if len(own) == 1 else None)
    desc = q["desc"]
    if card:
        # Remove the card name from the description ("kebap 500 bonus" → "Kebap")
        for w in re.findall(r"\w+", card.name.lower()):
            desc = re.sub(rf"\b{re.escape(w)}\b", "", desc, flags=re.I)
        desc = re.sub(r"\s{2,}", " ", desc).strip(" ,.-") or q["desc"]
        desc = desc[:1].upper() + desc[1:]
    tx = services.create_transaction(
        db,
        kind="expense",
        amount_cents=finance.to_cents(q["amount"]),
        currency=q["currency"],
        on=q["date"],
        category_id=category_id,
        user_id=user.id,
        payment_method="card" if card else "cash",
        card_id=card.id if card else None,
        merchant=desc,
        # "Telegram" = payment method not specified; may be linked to a card when the statement arrives
        note="Telegram · cash" if q["cash"] else "Telegram",
        occurred_at=q.get("occurred_at"),
    )
    audit.record(db, user.id, "transaction", tx, "create")
    db.commit()
    services.after_transaction_saved(db, tx)
    cat = db.get(Category, category_id) if category_id else None
    when = f" · {_date(q['date'])}" if q["date"] != date.today() else ""
    if q.get("occurred_at"):
        when += f" · {q['occurred_at']:%H:%M}"
    text = (
        f"✅ {_esc(tx.merchant)}: <b>{_money(q['amount'], q['currency'])}</b>{when}\n"
        f"{(cat.icon + ' ' + cat.name) if cat else '🏷 Pick a category:'} · {('💳 ' + _esc(card.name)) if card else '💵 Cash'}"
    )
    if cat:
        send(chat_id, text, [[("🏷 Change category", f"catpick:{tx.id}"), ("↩️ Undo", f"undo:{tx.id}")]])
    else:
        send(chat_id, text, _category_keyboard(db, tx.id))


def _summary_text(db) -> str:
    from .routers.misc import planned_for_month
    from .routers.reports import summary

    ym = date.today().strftime("%Y-%m")
    s = summary(month=ym, db=db, _=None)
    cats = {c.id: c for c in db.scalars(select(Category))}
    lines = [f"📊 <b>{_month_name(ym)}</b>", f"Expenses: <b>{_money(s['expense'])}</b>"]
    if s["prev_expense"]:
        lines[-1] += f" (all of last month: {_money(s['prev_expense'])})"
    lines.append(f"Income: {_money(s['income'])} · Net: {_money(s['net'])}")
    if s["by_category"]:
        lines.append("")
        for c in s["by_category"][:5]:
            cat = cats.get(c["category_id"])
            lines.append(f"{cat.icon if cat else '•'} {_esc(cat.name if cat else 'Uncategorized')}: {_money(c['total'])}")
    planned = planned_for_month(db, ym)
    if planned:
        from .fx import Converter

        conv = Converter(db)
        total = sum(conv.to_base_cents(finance.to_cents(p["amount"]), p["currency"], date.today())
                    for p in planned if p["kind"] == "expense") / 100
        lines.append(f"\n🔁 Recurring payments until month end: {_money(total)} ({len(planned)} items)")
    debt = s["card_debt"]
    if any(debt.values()):
        lines.append(f"💳 Card balances: {_money_multi(debt)}")
    from . import spendable

    lines.append("\n" + spendable.summary_text(db))
    return "\n".join(lines)


def _recent_text(db) -> str:
    # Installments for future months aren't "recent transactions"
    rows = db.scalars(select(Transaction).where(Transaction.date <= date.today())
                      .order_by(Transaction.date.desc(), Transaction.id.desc()).limit(8)).unique().all()
    if not rows:
        return "No transactions yet."
    lines = ["🧾 <b>Recent transactions</b>"]
    for t in rows:
        sign = "+" if t.kind == "income" else "−"
        lines.append(f"{_date(t.date)} · {_esc(t.merchant or (t.category.name if t.category else 'Transaction'))}: {sign}{_money(t.amount_cents / 100, t.currency)}")
    return "\n".join(lines)


def _upcoming_text(db) -> str:
    from .routers.reports import upcoming

    items = upcoming(db, date.today(), days=31)
    if not items:
        return "No payments recorded for the next 30 days."
    lines = ["📅 <b>Upcoming payments</b>"]
    for u in items:
        lines.append(f"{_date(u['date'])} · {_esc(u['title'])}: {_money(u['amount'], u['currency'])}")
    return "\n".join(lines)


def _month_name(ym: str) -> str:
    names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    return f"{names[int(ym[5:]) - 1]} {ym[:4]}"


def _handle_callback(cb: dict) -> None:
    from . import services
    from .routers.documents import enqueue

    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    action, _, ident = (cb.get("data") or "").partition(":")
    db = SessionLocal()
    try:
        user = _user_for_chat(db, chat_id)
        if user is None:
            _call("answerCallbackQuery", callback_query_id=cb["id"], text="Unauthorized")
            return
        reply = "OK"
        if action in ("save", "newcard"):
            doc_id = int(ident)
            if not services.claim_document(db, doc_id):
                reply = "This document has already been processed."
            else:
                doc = db.get(Document, doc_id)
                db.refresh(doc)
                try:
                    card = services.card_from_statement(db, doc, user.id) if action == "newcard" else None
                    if card:
                        db.flush()
                    draft = (doc.result or {}).get("draft") or {}
                    if draft.get("doc_type") == "receipt":
                        for r in draft.get("rows", []):
                            r["include"] = True  # "Save anyway"
                    count = services.commit_draft(db, doc, draft, user.id)
                    reply = (f"Created card '{card.name}', " if card else "") + f"saved {count} transactions"
                except services.ValidationError as e:
                    services.release_document(db, doc_id)
                    reply = f"Couldn't save: {e}"
        elif action == "discard":
            doc = db.get(Document, int(ident))
            if doc and doc.status != "done":
                from pathlib import Path

                Path(doc.stored_path).unlink(missing_ok=True)
                db.delete(doc)
                db.commit()
            reply = "Deleted"
        elif action == "undo":
            tx = db.get(Transaction, int(ident))
            if tx:
                audit.record(db, user.id, "transaction", tx, "delete", audit.snapshot(tx))
                plan = tx.installment_plan
                db.delete(tx)
                if plan:
                    db.delete(plan)
                db.commit()
            reply = "Undone"
        elif action == "inv":
            data = telegram_invest.take(ident)
            if data is None:
                reply = "Expired; please send the message again."
            else:
                try:
                    reply = telegram_invest.apply(db, user, data, date.fromisoformat(data["date"]))
                except investments.LotError as e:
                    reply = f"Couldn't save: {e}"
        elif action == "fundundo":
            data = telegram_invest.take(ident)
            reply = telegram_invest.undo_fund(db, user, data) if data else "Expired"
        elif action == "invexp":
            data = telegram_invest.take(ident)
            q = parse_quick(data["text"]) if data else None
            if q is None:
                reply = "Expired; please send the message again."
            else:
                _call("answerCallbackQuery", callback_query_id=cb["id"], text="Saving as expense")
                _quick_from_text(db, user, chat_id, q, data["ts"])
                return
        elif action == "invx":
            telegram_invest.take(ident)
            reply = "Cancelled"
        elif action == "catpick":
            _call("answerCallbackQuery", callback_query_id=cb["id"])
            _call(
                "editMessageReplyMarkup",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup={"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in _category_keyboard(db, int(ident))]},
            )
            return
        elif action == "cat":
            tx_id, _, cat_id = ident.partition(":")
            tx = db.get(Transaction, int(tx_id))
            cat = db.get(Category, int(cat_id))
            if tx and cat:
                before = audit.snapshot(tx)
                tx.category_id = cat.id
                audit.record(db, user.id, "transaction", tx, "update", before)
                finance.learn_rule(db, tx.merchant, cat.id)  # next time the same merchant is automatic
                db.commit()
                _call("answerCallbackQuery", callback_query_id=cb["id"], text=f"Saved as {cat.name}")
                old = (cb["message"].get("text") or "").split("\n")[0]
                _call(
                    "editMessageText",
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"{old}\n{cat.icon} {cat.name}",
                    reply_markup={"inline_keyboard": [[{"text": "🏷 Change category", "callback_data": f"catpick:{tx.id}"}, {"text": "↩️ Undo", "callback_data": f"undo:{tx.id}"}]]},
                )
                return
            reply = "Transaction not found"
        elif action == "retry":
            doc = db.get(Document, int(ident))
            if doc:
                doc.status, doc.error = "pending", ""
                db.commit()
                enqueue(doc.id)
            reply = "Reading again…"
        _call("answerCallbackQuery", callback_query_id=cb["id"], text=reply)
        # Remove the buttons and append the result to the message
        old = cb["message"].get("text") or ""
        _call("editMessageText", chat_id=chat_id, message_id=message_id, text=f"{old}\n\n→ {reply}")
    finally:
        db.close()


# ---- Loop ---------------------------------------------------------------------


def _loop() -> None:
    global _bot_username
    me = None
    while me is None:
        me = _call("getMe")
        if me is None:
            time.sleep(30)
    _bot_username = me.get("username")
    _call(
        "setMyCommands",
        commands=[
            {"command": "summary", "description": "This month's summary"},
            {"command": "recent", "description": "Recent transactions"},
            {"command": "upcoming", "description": "Upcoming payments"},
            {"command": "fund", "description": "Update fund values"},
            {"command": "help", "description": "How to use"},
        ],
    )
    log.info("Telegram bot started: @%s", _bot_username)
    offset = None
    while True:
        params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        updates = _call("getUpdates", http_timeout=60, **params)
        if updates is None:
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                if "message" in u:
                    _handle_message(u["message"])
                elif "callback_query" in u:
                    _handle_callback(u["callback_query"])
            except Exception:
                log.exception("Failed to process Telegram update")


def _run_forever() -> None:
    while True:
        try:
            _loop()
        except Exception:
            log.exception("Telegram loop stopped, restarting in 30 s")
            time.sleep(30)


def start() -> None:
    if enabled():
        threading.Thread(target=_run_forever, daemon=True, name="telegram").start()


def send_fund_reminders(today: date | None = None) -> int:
    """On the 1st of each month, reminds fund owners via Telegram to update their values (once a month)."""
    from . import notify

    today = today or date.today()
    if not enabled():
        return 0
    db = SessionLocal()
    sent = 0
    try:
        for user in db.scalars(select(User).where(User.telegram_chat_id.is_not(None))):
            r = telegram_invest.fund_reminder(db, user)
            if r is None:
                continue
            from .models import SentNotice
            from sqlalchemy.exc import IntegrityError

            db.add(SentNotice(key=f"fund-reminder:{user.id}:{today:%Y-%m}"))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            send(user.telegram_chat_id, r)
            sent += 1
    finally:
        db.close()
    return sent


def send_once_to(db, chat_id: int, key: str, text: str) -> bool:
    """Sends to a single person, once per key."""
    from sqlalchemy.exc import IntegrityError

    from .models import SentNotice

    db.add(SentNotice(key=key))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    send(chat_id, text)
    return True
