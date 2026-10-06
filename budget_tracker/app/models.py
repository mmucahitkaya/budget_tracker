"""Database models. Amounts are stored as integers in minor units (cents)."""
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import prefs
from .db import Base


def _base() -> str:
    """Column default: the household base currency at insert time."""
    return prefs.base()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    ha_user_id: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    username: Mapped[str] = mapped_column(String, default="")  # HA username (for permission checks)
    telegram_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="expense")  # expense | income
    icon: Mapped[str] = mapped_column(String, default="•")
    color: Mapped[str] = mapped_column(String, default="#8E8E93")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    essential: Mapped[bool] = mapped_column(Boolean, default=False)


class CreditCard(Base):
    __tablename__ = "credit_cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    bank: Mapped[str] = mapped_column(String, default="")
    last4: Mapped[str] = mapped_column(String, default="")
    limit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    statement_day: Mapped[int] = mapped_column(Integer, default=1)
    due_day: Mapped[int] = mapped_column(Integer, default=10)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # account owner: debt and reminders
    # Card user (supplementary card): statement expenses are attributed to this person; None = owner
    holder_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    color: Mapped[str] = mapped_column(String, default="#0A84FF")
    # Current debt per currency {currency: cents} (entered manually or updated from a statement)
    debts: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    debt_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class CardStatement(Base):
    __tablename__ = "card_statements"
    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("credit_cards.id", ondelete="CASCADE"))
    period_end: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String, default=_base)  # primary statement currency
    totals: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict)  # statement balance per currency {currency: cents}
    min_payment_cents: Mapped[int] = mapped_column(Integer, default=0)
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)


class CardPayment(Base):
    """A payment toward card debt. Not an expense (bank -> card transfer); it reduces the card debt."""

    __tablename__ = "card_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("credit_cards.id", ondelete="CASCADE"))
    statement_id: Mapped[int | None] = mapped_column(ForeignKey("card_statements.id", ondelete="SET NULL"), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String, default=_base)
    date: Mapped[date] = mapped_column(Date)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class InstallmentPlan(Base):
    __tablename__ = "installment_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("credit_cards.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(String)
    monthly_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String, default=_base)
    count: Mapped[int] = mapped_column(Integer)
    # First day of the month in which installment #1 appears on the statement
    first_month: Mapped[date] = mapped_column(Date)
    # Each month's installment is stored as a separate transaction; category and person belong to the series
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, default="expense")  # expense | income
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String, default=_base)
    date: Mapped[date] = mapped_column(Date, index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    payment_method: Mapped[str] = mapped_column(String, default="card")  # cash | bank | card
    card_id: Mapped[int | None] = mapped_column(ForeignKey("credit_cards.id", ondelete="SET NULL"), nullable=True)
    merchant: Mapped[str] = mapped_column(String, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    installment_no: Mapped[int | None] = mapped_column(Integer, nullable=True)  # installment number (within the series)
    installment_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("installment_plans.id", ondelete="SET NULL"), nullable=True
    )
    recurring_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_payments.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # When the spending happened (e.g. the time the Telegram message was sent), local time
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Client-generated unique key: submitting the same form twice yields a single record
    client_ref: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    spending_type: Mapped[str] = mapped_column(String, default="variable")
    # User confirmed the category (hidden from the to-categorize list even if it is "Other")
    category_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    category: Mapped[Category | None] = relationship(lazy="joined")
    installment_plan: Mapped[InstallmentPlan | None] = relationship(lazy="joined")


class Account(Base):
    """Explicit balance ledger; existing expenses are never silently assigned to an account."""
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # cash | bank | savings
    currency: Mapped[str] = mapped_column(String, default=_base)
    opening_cents: Mapped[int] = mapped_column(Integer, default=0)
    opening_date: Mapped[date] = mapped_column(Date)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class AccountMovement(Base):
    __tablename__ = "account_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    target_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    source_cents: Mapped[int] = mapped_column(Integer, default=0)
    target_cents: Mapped[int] = mapped_column(Integer, default=0)
    date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String)  # transfer | deposit | withdrawal | adjustment
    note: Mapped[str] = mapped_column(String, default="")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    client_ref: Mapped[str] = mapped_column(String, unique=True)


class SavingsGoal(Base):
    __tablename__ = "savings_goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String, default=_base)
    target_cents: Mapped[int] = mapped_column(Integer)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    monthly_cents: Mapped[int] = mapped_column(Integer, default=0)
    reminder_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class GoalAllocation(Base):
    """Signed reservations, not expenses or transfers. User attribution is retained."""
    __tablename__ = "goal_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("savings_goals.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    client_ref: Mapped[str] = mapped_column(String, unique=True)


