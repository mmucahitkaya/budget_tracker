from datetime import date

from app import finance, services
from app.fx import Converter, parse_tcmb_xml
from app.models import CreditCard, Document, FxRate, InstallmentPlan, Transaction


def make_card(db, statement_day=15, due_day=25):
    c = CreditCard(name="Bonus", bank="Garanti", last4="1234", statement_day=statement_day, due_day=due_day)
    db.add(c)
    db.commit()
    return c


def test_money_roundtrip():
    assert finance.to_cents(12.345) == 1234 or finance.to_cents(12.345) == 1235
    assert finance.to_cents(1234.56) == 123456
    assert finance.from_cents(123456) == 1234.56


def test_add_months_clamps_month_end():
    assert finance.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert finance.add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_recurring_keeps_anchor_day():
    d = date(2026, 1, 31)
    d = finance.advance_recurring(d, "monthly", 31)
    assert d == date(2026, 2, 28)
    d = finance.advance_recurring(d, "monthly", 31)
    assert d == date(2026, 3, 31)
    assert finance.advance_recurring(date(2026, 3, 1), "yearly", 1) == date(2027, 3, 1)


def test_statement_month_and_due_date(db):
    c = make_card(db, statement_day=15, due_day=25)
    assert finance.statement_month_for(c, date(2026, 10, 15)) == date(2026, 10, 1)
    assert finance.statement_month_for(c, date(2026, 10, 16)) == date(2026, 11, 1)
    # Sun Oct 25, 2026 -> Mon Oct 26
    assert finance.next_due_date(c, date(2026, 10, 4)) == date(2026, 10, 26)
    assert finance.next_due_date(c, date(2026, 10, 27)) == date(2026, 11, 25)
    assert finance.next_statement_date(c, date(2026, 10, 4)) == date(2026, 10, 15)
    assert finance.next_statement_date(c, date(2026, 10, 16)) == date(2026, 11, 15)
    assert finance.day_distance(30, 1) == 2 and finance.day_distance(15, 16) == 1
    # Due date is in the month after the statement cut
    c2 = make_card(db, statement_day=28, due_day=8)
    assert finance.next_due_date(c2, date(2026, 10, 4)) == date(2026, 10, 8)
    assert finance.next_due_date(c2, date(2026, 10, 9)) == date(2026, 11, 9)  # Nov 8 is a Sunday


def test_installment_transaction_and_schedule(db):
    c = make_card(db)
    tx = services.create_transaction(
        db, kind="expense", amount_cents=120000, currency="TRY", on=date(2026, 10, 3), category_id=1,
        user_id=None, payment_method="card", card_id=c.id, merchant="Teknosa", installment_count=3,
    )
    db.commit()
    plan = db.get(InstallmentPlan, tx.installment_plan_id)
    assert plan.monthly_cents == 40000 and plan.first_month == date(2026, 10, 1)
    sched = finance.installment_schedule([plan], date(2026, 10, 4), 4)
    assert [s["totals"].get("TRY") for s in sched] == [400.0, 400.0, 400.0, None]
    assert finance.remaining_installments(plan, date(2026, 11, 2)) == 2


def test_merchant_rule_learning(db):
    finance.learn_rule(db, "MIGROS SANAL MARKET 0123", 1)
    db.commit()
    assert finance.normalize_merchant("Migros Sanal Market 99") == "migros sanal market"
    assert finance.rule_category(db, "MİGROS SANAL MARKET 77") == 1


def test_fx_conversion(db):
    db.add(FxRate(date=date(2026, 10, 1), currency="USD", rate=40.0))
    db.commit()
    conv = Converter(db)
    assert conv.to_base_cents(1000, "USD", date(2026, 10, 3)) == 40000  # previous day's rate
    assert conv.to_base_cents(1000, "TRY", date(2026, 10, 3)) == 1000


