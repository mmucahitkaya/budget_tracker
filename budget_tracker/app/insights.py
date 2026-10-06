"""Savings analysis: biggest expense items, subscriptions, frequent small purchases and tips.

Period: last N days (by spending date). Monthly values are scaled to 30 days based on the number of days with data.
Fixed items (rent, car loan…) and installment purchases are kept separate: savings are mostly sought in variable
spending. Tips are rule-based estimates, not exact figures.
"""
import re
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import finance, prefs
from .fx import Converter
from .models import Category, InstallmentPlan, Transaction

OTHER = "Other Expense"
# Digital merchants that can count as subscriptions
SUBSCRIPTION_HINT = re.compile(
    r"apple\.com|apple com|itunes|google|youtube|netflix|spotify|disney|amazon prime|prime video|exxen|blutv|"
    r"gain|tod |digiturk|icloud|microsoft|xbox|playstation|steam|chatgpt|openai|adobe|canva|duolingo|storytel|"
    r"hulu|hbo|paramount|peacock|audible|dropbox|"
    r"turkcell|vodafone|turk telekom|türk telekom|superonline|avea|fatura|"
    r"verizon|at&t|t-mobile|comcast|xfinity|telekom|o2 |sky ",
    re.I,
)
DELIVERY_HINT = re.compile(
    r"yemeksepeti|getir|trendyol ?go|trendyolgo|migros ?hemen|banabi|uber ?eats|doordash|grubhub|instacart|deliveroo|"
    r"just ?eat|lieferando|wolt|glovo|foodora|takeaway\.com|thuisbezorgd|gorillas|flink", re.I
)
EATING_OUT = {"Restaurants & Cafes"}

# Tip thresholds are defined in USD and scaled by a rough, static "units per USD" magnitude of the base currency
# (they only decide whether a tip is worth showing; precise FX is not needed).
APPROX_PER_USD = {
    "AUD": 1.5, "BGN": 1.8, "BRL": 5.5, "CAD": 1.4, "CHF": 0.9, "CNY": 7.2, "CZK": 23, "DKK": 7, "EUR": 0.92,
    "GBP": 0.79, "HKD": 7.8, "HUF": 360, "IDR": 16000, "ILS": 3.7, "INR": 84, "ISK": 138, "JPY": 150, "KRW": 1350,
    "MXN": 18, "MYR": 4.5, "NOK": 10.7, "NZD": 1.65, "PHP": 57, "PLN": 4, "RON": 4.6, "SEK": 10.5, "SGD": 1.35,
    "THB": 35, "TRY": 40, "USD": 1, "ZAR": 18,
}


def _threshold(usd: float) -> float:
    """A USD-denominated threshold expressed in the household base currency (whole units)."""
    return usd * APPROX_PER_USD.get(prefs.base(), 1)


def _monthly(cents: int, days: int) -> int:
    return round(cents * 30.4 / max(days, 1))


