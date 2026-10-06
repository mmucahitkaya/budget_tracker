from datetime import date, timedelta
from fastapi.testclient import TestClient
from app import analytics, reporting, services
from app.models import Budget, Category, CreditCard, Document, RecurringPayment, User
from app.main import app


def tx(db, amount, on, **kwargs):
    args = dict(
        kind="expense",
        amount_cents=amount * 100,
        currency="TRY",
        on=on,
        category_id=1,
        user_id=None,
        payment_method="cash",
        card_id=None,
    )
    spending_type = kwargs.pop("spending_type", "variable")
    args.update(kwargs)
    t = services.create_transaction(db, **args)
    t.spending_type = spending_type
    db.commit()
    return t


def test_partial_month_compares_same_days_and_excludes_future(db):
    tx(db, 100, date(2026, 10, 1))
    tx(db, 500, date(2026, 10, 25))
    tx(db, 80, date(2026, 9, 1))
    tx(db, 900, date(2026, 9, 25))
    r = reporting.workspace(
        db, date(2026, 10, 1), date(2026, 10, 31), today=date(2026, 10, 5)
    )
    assert r["expense"] == 100 and r["changes"][0]["delta"] == 20
    assert r["comparison_end"] == "2026-09-05"
    legacy = analytics.stats(db, "2026-10", "2026-10", today=date(2026, 10, 5))
    assert legacy["category_changes"][0]["delta"] == 20


def test_custom_range_uses_equal_length_previous_period(db):
    ps, pe = reporting.comparison_range(date(2026, 9, 15), date(2026, 10, 14))
    assert (pe - ps).days == 29 and pe == date(2026, 9, 14)
    assert reporting.comparison_range(date(2026, 2, 1), date(2026, 2, 28)) == (
        date(2026, 1, 1),
        date(2026, 1, 31),
    )


def test_category_history_exposes_missing_rates_without_breaking_legacy_shape(db):
    tx(db, 100, date.today(), currency="USD")
    c = TestClient(app)
    path = "/api/reports/category-trend?category_id=1"
    assert isinstance(c.get(path).json(), list)
    meta = c.get(path + "&with_meta=true").json()
    assert len(meta["series"]) == 12
    assert meta["fx_missing"] == [{"currency": "USD", "amount": 100}]
    assert c.get(path + "&months=1000").status_code == 422


def test_dashboard_summary_uses_same_day_comparison(db, monkeypatch):
    from app.routers import reports

    class Clock(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 5)

    monkeypatch.setattr(reports, "date", Clock)
    tx(db, 100, date(2026, 10, 1))
    tx(db, 1000, date(2026, 10, 20))
    tx(db, 80, date(2026, 9, 1))
    tx(db, 800, date(2026, 9, 20))
    r = TestClient(app).get("/api/reports/summary?month=2026-10").json()
    assert (r["expense"], r["prev_expense"]) == (100, 80)


def test_projection_filters_every_weekly_occurrence_and_end_date(db):
    u = User(ha_user_id="one", name="One")
    v = User(ha_user_id="two", name="Two")
    db.add_all([u, v])
    db.flush()
    db.add_all(
        [
            RecurringPayment(
                name="Weekly",
                amount_cents=10000,
                frequency="weekly",
                next_date=date(2026, 10, 9),
                day=9,
                end_date=date(2026, 10, 23),
                user_id=u.id,
                currency="TRY",
            ),
            RecurringPayment(
                name="Other person",
                amount_cents=999000,
                frequency="monthly",
                next_date=date(2026, 10, 10),
                day=10,
                user_id=v.id,
                currency="TRY",
            ),
            RecurringPayment(
                name="Other currency",
                amount_cents=999000,
                frequency="monthly",
                next_date=date(2026, 10, 10),
                day=10,
                user_id=u.id,
                currency="USD",
            ),
        ]
    )
    db.commit()
    r = analytics.daily_cumulative(
        db,
        "2026-10",
        analytics.Filters(user_id=u.id, currency="TRY"),
        today=date(2026, 10, 8),
    )
    assert r["projection"] == 300


def test_one_off_not_extrapolated_and_history_estimate_has_range(db):
    now = date(2026, 10, 15)
    for day in (
        date(2026, 8, 17),
        date(2026, 8, 24),
        date(2026, 8, 31),
        date(2026, 9, 7),
        date(2026, 9, 14),
        date(2026, 9, 21),
        date(2026, 9, 28),
        date(2026, 10, 5),
    ):
        tx(db, 700, day)
    tx(db, 10000, date(2026, 10, 1), spending_type="one_off")
    r = analytics.daily_cumulative(db, "2026-10", today=now)
    assert r["projection"] == 12300  # 10,700 realized + 16*100
    assert r["projection_low"] == r["projection_high"] == 12300
    assert "Median" in r["projection_basis"]


def test_workspace_quality_budgets_and_type_breakdown(db):
    now = date(2026, 10, 5)
    db.add_all(
        [
            Budget(category_id=1, month="2026-10", limit_cents=50000),
            Document(
                kind="receipt",
                filename="test",
                stored_path="/tmp/fake",
                mime="image/png",
                status="review",
            ),
            RecurringPayment(
                name="weekly",
                amount_cents=10000,
                next_date=date(2026, 10, 9),
                frequency="weekly",
                day=9,
                category_id=1,
            ),
        ]
    )
    db.commit()
    tx(db, 300, date(2026, 10, 1), spending_type="one_off")
    tx(db, 50, date(2026, 10, 2), category_id=None)
    r = reporting.workspace(db, date(2026, 10, 1), now, today=now)
    assert (
        r["quality"]["review_documents"] == 1
        and r["quality"]["uncategorized_count"] == 1
    )
    assert r["budgets"][0]["planned"] == 400
    assert r["series"][0]["one_off"] == 300
    assert len(r["insights"]) <= 3


def test_transaction_drilldown_retains_dates_category_zero_and_merchant(db):
    c = TestClient(app)
    t = tx(db, 100, date(2026, 9, 7), category_id=None, merchant="Migros 12")
    tx(db, 100, date(2026, 9, 8), merchant="Migros 12")
    tx(db, 100, date(2026, 9, 9), category_id=None, merchant="Shell")
    r = c.get(
        "/api/transactions",
        params=dict(
            start="2026-09-01", end="2026-09-30", category_id=0, merchant_key="migros"
        ),
    )
    assert r.status_code == 200 and [x["id"] for x in r.json()] == [t.id]
    assert (
        c.get("/api/reports/workspace?start=2026-10-05&end=2026-01-01").status_code
        == 422
    )


def test_cashflow_filters_and_no_double_counted_card_recurring(db):
    a = CreditCard(name="A", statement_day=20, due_day=30)
    b = CreditCard(name="B", statement_day=20, due_day=30)
    db.add_all([a, b])
    db.flush()
    db.add_all(
        [
            RecurringPayment(
                name="A charge",
                amount_cents=10000,
                next_date=date(2026, 10, 10),
                day=10,
                card_id=a.id,
                payment_method="card",
            ),
            RecurringPayment(
                name="B charge",
                amount_cents=90000,
                next_date=date(2026, 10, 10),
                day=10,
                card_id=b.id,
                payment_method="card",
            ),
        ]
    )
    db.commit()
    result = analytics.cashflow(
        db, 30, date(2026, 10, 5), analytics.Filters(card_id=a.id)
    )
    assert result["out"] == 100 and len(result["events"]) == 1
    assert result["opening_balance"] is None
