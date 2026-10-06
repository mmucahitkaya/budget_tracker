"""Create a demo household (Alice & Bob, USD) for trying the app locally and for screenshots.

Usage:
    BUDGET_DATA_DIR=/tmp/budget-demo .venv/bin/python scripts/demo_data.py
    cd budget_tracker && BUDGET_DATA_DIR=/tmp/budget-demo BUDGET_DEV_USER=alice:Alice \
        ../.venv/bin/uvicorn app.main:app --port 8099
The data directory is wiped first. No network access is needed (exchange rates and prices are stored as fixed values).
"""
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path(os.environ.setdefault("BUDGET_DATA_DIR", "/tmp/budget-demo"))
shutil.rmtree(DATA, ignore_errors=True)
DATA.mkdir(parents=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "budget_tracker"))

from app import finance, prefs, services  # noqa: E402
from app.db import Base, SessionLocal, engine, migrate  # noqa: E402
from app.models import (  # noqa: E402
    Asset, AssetLot, AssetPrice, Budget, CardStatement, Category, CreditCard, FxRate, RecurringPayment, SavingsGoal, User,
)
from app.seed import seed  # noqa: E402

Base.metadata.create_all(engine)
migrate()
db = SessionLocal()
seed(db)
prefs.save(db, base_currency="USD", currencies=["USD", "EUR", "GBP"], locale="en-US", region="", setup_done=True)

today = date.today()
m0 = today.replace(day=1)
cat = {c.name: c.id for c in db.query(Category)}
alice = User(ha_user_id="alice", name="Alice", username="alice")
bob = User(ha_user_id="bob", name="Bob", username="bob")
db.add_all([alice, bob])
db.flush()

for i in range(120):  # EUR rates for the trip
    db.merge(FxRate(date=today - timedelta(days=i), currency="EUR", rate=1.08))
    db.merge(FxRate(date=today - timedelta(days=i), currency="GBP", rate=1.27))

sapphire = CreditCard(name="Sapphire", bank="Chase", last4="4417", statement_day=24, due_day=18, owner_id=alice.id,
                      color="#0A84FF", limit_cents=1500000)
gold = CreditCard(name="Gold Card", bank="Amex", last4="1009", statement_day=8, due_day=3, owner_id=bob.id,
                  color="#FF9500", limit_cents=1200000)
db.add_all([sapphire, gold])
db.flush()


def spend(days_ago, merchant, amount, category, card=None, user=None, method=None, currency="USD", **kw):
    on = today - timedelta(days=days_ago)
    services.create_transaction(
        db, kind="expense", amount_cents=round(amount * 100), currency=currency, on=on, category_id=cat.get(category),
        user_id=(user or alice).id, payment_method=method or ("card" if card else "bank"),
        card_id=card.id if card else None, merchant=merchant, **kw)


# ~10 weeks of everyday spending
weekly = [
    ("Whole Foods Market", 142.35, "Groceries", sapphire, alice), ("Trader Joe's", 68.20, "Groceries", gold, bob),
    ("Shell", 54.10, "Transport & Fuel", sapphire, alice), ("Blue Bottle Coffee", 11.40, "Restaurants & Cafes", gold, bob),
    ("Chipotle", 27.85, "Restaurants & Cafes", sapphire, alice), ("Target", 46.99, "Home & Living", gold, bob),
]
for w in range(10):
    for j, (m, a, c, card, u) in enumerate(weekly):
        spend(w * 7 + j, m, round(a * (0.85 + 0.05 * ((w + j) % 6)), 2), c, card, u)
for d, m, a, c, card, u in [
    (3, "Netflix", 15.49, "Subscriptions", sapphire, alice), (34, "Netflix", 15.49, "Subscriptions", sapphire, alice),
    (5, "Spotify", 11.99, "Subscriptions", gold, bob), (36, "Spotify", 11.99, "Subscriptions", gold, bob),
    (9, "Apple iCloud", 2.99, "Subscriptions", sapphire, alice), (40, "Apple iCloud", 2.99, "Subscriptions", sapphire, alice),
    (12, "DoorDash", 38.40, "Restaurants & Cafes", gold, bob), (19, "DoorDash", 44.15, "Restaurants & Cafes", gold, bob),
    (6, "The French Laundry", 286.00, "Restaurants & Cafes", sapphire, alice),
    (14, "REI", 129.95, "Hobbies", sapphire, alice), (22, "Guitar Center", 249.00, "Hobbies", gold, bob),
    (16, "CVS Pharmacy", 23.75, "Health", gold, bob), (27, "Uniqlo", 89.70, "Clothing", sapphire, alice),
    (31, "Delta Air Lines", 612.40, "Travel", sapphire, alice), (8, "Bank fee", 15.00, "Bank & Card Fees", gold, bob),
]:
    spend(d, m, a, c, card, u)