def analyze(db: Session, days: int = 90, today: date | None = None) -> dict:
    today = today or date.today()
    conv = Converter(db)
    since = today - timedelta(days=days - 1)
    cats = {c.id: c for c in db.scalars(select(Category))}
    other_id = next((c.id for c in cats.values() if c.name == OTHER and c.kind == "expense"), None)
    txs = [t for t in db.scalars(select(Transaction).where(
        Transaction.kind == "expense", Transaction.date >= since, Transaction.date <= today,
        Transaction.payment_method != "voucher"))]
    if not txs:
        return {"days": days, "empty": True}
    first = min(t.date for t in txs)
    span = (today - first).days + 1  # actual number of days if data starts later than the period
    cents = {t.id: conv.to_base_cents(t.amount_cents, t.currency, t.date) for t in txs}

    total = sum(cents.values())
    fixed = sum(cents[t.id] for t in txs if t.recurring_id)
    installment = sum(cents[t.id] for t in txs if t.installment_plan_id)
    variable_txs = [t for t in txs if not t.recurring_id and not t.installment_plan_id]
    variable = sum(cents[t.id] for t in variable_txs)

    # Categories (all expenses)
    by_cat: dict[int | None, int] = defaultdict(int)
    count_cat: dict[int | None, int] = defaultdict(int)
    ids_cat: dict[int | None, list[int]] = defaultdict(list)
    for t in txs:
        key = t.category_id if t.category_id != other_id else None
        by_cat[key] += cents[t.id]
        count_cat[key] += 1
        ids_cat[key].append(t.id)
    categories = [
        {"category_id": cid, "name": cats[cid].name if cid in cats else "Other / uncategorized",
         "icon": cats[cid].icon if cid in cats else "❔", "total": v / 100, "monthly": _monthly(v, span) / 100,
         "share": round(v / total, 4) if total else 0, "count": count_cat[cid], "tx_ids": ids_cat[cid]}
        for cid, v in sorted(by_cat.items(), key=lambda x: -x[1])
    ]
    uncategorized = by_cat.get(None, 0)

    # Merchants (variable spending)
    merchants: dict[str, dict] = {}
    for t in variable_txs:
        # First two words: banks may spell the same merchant differently ("APPLE.COM/BILL" / "APPLE.COM/BILLAPPLE…")
        key = " ".join(finance.normalize_merchant(t.merchant).split()[:2]) or (t.merchant or "—").lower()
        m = merchants.setdefault(key, {"name": t.merchant or "—", "category_id": t.category_id, "total": 0, "count": 0,
                                       "amounts": [], "months": set(), "ids": []})
        m["total"] += cents[t.id]
        m["count"] += 1
        m["amounts"].append(cents[t.id])
        m["months"].add(t.date.strftime("%Y-%m"))
        m["ids"].append(t.id)
    top_merchants = [
        {"name": m["name"], "category_id": m["category_id"], "total": m["total"] / 100,
         "monthly": _monthly(m["total"], span) / 100, "count": m["count"],
         "avg": round(m["total"] / m["count"]) / 100, "tx_ids": m["ids"]}
        for m in sorted(merchants.values(), key=lambda m: -m["total"])[:12]
    ]

    # Subscriptions: digital merchants or similar amounts repeating every month
    subs = []
    for m in merchants.values():
        amounts = m["amounts"]
        # Once or twice a month, similar amount (not frequent shopping)
        similar = (len(amounts) >= 2 and max(amounts) <= min(amounts) * 1.3 and len(m["months"]) >= 2
                   and len(amounts) <= 2 * len(m["months"]))
        food = cats.get(m["category_id"]) and cats[m["category_id"]].name in ("Groceries", "Restaurants & Cafes")
        if SUBSCRIPTION_HINT.search(m["name"]) or (similar and not food):
            subs.append({"name": m["name"], "count": m["count"], "total": m["total"] / 100,
                         "monthly": _monthly(m["total"], span) / 100, "avg": round(m["total"] / m["count"]) / 100,
                         "tx_ids": m["ids"]})
    subs.sort(key=lambda s: -s["monthly"])
    subs_monthly = sum(s["monthly"] for s in subs)

    # Frequent small purchases: 6+ times a month, small average basket
    frequent = []
    for m in merchants.values():
        per_month = m["count"] * 30.4 / span
        avg = m["total"] / m["count"]
        if per_month >= 6 and avg < _threshold(37.5) * 100 and not SUBSCRIPTION_HINT.search(m["name"]):
            frequent.append({"name": m["name"], "per_month": round(per_month, 1), "avg": round(avg) / 100,
                             "monthly": _monthly(m["total"], span) / 100, "tx_ids": m["ids"]})
    frequent.sort(key=lambda f: -f["monthly"])

    cat_monthly = {c["name"]: c["monthly"] for c in categories}
    delivery = _monthly(sum(cents[t.id] for t in variable_txs if DELIVERY_HINT.search(t.merchant or "")), span) / 100
    fees = cat_monthly.get("Bank & Card Fees", 0)
    eating = sum(cat_monthly.get(n, 0) for n in EATING_OUT)

    # New installment load: monthly total of plans opened in the period
    new_inst = sum(conv.to_base_cents(p.monthly_cents, p.currency, today)
                   for p in db.scalars(select(InstallmentPlan).where(InstallmentPlan.first_month >= finance.month_start(since))))

    tips = []
    cur = prefs.base()
    cat_ids = {c["name"]: c["tx_ids"] for c in categories}
    new_plan_ids = {p.id for p in db.scalars(select(InstallmentPlan).where(InstallmentPlan.first_month >= finance.month_start(since)))}

    def tip(kind: str, title: str, text: str, saving: float = 0, tx_ids: list[int] | None = None):
        # tx_ids: transactions to review item by item when the tip is tapped
        tips.append({"kind": kind, "title": title, "text": text, "saving": round(saving), "tx_ids": tx_ids or []})

    var_monthly = _monthly(variable, span) / 100
    if total and uncategorized / total > 0.1:
        tip("categorize", "Categorize first",
            f"{round(uncategorized / total * 100)}% of spending is in 'Other'; analysis and tips are incomplete.")
    if subs_monthly >= _threshold(5):
        tip("subscriptions", "Review your subscriptions",
            f"{len(subs)} recurring digital/subscription payments cost ~{subs_monthly:,.0f} {cur} a month. Canceling the ones you don't use "
            "and switching to family plans usually saves half.", subs_monthly * 0.4,
            [i for x in subs for i in x["tx_ids"]])
    if eating and var_monthly and eating / var_monthly > 0.12:
        tip("eating", "Eating out",
            f"Restaurants & cafes cost ~{eating:,.0f} {cur} a month ({round(eating / var_monthly * 100)}% of variable spending). "
            "Cooking one more meal a week at home cuts this by ~25%.", eating * 0.25,
            [i for n in EATING_OUT for i in cat_ids.get(n, [])])
    if delivery >= _threshold(12.5):
        tip("delivery", "Food/grocery delivery",
            f"~{delivery:,.0f} {cur} a month on delivery apps. Delivery fees and small baskets add up; "
            "halving the number of orders saves ~30%.", delivery * 0.3,
            [t.id for t in variable_txs if DELIVERY_HINT.search(t.merchant or "")])
    if frequent:
        f = frequent[0]
        tip("frequent", "Frequent small purchases",
            f"{f['name']} ~{f['per_month']:g} times a month, average basket {f['avg']:,.0f} {cur}. Shopping in bulk with a weekly list "
            "cuts unplanned purchases by ~10%.", sum(x["monthly"] for x in frequent) * 0.1,
            [i for x in frequent for i in x["tx_ids"]])
    if fees >= _threshold(2.5):
        tip("fees", "Bank and card fees",
            f"~{fees:,.0f} {cur} a month in fees/interest. Paying statements in full, asking for an annual fee refund or switching to a no-fee card "
            "can eliminate this.", fees * 0.8, cat_ids.get("Bank & Card Fees", []))
    if new_inst >= _threshold(50) * 100:
        tip("installments", "New installments",
            f"Recently opened installments add ~{new_inst / 100:,.0f} {cur} a month to upcoming months. "
            "Avoiding new installments until the tight months pass eases the plan.", 0,
            [t.id for t in txs if t.installment_plan_id in new_plan_ids])
    # Top 3 variable categories: 10% reduction
    big = [c for c in categories if c["category_id"] is not None and c["name"] not in ("Rent & Housing", "Loans & Debt")][:3]
    if big:
        s = sum(c["monthly"] for c in big) * 0.1
        tip("top", "Biggest items",
            "Top spending: " + ", ".join(f"{c['icon']} {c['name']} (~{c['monthly']:,.0f} {cur}/mo)" for c in big)
            + f". Cutting each by 10% saves ~{s:,.0f} {cur} a month (may overlap with other tips, not added to the total).",
            0, [i for c in big for i in c["tx_ids"]])

    return {
        "days": days,
        "span": span,
        "since": first.isoformat(),
        "total": total / 100,
        "monthly_total": _monthly(total, span) / 100,
        "monthly_variable": var_monthly,
        "monthly_fixed": _monthly(fixed, span) / 100,
        "monthly_installment": _monthly(installment, span) / 100,
        "uncategorized_share": round(uncategorized / total, 4) if total else 0,
        "categories": categories,
        "merchants": top_merchants,
        "subscriptions": subs[:10],
        "subscriptions_monthly": round(subs_monthly, 2),
        "frequent": frequent[:6],
        "tips": tips,
        "saving_total": round(sum(t["saving"] for t in tips)),
        "fx_missing": conv.missing_list(),
    }
