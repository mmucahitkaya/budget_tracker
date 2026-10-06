from datetime import date

from app import analytics, services
from app.models import Budget, FxRate, RecurringPayment


def add(db, amount, on, cat=1, kind="expense", merchant="", currency="TRY", method="cash", recurring_id=None, user_id=None):
    tx = services.create_transaction(
        db, kind=kind, amount_cents=amount * 100, currency=currency, on=on, category_id=cat, user_id=user_id,
        payment_method=method, card_id=None, merchant=merchant, recurring_id=recurring_id,
    )
    db.commit()
    return tx


def test_daily_cumulative_and_projection(db):
    db.add(RecurringPayment(name="Rent", amount_cents=1000000, next_date=date(2026, 10, 20), day=20, category_id=5))
    db.commit()
    add(db, 100, date(2026, 10, 1))
    add(db, 200, date(2026, 10, 5))
    add(db, 300, date(2026, 9, 3))
    r = analytics.daily_cumulative(db, "2026-10", today=date(2026, 10, 10))
    d = {x["day"]: x for x in r["days"]}
    assert d[1]["current"] == 100 and d[5]["current"] == 300 and d[10]["current"] == 300
    assert d[11]["current"] is None  # future days are not plotted
    assert d[3]["previous"] == 300 and d[30]["previous"] == 300 and d[31]["previous"] is None
    assert r["previous_same_day"] == 300
    # 300 TRY / 10 days * 31 days = 930 + remaining rent 10,000
    assert r["projection"] == 10930


def test_stats(db):
    db.add(FxRate(date=date(2026, 10, 1), currency="USD", rate=40.0))
    db.commit()
    add(db, 50000, date(2026, 10, 1), cat=20, kind="income", merchant="Salary", method="bank")
    add(db, 100, date(2026, 10, 5), merchant="MIGROS 123")  # Monday
    add(db, 300, date(2026, 10, 6), merchant="Migros 55")
    add(db, 11, date(2026, 10, 6), merchant="Spotify", currency="USD", method="card")
    s = analytics.stats(db, "2026-10", "2026-10", today=date(2026, 10, 10))
    assert s["count"] == 3 and s["expense"] == 840 and s["income"] == 50000
    assert s["daily_avg"] == 84 and s["median_ticket"] == 300 and s["max_ticket"] == 440
    assert s["top_merchants"][0]["name"] == "Spotify" and s["top_merchants"][1]["total"] == 400
    assert s["top_merchants"][1]["count"] == 2  # two Migros branches are merged
    assert s["weekday"][0]["total"] == 100 and s["weekday"][1]["total"] == 740
    assert s["savings_rate"] == round((50000 - 840) / 50000, 4)
    only_usd = analytics.stats(db, "2026-10", "2026-10", analytics.Filters(currency="USD"), today=date(2026, 10, 10))
    assert only_usd["count"] == 1


def test_monthly_summary(db):
    db.add(Budget(category_id=1, month="2026-09", limit_cents=10000))
    db.commit()
    add(db, 150, date(2026, 9, 2), cat=1)
    add(db, 50, date(2026, 9, 3), cat=2)
    add(db, 100, date(2026, 8, 3), cat=2)
    s = analytics.monthly_summary(db, "2026-09")
    assert s["expense"] == 20000 and s["change"] == 1.0
    assert s["top"][0] == ("Groceries", 15000)
    assert s["over_budget"][0]["category"] == "Groceries"


def test_recurring_end_date_and_backfill(db):
    from app.finance import recurring_dates
    from app.models import Transaction

    # Loan: monthly from Aug 15, 2026 to Jan 15, 2027
    dates = recurring_dates(date(2026, 8, 15), "monthly", 15, date(2027, 1, 15), date(2030, 1, 1))
    assert len(dates) == 6 and dates[-1] == date(2027, 1, 15)

    r = RecurringPayment(name="Loan", amount_cents=500000, next_date=date(2026, 8, 15), day=15,
                         end_date=date(2026, 11, 15), category_id=1)
    db.add(r)
    db.flush()
    done = services.materialize_recurring(db, r, date(2026, 10, 4))
    db.commit()
    assert done == [date(2026, 8, 15), date(2026, 9, 15)]
    assert r.next_date == date(2026, 10, 15) and r.active
    assert db.query(Transaction).filter(Transaction.recurring_id == r.id).count() == 2
    services.materialize_recurring(db, r, date(2026, 12, 1))
    assert r.active is False  # deactivated after the end date


def test_db_snapshot_rotates(db, monkeypatch, tmp_path):
    import sqlite3

    from app import scheduler

    monkeypatch.setattr(scheduler, "BACKUP_DIR", tmp_path)
    add(db, 42, date(2026, 10, 1))
    for d in range(1, 18):
        scheduler.job_db_snapshot(date(2026, 10, d))
    files = sorted(p.name for p in tmp_path.glob("budget-*.db"))
    assert len(files) == 14 and files[0] == "budget-2026-10-04.db"
    con = sqlite3.connect(tmp_path / files[-1])
    assert con.execute("select amount_cents from transactions").fetchone() == (4200,)
