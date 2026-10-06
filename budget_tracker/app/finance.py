"""Money, installment, budget and recurring-entry calculations."""
import calendar
import re
import unicodedata
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import notify, prefs
from .fx import Converter
from .models import Budget, Category, CreditCard, InstallmentPlan, MerchantRule, Transaction


def parse_amount(text: str) -> float:
    """Turkish-style amount: "1.250" -> 1250 (exactly 3 digits after a dot means thousands), "1,25" -> 1.25,
    "1.250,50" -> 1250.5, "12.5" -> 12.5. The web UI (format.ts parseAmount) applies the same rule."""
    t = re.sub(r"[\s₺$€£]", "", text or "")
    if not t:
        raise ValueError("empty amount")
    if "," in t:
        return float(t.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        return float(t.replace(".", ""))
    return float(t)


def format_money(cents: int | None, currency: str | None = None, decimals: int = 2) -> str:
    """Human-readable amount for messages: 123456 cents -> "1,234.56 USD" (default: base currency)."""
    return f"{(cents or 0) / 100:,.{decimals}f} {currency or prefs.base()}"


def format_amounts(amounts: dict[str, int] | None) -> str:
    """{currency: cents} -> "1,234.56 USD + 10.00 EUR" (base first, zero amounts skipped)."""
    base = prefs.base()
    items = sorted(((c, v) for c, v in (amounts or {}).items() if v), key=lambda x: (x[0] != base, x[0]))
    return " + ".join(format_money(v, c) for c, v in items) or format_money(0)


def to_cents(x: float | int | None) -> int:
    return int(round((x or 0) * 100))


def from_cents(c: int | None) -> float:
    return round((c or 0) / 100, 2)


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def month_range(ym: str) -> tuple[date, date]:
    """'2026-10' -> (2026-10-01, 2026-10-31)"""
    y, m = map(int, ym.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def clamp_day(y: int, m: int, day: int) -> date:
    if day == LAST_BUSINESS_DAY:
        return last_business_day(y, m)
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


LAST_BUSINESS_DAY = 0  # for recurring payments, instead of a "day": the last business day of the month (salary)


def last_business_day(y: int, m: int) -> date:
    """Last weekday of the month (public holidays are not considered)."""
    d = date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ---- Credit card calendar --------------------------------------------------


def statement_month_for(card: CreditCard, purchase: date) -> date:
    """Month of the statement the purchase will appear on (1st of the month)."""
    cut = clamp_day(purchase.year, purchase.month, card.statement_day)
    first = month_start(purchase)
    return first if purchase <= cut else add_months(first, 1)


def business_day(d: date) -> date:
    """A due date that falls on a weekend moves to the next business day at banks."""
    if d.weekday() == 5:
        return d + timedelta(days=2)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def next_due_date(card: CreditCard, today: date) -> date:
    """The card's first due date from today on (computed from the day in the card settings)."""
    dues = []
    for i in range(-1, 3):
        m = add_months(month_start(today), i)
        due_month = m if card.due_day > card.statement_day else add_months(m, 1)
        dues.append(business_day(clamp_day(due_month.year, due_month.month, card.due_day)))
    return min(d for d in dues if d >= today)


def next_statement_date(card: CreditCard, today: date) -> date:
    cut = clamp_day(today.year, today.month, card.statement_day)
    if cut < today:
        n = add_months(month_start(today), 1)
        cut = clamp_day(n.year, n.month, card.statement_day)
    return cut


def day_distance(a: int, b: int) -> int:
    """Circular distance between days of the month (e.g. 30 and 1 -> 1-2 days)."""
    d = abs(a - b) % 31
    return min(d, 31 - d)


def last_statement_cut(card: CreditCard, today: date) -> date:
    cut = clamp_day(today.year, today.month, card.statement_day)
    if cut >= today:
        prev = add_months(month_start(today), -1)
        cut = clamp_day(prev.year, prev.month, card.statement_day)
    return cut


def installment_schedule(plans: list[InstallmentPlan], start: date, months: int) -> list[dict]:
    """Installment load per currency for the coming months."""
    out = []
    for i in range(months):
        m = add_months(month_start(start), i)
        totals: dict[str, int] = {}
        for p in plans:
            idx = (m.year - p.first_month.year) * 12 + (m.month - p.first_month.month)
            if 0 <= idx < p.count:
                totals[p.currency] = totals.get(p.currency, 0) + p.monthly_cents
        out.append({"month": m.strftime("%Y-%m"), "totals": {k: from_cents(v) for k, v in totals.items()}})
    return out


def remaining_installments(plan: InstallmentPlan, today: date) -> int:
    idx = (today.year - plan.first_month.year) * 12 + (today.month - plan.first_month.month)
    return max(0, min(plan.count, plan.count - idx))


# ---- Merchant -> category rules --------------------------------------------


def normalize_merchant(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).replace("ı", "i")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    # Drop digit groups such as branch/terminal numbers
    s = re.sub(r"\b\d+\b", " ", s)
    words = [w for w in s.split() if len(w) > 1][:3]
    return " ".join(words)


# Marketplaces and payment intermediaries: each order is categorized separately, no merchant rule is learned
MARKETPLACE = re.compile(
    r"hepsiburada|hepsipay|amazon|trendyol|n11|ciceksepeti|çiçeksepeti|iyzico|paycell|paytr|moneypay|dgpays|param\b|"
    r"shopier|aliexpress|temu|pazarama|gittigidiyor|boyner|morhipo|getir|yemeksepeti|garanti pay|papara|sipay",
    re.I,
)


def _other_category_id(db: Session) -> int | None:
    return db.scalar(select(Category.id).where(Category.name == "Other Expense", Category.kind == "expense"))


def rule_category(db: Session, merchant: str) -> int | None:
    key = normalize_merchant(merchant)
    if not key or MARKETPLACE.search(merchant or ""):
        return None  # on a marketplace each order may be in a different category
    cid = db.scalar(select(MerchantRule.category_id).where(MerchantRule.pattern == key))
    # "Other" is not a rule: it must not block the model's/user's next guess
    return None if cid is not None and cid == _other_category_id(db) else cid


def learn_rule(db: Session, merchant: str, category_id: int | None) -> None:
    key = normalize_merchant(merchant)
    if not key or not category_id or category_id == _other_category_id(db) or MARKETPLACE.search(merchant or ""):
        return
    rule = db.scalar(select(MerchantRule).where(MerchantRule.pattern == key))
    if rule:
        rule.category_id = category_id
    else:
        db.add(MerchantRule(pattern=key, category_id=category_id))


def category_by_name(db: Session, name: str | None, kind: str = "expense") -> int | None:
    if not name:
        return None
    cats = db.scalars(select(Category).where(Category.kind == kind, Category.archived.is_(False))).all()
    lname = name.strip().lower()
    for c in cats:
        if c.name.lower() == lname:
            return c.id
    for c in cats:
        if lname in c.name.lower() or c.name.lower() in lname:
            return c.id
    fallback = "other expense" if kind == "expense" else "other income"
    return next((c.id for c in cats if c.name.lower() == fallback), None)


# ---- Budget ---------------------------------------------------------------


def category_spend_base(db: Session, category_id: int, start: date, end: date, conv: Converter | None = None) -> int:
    """Category's expenses in the period, in base-currency cents."""
    conv = conv or Converter(db)
    rows = db.execute(
        select(Transaction.amount_cents, Transaction.currency, Transaction.date).where(
            Transaction.kind == "expense",
            Transaction.category_id == category_id,
            Transaction.date >= start,
            Transaction.date <= end,
        )
    ).all()
    return sum(conv.to_base_cents(a, c, d) for a, c, d in rows)


def check_budget(db: Session, category_id: int | None, on: date) -> None:
    if not category_id:
        return
    ym = on.strftime("%Y-%m")
    budget = db.scalar(select(Budget).where(Budget.category_id == category_id, Budget.month == ym))
    if not budget or budget.limit_cents <= 0:
        return
    start, end = month_range(ym)
    spent = category_spend_base(db, category_id, start, end)
    ratio = spent / budget.limit_cents
    cat = db.get(Category, category_id)
    for level in (100, 80):
        if ratio * 100 >= level:
            notify.send_once(
                db,
                f"budget:{budget.id}:{ym}:{level}",
                "Budget exceeded" if level == 100 else "Budget warning",
                f"{cat.name if cat else 'Category'}: {format_money(spent)} / {format_money(budget.limit_cents)} ({ratio * 100:.0f}%)",
            )
            break


# ---- Recurring entry detection --------------------------------------------


def merchant_overlap(a: str, b: str) -> float:
    """Word overlap of two merchant names (relative to the shorter one's words, 0..1).

    "Mario pizza" <-> "MARIO PIZZA DOWNTOWN" = 1.0; "Walmart" <-> "Shell" = 0.
    """
    wa, wb = set(normalize_merchant(a).split()), set(normalize_merchant(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def match_candidates(
    db: Session,
    kind: str,
    amount_cents: int,
    currency: str,
    on: date,
    merchant: str,
    card_id: int | None = None,
    days: int = 3,
    exclude: set[int] | None = None,
) -> list[dict]:
    """Existing records that may be the same spending, sorted by score.

    Same kind, amount and currency within ±days days are required. A "strong" match is excluded automatically;
    weak ones are only shown as suggestions (e.g. a different merchant with the same amount).
    """
    q = select(Transaction).where(
        Transaction.kind == kind,
        Transaction.amount_cents == amount_cents,
        Transaction.currency == currency,
        Transaction.date >= on - timedelta(days=days),
        Transaction.date <= on + timedelta(days=days),
    )
    if card_id:
        q = q.where((Transaction.card_id == card_id) | (Transaction.card_id.is_(None)))
    out = []
    for t in db.scalars(q).unique():
        if exclude and t.id in exclude:
            continue
        name_score = merchant_overlap(t.merchant or "", merchant or "")
        day_gap = abs((t.date - on).days)
        unspecified = t.card_id is None and t.note == "Telegram"  # quick entry with no payment method given
        same_card_same_day = bool(card_id) and t.card_id == card_id and day_gap == 0
        score = 0.6 * name_score + 0.25 * (1 - day_gap / (days + 1)) + (0.15 if unspecified or same_card_same_day else 0)
        strong = name_score >= 0.5 or same_card_same_day or (unspecified and day_gap <= 1 and name_score > 0)
        out.append(
            {
                "tx_id": t.id,
                "merchant": t.merchant,
                "date": t.date.isoformat(),
                "amount": from_cents(t.amount_cents),
                "currency": t.currency,
                "score": round(score, 3),
                "strong": strong,
            }
        )
    out.sort(key=lambda c: (-c["strong"], -c["score"]))
    return out


def recurring_amount(r, on: date) -> int:
    """Amount of the recurring payment on that date (cents).

    Priority: that year's month ("2026-12") -> that month every year ("09") -> standard amount. 0 = no payment that month.
    """
    plan = r.amount_plan or {}
    planned = plan.get(f"{on:%Y-%m}", plan.get(f"{on:%m}"))
    if planned is not None:
        return int(planned)
    if r.asset_code and r.asset_qty:
        price = asset_unit_price(r, on)
        if price:
            return round(r.asset_qty * price)
    return r.amount_cents


def asset_unit_price(r, on: date) -> int | None:
    """Unit price (cents) for a recurring item paid in gold: last price up to that day, otherwise the latest price."""
    from sqlalchemy.orm import object_session

    from . import prices

    db = object_session(r)
    if db is None:
        return None
    kind, code = prices.split_asset_code(r.asset_code)
    p = prices.price_on(db, kind, code, on) or prices.latest(db, kind, code)
    return p.price_cents if p else None


def advance_recurring(next_date: date, frequency: str, anchor_day: int) -> date:
    """Next date; the original day (anchor_day) is kept to prevent end-of-month drift."""
    if frequency == "weekly":
        return next_date + timedelta(days=7)
    n = add_months(next_date.replace(day=1), 12 if frequency == "yearly" else 1)
    return clamp_day(n.year, n.month, anchor_day)


def count_transactions(db: Session) -> int:
    return db.scalar(select(func.count(Transaction.id))) or 0


def recurring_dates(next_date: date, frequency: str, anchor_day: int, end_date: date | None, until: date) -> list[date]:
    """All payment dates starting at next_date up to and including until (and end_date, if any)."""
    out = []
    d = next_date
    limit = min(until, end_date) if end_date else until
    while d <= limit and len(out) < 600:
        out.append(d)
        d = advance_recurring(d, frequency, anchor_day)
    return out


def copy_budgets(db: Session, from_month: str, to_month: str, only_if_empty: bool = True) -> int:
    """Copies one month's budget limits to another month. Leaves categories already set in the target month alone."""
    existing = {b.category_id for b in db.scalars(select(Budget).where(Budget.month == to_month))}
    if only_if_empty and existing:
        return 0
    n = 0
    for b in db.scalars(select(Budget).where(Budget.month == from_month)).all():
        if b.category_id not in existing:
            db.add(Budget(category_id=b.category_id, month=to_month, limit_cents=b.limit_cents))
            n += 1
    return n
