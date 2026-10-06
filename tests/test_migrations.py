"""An existing installation keeps its transactions when new reporting columns arrive."""

from sqlalchemy import create_engine, text

from app import db as database


def test_reporting_migration_is_additive_and_repeatable(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE categories (id INTEGER PRIMARY KEY, name VARCHAR)")
        )
        conn.execute(
            text(
                "CREATE TABLE transactions (id INTEGER PRIMARY KEY, amount_cents INTEGER)"
            )
        )
        conn.execute(text("INSERT INTO categories VALUES (1, 'Groceries')"))
        conn.execute(text("INSERT INTO transactions VALUES (1, 12345)"))
    monkeypatch.setattr(database, "engine", engine)
    database.migrate()
    database.migrate()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name, essential FROM categories")).one() == (
            "Groceries",
            0,
        )
        assert conn.execute(
            text("SELECT amount_cents, spending_type FROM transactions")
        ).one() == (12345, "variable")
    engine.dispose()


def test_legacy_card_columns_fold_into_currency_dicts(tmp_path, monkeypatch):
    import json

    engine = create_engine(f"sqlite:///{tmp_path / 'cards.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE credit_cards (id INTEGER PRIMARY KEY, name VARCHAR, debt_try_cents INTEGER, "
                          "debt_usd_cents INTEGER, debt_eur_cents INTEGER)"))
        conn.execute(text("CREATE TABLE card_statements (id INTEGER PRIMARY KEY, total_try_cents INTEGER, "
                          "total_usd_cents INTEGER, total_eur_cents INTEGER)"))
        conn.execute(text("CREATE TABLE transactions (id INTEGER PRIMARY KEY, amount_cents INTEGER)"))
        conn.execute(text("INSERT INTO credit_cards VALUES (1, 'Bonus', 12345, 0, 500)"))
        conn.execute(text("INSERT INTO card_statements VALUES (1, 9000, 100, 0)"))
    monkeypatch.setattr(database, "engine", engine)
    database.migrate()
    database.migrate()
    with engine.connect() as conn:
        assert json.loads(conn.execute(text("SELECT debts FROM credit_cards")).scalar()) == {"TRY": 12345, "EUR": 500}
        totals, currency = conn.execute(text("SELECT totals, currency FROM card_statements")).one()
        assert json.loads(totals) == {"TRY": 9000, "USD": 100} and currency == "TRY"
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(credit_cards)"))}
        assert "debt_try_cents" not in cols
    engine.dispose()
