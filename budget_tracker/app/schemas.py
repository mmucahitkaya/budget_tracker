from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from . import prefs


def _upper(v):
    return v.strip().upper() if isinstance(v, str) else v


def _supported(v: str) -> str:
    if len(v) != 3 or v not in prefs.SUPPORTED_CURRENCIES:
        raise ValueError(f"Unsupported currency: {v}")
    return v


# ISO 4217 code from the supported list (case-insensitive input)
Currency = Annotated[str, BeforeValidator(_upper), AfterValidator(_supported)]


def _amount_dict(v: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for cur, amount in (v or {}).items():
        code = _supported(_upper(cur))
        if amount is None:
            continue
        if amount < 0 or amount > 1_000_000_000:
            raise ValueError("Amount out of range")
        out[code] = out.get(code, 0) + amount
    return out


# {currency: amount}, e.g. {"USD": 120.5, "EUR": 10}
AmountDict = Annotated[dict[str, float], AfterValidator(_amount_dict)]
Kind = Literal["expense", "income"]
Method = Literal["cash", "bank", "card", "voucher"]  # voucher: shopping voucher / meal card


class TransactionIn(BaseModel):
    spending_type: Literal["variable", "fixed", "one_off"] = "variable"
    kind: Kind = "expense"
    amount: float = Field(gt=0, le=1_000_000_000)
    currency: Currency = Field(default_factory=prefs.base)
    date: date
    category_id: int | None = None
    user_id: int | None = None
    payment_method: Method = "card"
    card_id: int | None = None
    merchant: str = Field("", max_length=200)
    note: str = Field("", max_length=500)
    installment_count: int | None = Field(default=None, ge=1, le=36)
    client_ref: str | None = Field(default=None, max_length=64)


class CategoryIn(BaseModel):
    essential: bool = False
    name: str
    kind: Kind = "expense"
    icon: str = "•"
    color: str = "#8E8E93"
    archived: bool = False


class CardIn(BaseModel):
    name: str
    bank: str = ""
    last4: str = ""
    limit: float | None = None
    statement_day: int = Field(ge=1, le=31)
    due_day: int = Field(ge=1, le=31)
    owner_id: int | None = None
    holder_id: int | None = None
    color: str = "#0A84FF"
    debts: AmountDict = Field(default_factory=dict)
    archived: bool = False


class StatementIn(BaseModel):
    period_end: date
    due_date: date
    currency: Currency = Field(default_factory=prefs.base)
    totals: AmountDict = Field(default_factory=dict)
    min_payment: float = 0
    paid: bool = False


class PaymentIn(BaseModel):
    amount: float = Field(gt=0, le=1_000_000_000)
    currency: Currency = Field(default_factory=prefs.base)
    date: date
    statement_id: int | None = None
    note: str = Field("", max_length=200)


class InstallmentIn(BaseModel):
    card_id: int
    description: str
    monthly: float = Field(gt=0)
    currency: Currency = Field(default_factory=prefs.base)
    count: int = Field(ge=2, le=48)
    first_month: str  # YYYY-MM


class RecurringIn(BaseModel):
    name: str
    kind: Kind = "expense"
    amount: float = Field(gt=0)
    currency: Currency = Field(default_factory=prefs.base)
    frequency: Literal["weekly", "monthly", "yearly"] = "monthly"
    next_date: date
    category_id: int | None = None
    payment_method: Method = "bank"
    card_id: int | None = None
    user_id: int | None = None
    auto_create: bool = True
    active: bool = True
    note: str = ""
    end_date: date | None = None  # last payment date (inclusive)
    backfill: bool = False  # if the first date is in the past, also add past payments as transactions
    last_business_day: bool = False  # last business day of each month (instead of a fixed day)
    raise_months: list[Annotated[int, Field(ge=1, le=12)]] = Field(default_factory=list, max_length=12)


YM = r"^\d{4}-(0[1-9]|1[0-2])$"


class BudgetIn(BaseModel):
    category_id: int
    limit: float = Field(ge=0, le=1_000_000_000)
    month: str | None = Field(None, pattern=YM)


class BudgetCopyIn(BaseModel):
    from_month: str = Field(pattern=YM)
    to_month: str = Field(pattern=YM)


# ---- Document review (receipt/statement draft) ---------------------------------
# The draft sent back by the client is validated with the same rules as regular transaction input.


class DraftItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str = Field("", max_length=300)
    amount: float = Field(ge=-1_000_000_000, le=1_000_000_000)  # receipt discounts may be negative


class DraftRowIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    include: bool
    mode: Literal["transaction", "installment_only"] = "transaction"
    kind: Kind = "expense"
    date: date
    amount: float = Field(gt=0, le=1_000_000_000)
    line_amount: float | None = Field(None, ge=0, le=1_000_000_000)
    currency: Currency = Field(default_factory=prefs.base)
    merchant: str = Field("", max_length=200)
    category_id: int | None = None
    payment_method: Method = "card"
    card_id: int | None = None
    installment_count: int | None = Field(None, ge=2, le=48)
    installment_no: int | None = Field(None, ge=1, le=48)
    duplicate_of: int | None = None
    match: int | None = None
    match_candidates: list[dict] | None = Field(None, max_length=10)
    items: list[DraftItemIn] | None = Field(None, max_length=300)
    note: str | None = Field(None, max_length=300)

    @field_validator("installment_count", mode="before")
    @classmethod
    def _single_is_none(cls, v):
        return None if v in (0, 1) else v

    @model_validator(mode="after")
    def _installment_no_within_count(self):
        if self.installment_no and self.installment_count and self.installment_no > self.installment_count:
            raise ValueError("Installment number cannot exceed the installment count")
        return self


class DraftStatementIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    period_end: date
    due_date: date
    currency: Currency = Field(default_factory=prefs.base)
    totals: AmountDict = Field(default_factory=dict)
    min_payment: float = Field(0, ge=0, le=1_000_000_000)
    period_spending: float | None = Field(None, ge=0, le=1_000_000_000)


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    doc_type: Literal["receipt", "statement"]
    card_id: int | None = None
    statement: DraftStatementIn | None = None
    rows: list[DraftRowIn] = Field(max_length=500)


class DraftIn(BaseModel):
    draft: DraftBody