class RecurringPayment(Base):
    __tablename__ = "recurring_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="expense")
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String, default=_base)
    frequency: Mapped[str] = mapped_column(String, default="monthly")  # weekly | monthly | yearly
    next_date: Mapped[date] = mapped_column(Date)
    day: Mapped[int] = mapped_column(Integer, default=1)  # day of month (prevents end-of-month drift)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # last payment (inclusive); None = open-ended
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    payment_method: Mapped[str] = mapped_column(String, default="bank")
    card_id: Mapped[int | None] = mapped_column(ForeignKey("credit_cards.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    auto_create: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    # Raise months ("1,7"): on the 1st of those months the person gets a reminder to update the amount
    raise_months: Mapped[str] = mapped_column(String, default="")
    # Per-month amount override {"2026-12": 9000000} (e.g. car loan installments); amount_cents for other months
    amount_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Paid in gold (e.g. 2 quarter-gold coins): "gold:CEYREKALTIN" x qty; base-currency amount at that day's price
    asset_code: Mapped[str] = mapped_column(String, default="")
    asset_qty: Mapped[float | None] = mapped_column(Float, nullable=True)


class Budget(Base):
    """Monthly category limit. Each month has its own limit; past months do not change."""

    __tablename__ = "budgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String)  # YYYY-MM
    limit_cents: Mapped[int] = mapped_column(Integer)  # base currency
    __table_args__ = (UniqueConstraint("category_id", "month"),)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, default="auto")  # auto | receipt | statement
    filename: Mapped[str] = mapped_column(String)
    stored_path: Mapped[str] = mapped_column(String)
    mime: Mapped[str] = mapped_column(String)
    # pending | processing | review | done | error | discarded
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("credit_cards.id", ondelete="SET NULL"), nullable=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # reply address if it came from Telegram
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # message to delete after processing
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MerchantRule(Base):
    __tablename__ = "merchant_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String, unique=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))


class FxRate(Base):
    __tablename__ = "fx_rates"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String, primary_key=True)
    rate: Mapped[float] = mapped_column(Float)  # 1 unit of currency = rate units of the base currency


class SentNotice(Base):
    """Key record so the same notification is never sent twice."""

    __tablename__ = "sent_notices"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("key"),)


class TelegramMessage(Base):
    """Messages to delete from the Telegram chat once they expire (so data does not pile up in Telegram)."""

    __tablename__ = "telegram_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[int] = mapped_column(Integer)
    delete_after: Mapped[datetime] = mapped_column(DateTime, index=True)


class AuditLog(Base):
    """Change history: who changed what and when (before/after), and whether it was undone."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    entity: Mapped[str] = mapped_column(String, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String)  # create | update | delete | commit | undo
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str] = mapped_column(String, default="")
    undone_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Asset(Base):
    """Investment asset: gold/silver/fx/stock are valued by price; funds by a manually entered amount."""

    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String)  # gold | silver | fx | stock | fund
    code: Mapped[str] = mapped_column(String, default="")  # GRA, CEYREKALTIN, USD, THYAO; may be empty for funds
    name: Mapped[str] = mapped_column(String)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # None = shared
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("savings_goals.id", ondelete="SET NULL"), nullable=True)
    # Funds only: current value and (optional) total invested
    manual_value_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manual_value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    manual_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String, default="")  # group name for physical assets (e.g. "Wedding jewelry")
    location: Mapped[str] = mapped_column(String, default="")  # "" = physical (in hand), otherwise bank/broker name
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AssetLot(Base):
    """A buy or sell. Profit/loss is computed using the average cost method."""

    __tablename__ = "asset_lots"
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String)  # buy | sell
    date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[float] = mapped_column(Float)
    unit_price_cents: Mapped[int] = mapped_column(Integer)  # base currency per unit
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str] = mapped_column(String, default="")
    client_ref: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)


class AssetPrice(Base):
    """Daily price (base currency per unit, buy side). Key: "kind:code"."""

    __tablename__ = "asset_prices"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    price_cents: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class PortfolioSnapshot(Base):
    """Daily value per asset (value history chart)."""

    __tablename__ = "portfolio_snapshots"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    value_cents: Mapped[int] = mapped_column(Integer)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base):
    """Household-wide settings (e.g. report period start day)."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)