# Trip to Paris in EUR
spend(24, "Le Comptoir du Relais", 96.00, "Restaurants & Cafes", sapphire, alice, currency="EUR")
spend(23, "Hôtel Le Marais", 420.00, "Travel", sapphire, alice, currency="EUR")
# Uncategorized marketplace orders (for the Categorize screen)
for d, a, note in [(4, 64.99, ""), (11, 23.48, ""), (18, 132.10, "")]:
    spend(d, "AMAZON MKTPL*2K4", a, None, gold, bob)
spend(13, "SQ *SUNSET FARMERS MKT", 31.00, None, sapphire, alice)
spend(29, "PAYPAL *ETSY", 47.25, None, sapphire, alice)
# Installment purchase (Apple Card style)
spend(50, "Apple Store", 1199.00, "Electronics", sapphire, alice, installment_count=12)

# Recurring income & bills
last_bd = finance.last_business_day(today.year, today.month)
for r in [
    RecurringPayment(name="Alice Salary", kind="income", amount_cents=620000, next_date=last_bd, day=finance.LAST_BUSINESS_DAY,
                     category_id=cat["Salary"], user_id=alice.id, raise_months="1"),
    RecurringPayment(name="Bob Salary", kind="income", amount_cents=480000, next_date=m0.replace(day=15) if today.day <= 15 else finance.add_months(m0, 1).replace(day=15),
                     day=15, category_id=cat["Salary"], user_id=bob.id, raise_months="1,7"),
    RecurringPayment(name="Rent", kind="expense", amount_cents=245000, next_date=finance.add_months(m0, 1), day=1,
                     category_id=cat["Rent & Housing"], raise_months="9"),
    RecurringPayment(name="Car loan", kind="expense", amount_cents=41200, next_date=m0.replace(day=20) if today.day < 20 else finance.add_months(m0, 1).replace(day=20),
                     day=20, end_date=finance.add_months(m0, 22).replace(day=20), category_id=cat["Loans & Debt"]),
    RecurringPayment(name="Electricity", kind="expense", amount_cents=11800, next_date=m0.replace(day=12) if today.day < 12 else finance.add_months(m0, 1).replace(day=12),
                     day=12, category_id=cat["Utilities"], amount_plan={f"{finance.add_months(m0, k):%Y-%m}": v for k, v in [(2, 15600), (3, 17400), (4, 16200)]}),
    RecurringPayment(name="Internet", kind="expense", amount_cents=6500, next_date=m0.replace(day=8) if today.day < 8 else finance.add_months(m0, 1).replace(day=8),
                     day=8, category_id=cat["Utilities"], payment_method="card", card_id=gold.id),
    RecurringPayment(name="Car insurance (annual)", kind="expense", amount_cents=128000, frequency="yearly",
                     next_date=finance.add_months(m0, 3).replace(day=5), day=5, category_id=cat["Taxes & Fees"]),
]:
    db.add(r)

# Last statements
prev = finance.add_months(m0, -1)
db.add(CardStatement(card_id=sapphire.id, period_end=prev.replace(day=24), due_date=m0.replace(day=18), currency="USD",
                     totals={"USD": 284730}, min_payment_cents=4000))
db.add(CardStatement(card_id=gold.id, period_end=m0.replace(day=8), due_date=finance.add_months(m0, 1).replace(day=3), currency="USD",
                     totals={"USD": 118440}, min_payment_cents=3500))
sapphire.debts, gold.debts = {"USD": 284730}, {"USD": 118440}
sapphire.debt_updated_at = gold.debt_updated_at = datetime.now()

# Budgets for this month
for name, limit in [("Groceries", 900), ("Restaurants & Cafes", 450), ("Transport & Fuel", 300), ("Hobbies", 250), ("Subscriptions", 60)]:
    db.add(Budget(category_id=cat[name], month=f"{m0:%Y-%m}", limit_cents=limit * 100))

# Goals and investments
house = SavingsGoal(name="House down payment", target_cents=6000000, target_date=date(today.year + 2, 6, 1))
trip = SavingsGoal(name="Japan trip", target_cents=800000, target_date=finance.add_months(m0, 9))
db.add_all([house, trip])
db.flush()
for code, name, kind, qty, buy, price in [
    ("VTI", "Vanguard Total Stock Market", "stock", 42, 21500, 29200),
    ("AAPL", "Apple", "stock", 15, 17800, 22950),
    ("XAU_OZ", "Gold (troy ounce)", "gold", 2, 205000, 265000),
]:
    a = Asset(kind=kind, code=code, name=name, goal_id=house.id if code == "VTI" else None, owner_id=None)
    db.add(a)
    db.flush()
    db.add(AssetLot(asset_id=a.id, side="buy", date=today - timedelta(days=400), quantity=qty, unit_price_cents=buy))
    db.merge(AssetPrice(key=f"{kind}:{code}", date=today, price_cents=price, source="demo", fetched_at=datetime.now()))
fund = Asset(kind="fund", code="EMRG", name="Emergency fund (HYSA)", manual_value_cents=1850000, manual_value_date=today,
             manual_cost_cents=1800000)
db.add(fund)
db.commit()

# Materialize recurring payments that already happened this month
for r in db.query(RecurringPayment):
    services.materialize_recurring(db, r, today)
db.commit()
print(f"Demo data written to {DATA}")
