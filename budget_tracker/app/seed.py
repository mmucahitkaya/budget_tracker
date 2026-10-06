import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Category

DEFAULT_CATEGORIES = [
    # (name, kind, icon, color)
    ("Groceries", "expense", "🛒", "#34C759"),
    ("Restaurants & Cafes", "expense", "🍽️", "#FF9500"),
    ("Transport & Fuel", "expense", "⛽", "#5AC8FA"),
    ("Utilities", "expense", "💡", "#FFCC00"),
    ("Rent & Housing", "expense", "🏠", "#AF52DE"),
    ("Health", "expense", "💊", "#FF3B30"),
    ("Education", "expense", "📚", "#5856D6"),
    ("Clothing", "expense", "👕", "#FF2D55"),
    ("Home & Living", "expense", "🛋️", "#A2845E"),
    ("Electronics", "expense", "📱", "#64D2FF"),
    ("Entertainment", "expense", "🎬", "#BF5AF2"),
    ("Subscriptions", "expense", "🔁", "#30B0C7"),
    ("Travel", "expense", "✈️", "#0A84FF"),
    ("Personal Care", "expense", "💇", "#FF6482"),
    ("Gifts & Donations", "expense", "🎁", "#FFD60A"),
    ("Pets", "expense", "🐾", "#AC8E68"),
    ("Taxes & Fees", "expense", "🏛️", "#8E8E93"),
    ("Bank & Card Fees", "expense", "🏦", "#636366"),
    ("Loans & Debt", "expense", "🏛️", "#5E5CE6"),
    ("Other Expense", "expense", "📦", "#8E8E93"),
    ("Hobbies", "expense", "🎨", "#FF9F0A"),
    ("Salary", "income", "💼", "#30D158"),
    ("Side Income", "income", "💰", "#32D74B"),
    ("Rental Income", "income", "🏘️", "#66D4CF"),
    ("Investment Income", "income", "📈", "#0A84FF"),
    ("Other Income", "income", "➕", "#8E8E93"),
    ("Bonus", "income", "🏆", "#FFD60A"),
    ("Vouchers & Perks", "income", "🎟️", "#FF9F0A"),
]


# Default categories added after the first release (also added to existing installs)
ADDED_LATER = {"Loans & Debt", "Bonus", "Vouchers & Perks", "Hobbies"}


def seed_prefs(db: Session) -> None:
    """Installs from the Turkey-only release have data but no preferences: keep them on TRY + the Turkey pack."""
    from . import prefs
    from .models import AppSetting, Transaction

    if db.scalar(select(AppSetting.key).where(AppSetting.key.like(prefs.PREFIX + "%")).limit(1)):
        return
    if db.scalar(select(Transaction.id).where(Transaction.currency == "TRY").limit(1)):
        # Written directly (not prefs.save) so the stored TRY-based FX rates are kept
        legacy = {"base_currency": "TRY", "currencies": ["TRY", "USD", "EUR"], "locale": "tr-TR", "region": "tr",
                  "setup_done": True}
        for name, value in legacy.items():
            db.merge(AppSetting(key=prefs.PREFIX + name, value=json.dumps(value)))
        db.commit()
        prefs.load(db)


def seed(db: Session) -> None:
    existing = set(db.scalars(select(Category.name)))
    for i, (name, kind, icon, color) in enumerate(DEFAULT_CATEGORIES):
        if not existing or (name in ADDED_LATER and name not in existing):
            db.add(Category(name=name, kind=kind, icon=icon, color=color, sort=i))
    db.commit()
    seed_prefs(db)