def test_parse_tcmb_xml():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Tarih_Date Tarih="02.10.2026" Date="10/02/2026">
      <Currency CrossOrder="0" Kod="USD" CurrencyCode="USD"><Unit>1</Unit><ForexSelling>41.5</ForexSelling></Currency>
      <Currency CrossOrder="1" Kod="JPY" CurrencyCode="JPY"><Unit>100</Unit><ForexSelling>28</ForexSelling></Currency>
      <Currency CrossOrder="9" Kod="EUR" CurrencyCode="EUR"><Unit>1</Unit><ForexSelling>48.25</ForexSelling></Currency>
    </Tarih_Date>"""
    d, rates = parse_tcmb_xml(xml, {"USD", "EUR"})
    assert d == date(2026, 10, 2) and rates == {"USD": 41.5, "EUR": 48.25}
    assert parse_tcmb_xml(xml)[1]["JPY"] == 0.28


def test_statement_draft_dedupe_and_installments(db):
    c = make_card(db)
    # Spending previously entered from a receipt
    services.create_transaction(
        db, kind="expense", amount_cents=45050, currency="TRY", on=date(2026, 9, 20), category_id=1,
        user_id=None, payment_method="card", card_id=c.id, merchant="Migros",
    )
    db.commit()
    doc = Document(filename="e.pdf", stored_path="/x", mime="application/pdf")
    db.add(doc)
    db.commit()
    parsed = {
        "doc_type": "statement", "card_last4": "1234", "bank": "Garanti", "period_end": "2026-10-15",
        "due_date": "2026-10-25", "currency": "TRY", "totals": [{"currency": "TRY", "amount": 3000}], "min_payment": 600,
        "items": [
            {"date": "2026-09-21", "description": "MIGROS", "amount": 450.5, "currency": "TRY", "is_refund": False,
             "installment_no": None, "installment_count": None, "category": "Groceries"},
            {"date": "2026-10-01", "description": "TEKNOSA", "amount": 500, "currency": "TRY", "is_refund": False,
             "installment_no": 1, "installment_count": 4, "category": "Electronics"},
            {"date": "2026-08-01", "description": "IKEA", "amount": 300, "currency": "TRY", "is_refund": False,
             "installment_no": 3, "installment_count": 6, "category": "Home & Living"},
            {"date": "2026-10-02", "description": "REFUND", "amount": 100, "currency": "TRY", "is_refund": True,
             "installment_no": None, "installment_count": None, "category": None},
        ],
    }
    draft = services.build_draft(db, doc, parsed)
    assert draft["card_id"] == c.id
    migros, teknosa, ikea, refund = draft["rows"]
    assert migros["include"] is False and migros["duplicate_of"] is not None
    assert teknosa["amount"] == 2000 and teknosa["installment_count"] == 4
    assert ikea["mode"] == "installment_only"
    assert refund["include"] is False and refund["kind"] == "income"

    doc.status = "review"
    db.commit()
    created = services.commit_draft(db, doc, draft, user_id=None)
    assert created == 1  # only Teknosa
    plans = db.query(InstallmentPlan).order_by(InstallmentPlan.id).all()
    assert [(p.description, p.count, p.first_month) for p in plans] == [
        ("TEKNOSA", 4, date(2026, 10, 1)),
        ("IKEA", 6, date(2026, 8, 1)),
    ]
    db.refresh(c)
    assert c.debts == {"TRY": 300000}
    assert draft["statement"]["totals"] == {"TRY": 3000} and draft["statement"]["currency"] == "TRY"
    # One installment per month: Teknosa 1-4 (500 each), IKEA 3-6 from this statement on (300 each)
    tek = db.query(Transaction).filter(Transaction.merchant == "TEKNOSA").order_by(Transaction.date).all()
    assert [(t.date, t.amount_cents, t.installment_no) for t in tek] == [
        (date(2026, 10, 1), 50000, 1), (date(2026, 11, 1), 50000, 2), (date(2026, 12, 1), 50000, 3), (date(2027, 1, 1), 50000, 4)]
    ikea = db.query(Transaction).filter(Transaction.merchant == "IKEA").order_by(Transaction.date).all()
    assert [(t.date, t.amount_cents, t.installment_no) for t in ikea] == [
        (date(2026, 10, 1), 30000, 3), (date(2026, 11, 1), 30000, 4), (date(2026, 12, 1), 30000, 5), (date(2027, 1, 1), 30000, 6)]
    assert all(t.category_id for t in tek + ikea)
    assert db.query(Transaction).count() == 1 + 4 + 4


def test_normalize_result_fills_gaps():
    from app.ai_parser import normalize_result

    r = normalize_result(
        {
            "doc_type": "receipt",
            "currency": "TL",
            "items": [
                {"description": "Milk", "amount": "79.9", "installment_no": "1"},
                {"description": "empty", "amount": 0},
                "broken",
            ],
        }
    )
    assert r["currency"] == "TRY" and r["merchant"] is None and r["due_date"] is None
    assert r["items"] == [
        {"date": None, "description": "Milk", "amount": 79.9, "currency": "TRY", "is_refund": False,
         "installment_no": None, "installment_count": None, "category": None}
    ]


def test_pdf_pages_become_images():
    from pathlib import Path

    from app.ai_parser import document_images

    pdf = Path(__file__).parent / "fixtures" / "statement-example.pdf"
    assert len(document_images(pdf, "application/pdf")) == 2
    # The synthetic PDF has no text layer -> it is read as images
    from app.ai_parser import document_pages

    assert all("image" in p for p in document_pages(pdf, "application/pdf"))


def test_merge_pages_dedupes_across_pages_only():
    from app.ai_parser import _merge_pages

    p1 = {"doc_type": "statement", "card_last4": "1234", "items": [
        {"date": "2026-10-11", "description": "ZARA İSTİNYE İADE", "amount": 899},
        {"date": "2026-10-12", "description": "A", "amount": 50},
        {"date": "2026-10-12", "description": "B", "amount": 50},  # same amount on the same page: kept
    ]}
    p2 = {"doc_type": "statement", "due_date": "2026-10-25", "items": [
        {"date": "2026-10-11", "description": "ZARA ISTINYE IADE", "amount": -899},
        {"date": "2026-10-15", "description": "Fee", "amount": 100},
    ]}
    m = _merge_pages([p1, p2])
    assert [i["description"] for i in m["items"]] == ["ZARA İSTİNYE İADE", "A", "B", "Fee"]
    assert m["card_last4"] == "1234" and m["due_date"] == "2026-10-25"


def test_merchant_core_strips_payment_processors():
    from app.ai_parser import merchant_core

    assert merchant_core("PAYNKO /DGPARA OPETPAY") == "OPETPAY"
    assert merchant_core("IYZICO *AMAZON.COM.T") == "AMAZON.COM.T"
    assert merchant_core("PAYCELL/FATURAODEMEM") == "FATURAODEMEM"
    assert merchant_core("HEPSİPAY-HEP*HEPSİBURADA") == "HEPSİBURADA"
    assert merchant_core("MIGROS") == "MIGROS"


def test_receipt_items_reconciled_with_total():
    from app.ai_parser import normalize_result

    r = normalize_result({"doc_type": "receipt", "currency": "TRY", "total": 427.42, "items": [
        {"description": "Finish", "amount": 259.0}, {"description": "Arko", "amount": 104.5},
        {"description": "Chicken", "amount": 102.27}, {"description": "Discount", "amount": -54.5},
        {"description": "Bag", "amount": 0.25},
    ]})
    assert r["items"][3]["amount"] == -54.5  # receipt discount stays negative
    assert r["items"][-1] == {"date": None, "description": "Other items", "amount": 15.9, "currency": "TRY",
                              "is_refund": False, "installment_no": None, "installment_count": None, "category": None}
    assert round(sum(i["amount"] for i in r["items"]), 2) == 427.42


def test_card_match_requires_same_last4_when_known(db):
    from app.services import _match_card

    c = make_card(db)  # Garanti, last 4: 1234
    assert _match_card(db, "1234", "Garanti BBVA", None) == c.id
    assert _match_card(db, "**** 1014", "Garanti BBVA", None) is None  # same bank, different card
    assert _match_card(db, None, "Garanti BBVA", None) == c.id  # bank, if the last 4 could not be read


def test_statement_paid_toggle_restores_debt(db):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import CardStatement

    c = make_card(db)
    c.debts = {"TRY": 9494503}
    st = CardStatement(card_id=c.id, period_end=date(2026, 9, 26), due_date=date(2026, 10, 6), totals={"TRY": 9494503})
    db.add(st)
    db.commit()
    client = TestClient(app)
    client.post(f"/api/cards/statements/{st.id}/paid?paid=true")
    db.refresh(c)
    assert c.debts == {}
    client.post(f"/api/cards/statements/{st.id}/paid?paid=true")  # a double tap does not push the debt negative
    client.post(f"/api/cards/statements/{st.id}/paid?paid=false")
    db.refresh(c)
    assert c.debts == {"TRY": 9494503}


def test_parse_amount_shared_cases():
    """Same cases as the web UI (tests/amount_cases.json; the frontend vitest reads the same file)."""
    import json
    from pathlib import Path

    for text, expected in json.loads((Path(__file__).parent / "amount_cases.json").read_text()):
        assert finance.parse_amount(text) == expected, text


def test_last_business_day_recurring():
    from app import finance
    assert finance.last_business_day(2026, 10) == date(2026, 10, 30)  # Oct 31 is a Saturday
    assert finance.last_business_day(2026, 5) == date(2026, 5, 29)  # May 31 is a Sunday
    d = finance.advance_recurring(date(2026, 10, 30), "monthly", finance.LAST_BUSINESS_DAY)
    assert d == date(2026, 11, 30)
    assert finance.advance_recurring(d, "monthly", finance.LAST_BUSINESS_DAY) == date(2026, 12, 31)


def test_statement_number_format_detection():
    from app import statement_text as st
    assert st.detect_format(["LCWAIKIKI 2. Taksit 144.00 4x144.00\nPEGASUS 18,080.00\nBIM 923.50 2"]) == "us"
    assert st.detect_format(["MIGROS 1.234,56\nSHELL 850,00\n3.986,36x3=11.959,08"]) == "tr"
    assert st._amount("2,210.00", "us") == 2210.0 and st._amount("2.210,00") == 2210.0
    line = "IKEA ANKARA MAPA MOB 2. Taksit 2,210.00 1x2,210.00"
    m = st.NTH_INSTALLMENT.search(line)
    assert m.group("no") == "2" and st.REMAINING.search(line, m.end()).group("left") == "1"


def test_implausible_statement_dates():
    from app.services import _implausible_dates
    assert _implausible_dates(date(2026, 10, 5), date(2026, 10, 5), [date(2026, 9, 11)])  # due date = statement cut
    assert _implausible_dates(date(2026, 6, 19), date(2026, 9, 14), [])
    assert _implausible_dates(date(2026, 9, 1), date(2026, 9, 11), [date(2026, 9, 10)])  # transaction after the cut
    assert not _implausible_dates(date(2026, 9, 14), date(2026, 9, 24), [date(2026, 3, 1), date(2026, 9, 11)])


def test_installment_series_edit_and_delete(db):
    from fastapi.testclient import TestClient
    from app.main import app
    c = make_card(db)
    db.commit()
    client = TestClient(app)
    t = client.post("/api/transactions", json={"kind": "expense", "amount": 1200, "date": "2026-09-10", "payment_method": "card",
                                                "card_id": c.id, "merchant": "TV", "installment_count": 3}).json()
    rows = db.query(Transaction).order_by(Transaction.date).all()
    assert [(r.amount_cents, r.installment_no) for r in rows] == [(40000, 1), (40000, 2), (40000, 3)]
    # Edit applies to all months: category
    t["category_id"] = 1
    client.put(f"/api/transactions/{t['id']}", json=t)
    db.expire_all()
    assert {r.category_id for r in db.query(Transaction)} == {1}
    # Delete: the whole series
    client.delete(f"/api/transactions/{rows[1].id}")
    assert db.query(Transaction).count() == 0 and db.query(InstallmentPlan).count() == 0


def test_next_statement_does_not_duplicate_installments(db):
    c = make_card(db)
    db.commit()

    def statement(period_end, items):
        doc = Document(filename="e.pdf", stored_path="/x", mime="application/pdf", status="review")
        db.add(doc)
        db.commit()
        parsed = {"doc_type": "statement", "card_last4": "1234", "bank": "Garanti", "period_end": period_end,
                  "due_date": period_end[:8] + "25", "currency": "TRY", "totals": [{"currency": "TRY", "amount": 1}],
                  "min_payment": 0, "items": items}
        draft = services.build_draft(db, doc, parsed)
        return draft, services.commit_draft(db, doc, draft, user_id=None)

    def item(d, desc, amount, no, count):
        return {"date": d, "description": desc, "amount": amount, "currency": "TRY", "is_refund": False,
                "installment_no": no, "installment_count": count, "category": "Clothing"}

    # Purchase entered as a single payment (will show up as 1/3 on the statement later)
    services.create_transaction(db, kind="expense", amount_cents=300000, currency="TRY", on=date(2026, 11, 3),
                                category_id=1, user_id=None, payment_method="card", card_id=c.id, merchant="ZARA")
    db.commit()
    statement("2026-10-15", [item("2026-10-01", "LCWAIKIKI.COM", 1216.94, 1, 3), item("2026-06-14", "DEFACTO", 217.49, 4, 6)])
    n_after_oct = db.query(Transaction).count()
    # November: next months of the same installments (LCW with a few cents difference) + ZARA 1/3
    draft, created = statement("2026-11-15", [
        item("2026-10-01", "LCWAIKIKI.COM", 1216.50, 2, 3),
        item("2026-06-14", "DEFACTO", 217.49, 5, 6),
        item("2026-11-03", "ZARA", 1000.00, 1, 3),
    ])
    lcw, defacto, zara = draft["rows"]
    assert lcw["include"] is False and defacto["include"] is False  # already recorded
    assert db.query(InstallmentPlan).filter(InstallmentPlan.description.like("LCW%")).count() == 1
    assert db.query(InstallmentPlan).filter(InstallmentPlan.description == "DEFACTO").count() == 1
    # ZARA single-payment record was split into installments: 3 months x 1000, no new duplicate
    zara_rows = db.query(Transaction).filter(Transaction.merchant == "ZARA").order_by(Transaction.date).all()
    assert [(t.amount_cents, t.installment_no) for t in zara_rows] == [(100000, 1), (100000, 2), (100000, 3)]
    assert db.query(Transaction).count() == n_after_oct + 2



def test_installment_posting_date_bank_formats():
    from app.services import installment_posting_date as post
    # Garanti: posting date on the line (08-28, 3/3, cut 09-26) -> ordered June 28
    assert post(date(2026, 8, 28), 3, date(2026, 9, 26)) == date(2026, 8, 28)
    # VakifBank: purchase date on the line (06-14, 3/6, cut 09-13) -> posts on Aug 14
    assert post(date(2026, 6, 14), 3, date(2026, 9, 13)) == date(2026, 8, 14)
    # VakifBank sometimes prints the posting date (08-20, 3/4, cut 09-13)
    assert post(date(2026, 8, 20), 3, date(2026, 9, 13)) == date(2026, 8, 20)
