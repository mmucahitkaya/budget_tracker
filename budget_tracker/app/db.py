from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATA_DIR, DB_PATH

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# create_all does not add new columns to existing tables; columns added later go here
_ADDED_COLUMNS = [
    ("categories", "essential", "BOOLEAN NOT NULL DEFAULT 0"),
    ("transactions", "spending_type", "VARCHAR NOT NULL DEFAULT 'variable'"),
    ("recurring_payments", "end_date", "DATE"),
    ("users", "telegram_chat_id", "INTEGER"),
    ("documents", "telegram_chat_id", "INTEGER"),
    ("transactions", "occurred_at", "DATETIME"),
    ("transactions", "client_ref", "VARCHAR"),
    ("documents", "telegram_message_id", "INTEGER"),
    ("users", "username", "VARCHAR DEFAULT ''"),
    ("assets", "location", "VARCHAR NOT NULL DEFAULT ''"),
    ("recurring_payments", "raise_months", "VARCHAR NOT NULL DEFAULT ''"),
    ("recurring_payments", "amount_plan", "JSON"),
    ("credit_cards", "holder_id", "INTEGER REFERENCES users(id)"),
    ("recurring_payments", "asset_code", "VARCHAR NOT NULL DEFAULT ''"),
    ("recurring_payments", "asset_qty", "FLOAT"),
    ("transactions", "category_confirmed", "BOOLEAN NOT NULL DEFAULT 0"),
    ("transactions", "installment_no", "INTEGER"),
    ("installment_plans", "category_id", "INTEGER REFERENCES categories(id)"),
    ("installment_plans", "user_id", "INTEGER REFERENCES users(id)"),
    # Per-currency card debts / statement totals (replace fixed TRY/USD/EUR columns)
    ("credit_cards", "debts", "JSON"),
    ("card_statements", "totals", "JSON"),
    ("card_statements", "currency", "VARCHAR NOT NULL DEFAULT ''"),
]
# Separate index because ALTER TABLE cannot add UNIQUE
_ADDED_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_transactions_client_ref ON transactions (client_ref)",
]


def _migrate_monthly_budgets(conn) -> None:
    """budgets: one limit per category -> one limit per category + month. Existing limits move to the current month."""
    from datetime import date

    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(budgets)")}
    if not cols or "month" in cols:
        return
    ym = date.today().strftime("%Y-%m")
    conn.exec_driver_sql(
        "CREATE TABLE budgets_new (id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL "
        "REFERENCES categories(id) ON DELETE CASCADE, month VARCHAR NOT NULL, limit_cents INTEGER NOT NULL, "
        "UNIQUE (category_id, month))"
    )
    conn.exec_driver_sql(
        "INSERT INTO budgets_new (id, category_id, month, limit_cents) SELECT id, category_id, ?, limit_cents FROM budgets",
        (ym,),
    )
    conn.exec_driver_sql("DROP TABLE budgets")
    conn.exec_driver_sql("ALTER TABLE budgets_new RENAME TO budgets")


# Fixed-currency card columns from the Turkey-only release: folded into the JSON dicts, then dropped
_LEGACY_CARD_COLUMNS = {
    "credit_cards": ("debts", {"TRY": "debt_try_cents", "USD": "debt_usd_cents", "EUR": "debt_eur_cents"}),
    "card_statements": ("totals", {"TRY": "total_try_cents", "USD": "total_usd_cents", "EUR": "total_eur_cents"}),
}


def _migrate_card_currency_dicts(conn) -> None:
    import json

    for table, (target, legacy) in _LEGACY_CARD_COLUMNS.items():
        cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        present = {cur: col for cur, col in legacy.items() if col in cols}
        if not present or target not in cols:
            continue
        for row in conn.exec_driver_sql(f"SELECT id, {', '.join(present.values())} FROM {table}").all():
            values = {cur: int(v) for cur, v in zip(present, row[1:]) if v}
            conn.exec_driver_sql(f"UPDATE {table} SET {target} = ? WHERE id = ?", (json.dumps(values), row[0]))
        if table == "card_statements" and "currency" in cols:
            conn.exec_driver_sql("UPDATE card_statements SET currency = 'TRY' WHERE currency = '' OR currency IS NULL")
        for col in present.values():
            conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {col}")


def migrate() -> None:
    with engine.begin() as conn:
        _migrate_monthly_budgets(conn)
        for table, column, ddl in _ADDED_COLUMNS:
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if cols and column not in cols:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        _migrate_card_currency_dicts(conn)
        for ddl in _ADDED_INDEXES:
            conn.exec_driver_sql(ddl)
