"""Reads receipts and credit card statements with a local vision-language model (Ollama)."""
import base64
import io
import json
import logging
import re
import unicodedata
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from . import prefs, statement_text
from .config import settings

log = logging.getLogger(__name__)

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

# The number of image tokens determines speed on a local model
MAX_IMAGE_SIDE = 1600
MAX_PAGES = 8


def _nullable(t: dict) -> dict:
    return {"anyOf": [t, {"type": "null"}]}


STR = {"type": "string"}
NUM = {"type": "number"}
INT = {"type": "integer"}
DATE = {"type": "string", "format": "date"}
CUR = {"type": "string", "enum": prefs.SUPPORTED_CURRENCIES}
TOTAL = {
    "type": "object",
    "properties": {"currency": CUR, "amount": NUM},
    "required": ["currency", "amount"],
    "additionalProperties": False,
}
# Currency symbols/abbreviations a model may return instead of an ISO code
CURRENCY_ALIASES = {"TL": "TRY", "YTL": "TRY", "₺": "TRY", "$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY",
                    "FR": "CHF", "ZL": "PLN", "ZŁ": "PLN", "KR": "SEK", "R$": "BRL", "C$": "CAD", "A$": "AUD"}


def currency_code(value, default: str | None = None) -> str:
    """Normalizes a currency read from a document to a supported ISO code (default: base currency)."""
    code = str(value or "").strip().upper()
    code = CURRENCY_ALIASES.get(code, code)
    return code if code in prefs.SUPPORTED_CURRENCIES else (default or prefs.base())

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "date": _nullable(DATE),
        "description": STR,
        "amount": NUM,
        "currency": CUR,
        "is_refund": {"type": "boolean"},
        "installment_no": _nullable(INT),
        "installment_count": _nullable(INT),
        "category": _nullable(STR),
    },
    "required": [
        "date",
        "description",
        "amount",
        "currency",
        "is_refund",
        "installment_no",
        "installment_count",
        "category",
    ],
    "additionalProperties": False,
}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["receipt", "statement", "other"]},
        "merchant": _nullable(STR),
        "date": _nullable(DATE),
        "currency": CUR,
        "total": _nullable(NUM),
        "payment_method": _nullable({"type": "string", "enum": ["cash", "card", "bank"]}),
        "card_last4": _nullable(STR),
        "bank": _nullable(STR),
        "category": _nullable(STR),
        "period_end": _nullable(DATE),
        "due_date": _nullable(DATE),
        "totals": {"type": "array", "items": TOTAL},
        "min_payment": _nullable(NUM),
        "period_spending": _nullable(NUM),
        "items": {"type": "array", "items": ITEM_SCHEMA},
        "notes": _nullable(STR),
    },
    "required": [
        "doc_type",
        "merchant",
        "date",
        "currency",
        "total",
        "payment_method",
        "card_last4",
        "bank",
        "category",
        "period_end",
        "due_date",
        "totals",
        "min_payment",
        "period_spending",
        "items",
        "notes",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are reading documents for a household budget app. The document is either a shopping receipt/invoice or a credit card statement. Documents may be in Turkish or English.

General rules:
- Give amounts as numbers; European/Turkish format "1.234,56" = 1234.56, English format "1,234.56" = 1234.56. Currencies are ISO codes; infer them from the symbol/text (€ → EUR, $ → USD, £ → GBP, ₺/TL → TRY, zł → PLN, Fr. → CHF); if not stated, {base}.
- Give dates in YYYY-MM-DD format. Use null for fields you can't read; don't make anything up.
- For the "category" field pick only one from this list: {categories}.

Receipt (doc_type="receipt"):
- merchant: short, readable business name (e.g. "Migros", "Shell"). total: grand total paid (tax included).
- items: product lines on the receipt (description, amount = line total, date = null, installment fields null).
- Quantity lines like "1,033Kg X 99,00" or "2 X 15,00" are not separate products; they belong to the product below/above them, don't add them to items separately.
- Discount/promotion lines (amounts ending in "-", "İNDİRİM", "KAMPANYA", "DISCOUNT") are written as separate lines with a NEGATIVE amount (e.g. -149.00).
- The sum of product lines (including discounts) must equal the receipt TOTAL; don't put tax (KDV, TOPKDV), subtotal lines in items.
- If the receipt shows the card's last 4 digits or installment ("taksit") info, give card_last4 and installment_count on the first line; infer payment_method from cash/card info.

Credit card statement (doc_type="statement"):
- bank, card_last4, period_end (statement closing date), due_date (payment due date), currency (the statement's main currency), totals (statement balance: one entry per currency shown, e.g. [{{"currency": "EUR", "amount": 1234.56}}]), min_payment (minimum payment, in the main currency).
- period_spending: if the statement shows a total for purchases this period ("New purchases", "Dönem Harcamaları"), that amount (main currency), otherwise null.
- items: EVERY purchase/refund line on the statement. description: the merchant name on the line. amount: the amount on that line that counts toward this period's balance (positive). For refund/cancellation/payment lines is_refund=true.
- If a line contains info like "3/6 taksit", "Taksit 3/6" or "installment 3 of 6", then installment_no=3, installment_count=6; otherwise null.
- Format "1.328,00x3=3.984,00 3.Taksit": monthly installment 1.328,00, 3 installments, this is the 3rd → amount=1328.00, installment_count=3, installment_no=3.
- The amount is taken from the column headed "Tutar", "TL Tutar", "Tutar (TL)" or "Amount". Points columns like "Bankkart Lira", "Bonus", "Puan", "WorldPuan", "Chip-Para", "MaxiPuan" and the "USD Tutar" column are NOT the amount. Without a column header, the transaction amount is usually the rightmost of several amounts on a line.
- Lines with an amount of 0,00 (e.g. "Bankkart Lira ile Ödeme", "Kampanya Kazanımı", paid with points) are not purchases; don't add them to items.
- Read each line's date from its own line; dates are day.month.year ("19.08.2026" = 2026-08-19). Don't confuse the statement closing date with the due date.
- Bonus/points/chip-para promotion lines ("BONUS ... KAMPANYASI", "BONUS BEDAVA ALIŞVERİŞ", points earned) and "ÖNCEKİ DÖNEMDEN DEVİR" (carried over from previous period) are not purchases; don't add them to items.
- Don't add previous balance, total payments, interest/fee summary lines to items; but do add transaction lines such as card fees, interest, BSMV with the "Bank & Card Fees" category.
- Don't add card account payments (payments made toward the balance) to items.

If the document is neither of these, doc_type="other" and items is an empty list."""


KIND_HINTS = {
    "receipt": "The user indicated this document is a receipt.",
    "statement": "The user indicated this document is a credit card statement.",
}


class ParseError(Exception):
    pass


def _jpeg_b64(img: Image.Image) -> str:
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if w < 1000:
        # Small images like phone screenshots: upscale so text is legible (long side at most 2600)
        f = min(2.0, 2600 / max(h, 1))
        if f > 1.1:
            img = img.resize((round(w * f), round(h * f)), Image.LANCZOS)
        img.thumbnail((2600, 2600))
    else:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode()


# Date at the start of the line (e.g. "14 Eylül 2026", "14.09.2026", "14 Sep 2026") and an amount on the line → transaction line
_TX_LINE = re.compile(
    r"^\s*\d{1,2}[ ./-](?:\d{1,2}|[A-Za-zÇĞİÖŞÜçğıöşü]+)[ ./-](?:19|20)?\d{2}\b.*\d+[.,]\d{2}", re.MULTILINE
)


def document_pages(path: Path, mime: str) -> list[dict]:
    """Splits the document into pages. Returns text for PDFs with a text layer, images otherwise.

    Each item is {"text": str} or {"image": base64-jpeg}. Text pages without transaction lines (legal notices) are skipped.
    """
    if mime != "application/pdf":
        return [{"image": _jpeg_b64(ImageOps.exif_transpose(Image.open(path)))}]
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    if len(pdf) > MAX_PAGES:
        raise ParseError(f"PDF has {len(pdf)} pages; at most {MAX_PAGES} pages are supported.")
    texts = [pdf[i].get_textpage().get_text_range().replace("\r", "") for i in range(len(pdf))]
    if sum(len(t.strip()) for t in texts) >= 200 * len(pdf):
        pages = [{"text": t} for t in texts if _TX_LINE.search(t)]
        return pages or [{"text": texts[0]}]
    # Scanned PDF: render pages to images
    return [{"image": _jpeg_b64(pdf[i].render(scale=2).to_pil())} for i in range(len(pdf))]


def document_images(path: Path, mime: str) -> list[str]:
    """Converts the image or each PDF page to base64 JPEG."""
    if mime == "application/pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        return [_jpeg_b64(pdf[i].render(scale=2).to_pil()) for i in range(len(pdf))]
    return [_jpeg_b64(ImageOps.exif_transpose(Image.open(path)))]


# Non-spending summary, payment and points-promotion lines on statements (small models may list these too).
# Turkish patterns (unaccented) plus English equivalents.
_NON_SPENDING = re.compile(
    r"onceki donem|donem borcu|son ekstre borcu|devir|odeme\s*-?\s*tesekkur|tesekkur ederiz|hesaba odeme|"
    r"otomatik odeme|kart odemesi|borc odeme|^odeme$|asgari odeme|toplam borc|"
    r"(bonus|puan|chip|parafpara|maxipuan|worldpuan).*kampanya|kampanya.*(bonus|puan)|bedava alisveris|bol bonuslu|"
    r"bankkart lira ile odeme|kampanya kazanim|puan(la| ile) odeme|puan kullanim|"
    r"previous balance|payment\s*-?\s*thank you|thank you for your payment|autopay|automatic payment|"
    r"minimum payment|total balance|points? redemption"
)


def _plain(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower().replace("ı", "i").replace("İ", "i"))
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def normalize_result(r: dict) -> dict:
    """Fills in responses that don't fully match the schema with safe defaults."""
    if r.get("doc_type") not in ("receipt", "statement", "other"):
        r["doc_type"] = "other"
    for key in RESULT_SCHEMA["required"]:
        r.setdefault(key, [] if key == "items" else None)
    r["currency"] = currency_code(r.get("currency"))
    totals = []
    for t in r.get("totals") or []:
        if not isinstance(t, dict):
            continue
        try:
            amount = abs(float(t.get("amount") or 0))
        except (TypeError, ValueError):
            continue
        if amount:
            totals.append({"currency": currency_code(t.get("currency"), r["currency"]), "amount": amount})
    r["totals"] = totals
    items = []
    for it in r.get("items") or []:
        if not isinstance(it, dict):
            continue
        try:
            amount = float(it.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        # Discount lines stay negative on receipts; on statements the sign is carried by is_refund
        if r["doc_type"] != "receipt":
            amount = abs(amount)
        if amount == 0:
            continue
        if r["doc_type"] == "statement" and _NON_SPENDING.search(_plain(str(it.get("description") or ""))):
            continue
        no, count = it.get("installment_no"), it.get("installment_count")
        items.append(
            {
                "date": it.get("date"),
                "description": str(it.get("description") or "").strip(),
                "amount": amount,
                "currency": currency_code(it.get("currency"), r["currency"]),
                "is_refund": bool(it.get("is_refund")),
                "installment_no": no if isinstance(no, int) else None,
                "installment_count": count if isinstance(count, int) else None,
                "category": it.get("category"),
            }
        )
    r["items"] = items
    # If receipt items don't add up to the total (unread discount/line), close the gap with a single line
    if r["doc_type"] == "receipt" and items and isinstance(r.get("total"), (int, float)):
        diff = round(float(r["total"]) - sum(i["amount"] for i in items), 2)
        if abs(diff) >= 0.01:
            items.append(
                {
                    "date": None,
                    "description": "Discounts" if diff < 0 else "Other items",
                    "amount": diff,
                    "currency": r["currency"],
                    "is_refund": False,
                    "installment_no": None,
                    "installment_count": None,
                    "category": None,
                }
            )
    return r


def _chat(page: dict, system: str, prompt: str, schema: dict | None = None) -> dict:
    body = {
        "model": settings.ollama_model,
        "stream": False,
        "think": False,
        "format": schema or RESULT_SCHEMA,
        "options": {"temperature": 0, "num_ctx": 16384},
        "keep_alive": "10m",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt, "images": [page["image"]]}
            if "image" in page
            else {"role": "user", "content": f"{prompt}\n\nDocument text:\n```\n{page['text']}\n```"},
        ],
    }
    url = f"{settings.ollama_url.rstrip('/')}/api/chat"
    try:
        r = httpx.post(url, json=body, timeout=900)
    except httpx.ConnectError:
        raise ParseError(f"Couldn't connect to Ollama ({settings.ollama_url}). Is the computer on and on the network?")
    except httpx.TimeoutException:
        raise ParseError("The local model timed out.")
    if r.status_code == 404:
        raise ParseError(f"Model '{settings.ollama_model}' isn't available in Ollama (ollama pull {settings.ollama_model}).")
    if r.status_code != 200:
        raise ParseError(f"Ollama error ({r.status_code}): {r.text[:200]}")
    data = r.json()
    log.info("Model response (%s): %.1f s", settings.ollama_model, (data.get("total_duration") or 0) / 1e9)
    try:
        return json.loads((data.get("message") or {}).get("content", ""))
    except json.JSONDecodeError:
        raise ParseError("The model didn't produce a valid response; please try again.")


def _merge_pages(pages: list[dict]) -> dict:
    """Merges a statement read page by page into one result: header fields from the first non-empty value, items in order."""
    merged: dict = {"items": []}
    for key in RESULT_SCHEMA["required"]:
        if key != "items":
            merged[key] = next((p.get(key) for p in pages if p.get(key) not in (None, "", [])), None)
    merged["doc_type"] = "statement" if any(p.get("doc_type") == "statement" for p in pages) else pages[0].get("doc_type")
    # The model may return the same line on two pages written differently: skip if the same date+amount was on earlier pages
    seen: set[tuple] = set()
    for p in pages:
        page_keys = set()
        for it in p.get("items") or []:
            try:
                key = (it.get("date"), round(abs(float(it.get("amount") or 0)), 2))
            except (TypeError, ValueError):
                continue
            if key in seen:
                continue
            page_keys.add(key)
            merged["items"].append(it)
        seen |= page_keys
    return merged


HEADER_SCHEMA = {
    "type": "object",
    "properties": {k: v for k, v in RESULT_SCHEMA["properties"].items() if k != "items"},
    "required": [k for k in RESULT_SCHEMA["required"] if k != "items"],
    "additionalProperties": False,
}
SUMMARY_PAGE = re.compile(
    r"dönem harcama|donem harcama|hesap özeti|ekstre özeti|son ödeme|account summary|statement summary|payment due", re.I
)


CATEGORY_HINTS = """Hints:
- Payment processor prefixes are not the merchant, look at the name after them: IYZICO, PAYTR, PAYCELL, PAYNKO, HEPSIPAY, PARAM, NKOLAY, DGPARA, SQ *, PAYPAL *.
- Gas stations/fuel/OPET/Shell/BP/Total/PO, toll top-ups (HGS/OGS), parking → Transport & Fuel
- A101, BİM, ŞOK, Migros, CarrefourSA, Walmart, Costco, food, supermarket → Groceries
- Restaurants, cafes, fast food, bakeries, Yemeksepeti, Getir Yemek, Uber Eats, DoorDash, TrendyolGo → Restaurants & Cafes
- Netflix, Spotify, Disney, YouTube, Apple.com/bill, Google One, Amazon Prime, iCloud → Subscriptions
- Marketplaces like Hepsiburada, Trendyol, Amazon, N11 (when it's unclear what was bought) → Other Expense
- Insurance, tax office, government fees, vehicle tax (MTV) → Taxes & Fees
- Electricity, water, natural gas, phone/internet bills → Utilities
- Pharmacy, hospital, doctor, optician → Health
- Bus tickets (Obilet), flights, hotels, airlines (THY, Pegasus), duty free → Travel
- Clothing stores (DeFacto, LC Waikiki, Zara, Koton) → Clothing
- Home appliances (BSH, Arçelik, Vestel), furniture, IKEA → Home & Living
- "Bank & Card Fees" is only for bank fees, interest, BSMV, KKDF, card annual fee lines.
- If unsure → Other Expense"""


# Payment processor prefixes: "IYZICO *AMAZON", "PAYNKO /DGPARA OPETPAY", "PAYCELL/FATURAODEMEM" → actual merchant
_PROCESSOR_PREFIX = re.compile(
    r"^(?:(?:iyzico|paytr(?: odeme)?|paycell|paynko|hepsipay-hep|hepsipay|param|nkolay-kurum|nkolay|dgpara|"
    r"s/pttbank|ininal|papara|sipay)\s*[*/]?\s*)+",
    re.I,
)
_FEE_WORDS = re.compile(r"ücret|ucret|faiz|bsmv|kkdf|komisyon|aidat|masraf|\bfees?\b|interest|finance charge", re.I)


def merchant_core(description: str) -> str:
    core = _PROCESSOR_PREFIX.sub("", description).strip(" */-")
    return core or description


def _categorize(descriptions: list[str], category_names: list[str]) -> dict[str, str]:
    """Categorizes merchant names in a single call."""
    if not descriptions:
        return {}
    cores = {d: merchant_core(d) for d in descriptions}
    unique_cores = sorted(set(cores.values()))
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "category": {"type": "string", "enum": category_names}},
                    "required": ["name", "category"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    system = (
        "You are sorting merchant names from credit card statements into spending categories. "
        f"Categories: {', '.join(category_names)}.\n{CATEGORY_HINTS}"
    )
    listing = "\n".join(unique_cores)
    out = _chat(
        {"text": listing},
        system,
        "For each merchant name below, return JSON containing its name (name, verbatim) and its category (category).",
        schema,
    )
    by_core = {}
    for row in out.get("items") or []:
        by_core[str(row.get("name", "")).strip().lower()] = row.get("category")
    result = {}
    for d, core in cores.items():
        cat = by_core.get(core.lower())
        if cat == "Bank & Card Fees" and not _FEE_WORDS.search(d):
            cat = "Other Expense"
        if cat:
            result[d] = cat
    return result


def _parse_text_pdf(path: Path, kind_hint: str, system: str, category_names: list[str]) -> dict | None:
    """PDF with a text layer. If it's a statement, lines are extracted rule-based; otherwise None (image/text LLM path)."""
    texts = statement_text.page_texts(str(path))
    header_pages = [texts[0]] + [t for t in texts[1:] if SUMMARY_PAGE.search(t)][:2]
    header_text = "\n\n--- next page ---\n\n".join(t[:6000] for t in header_pages)
    hint = KIND_HINTS.get(kind_hint, "Determine the document type yourself.")
    header = _chat(
        {"text": header_text},
        system,
        f"{hint} Extract only the header/summary information of this document (transaction lines will be read separately).",
        HEADER_SCHEMA,
    )
    if header.get("doc_type") != "statement":
        if len(texts) == 1:
            # Single-page text PDF (e.g. e-invoice): pass the text straight to the model
            return _chat({"text": texts[0]}, system, f"{hint} Read the document and return only the requested JSON.")
        return None
    items = []
    for it in statement_text.extract_transactions(str(path)):
        if it.pop("non_spending"):
            continue
        if it.pop("column_mismatch"):
            # Not in the amount column (e.g. bonus column): not a purchase
            log.info("Skipped line not in the amount column: %s %s", it["description"], it["amount"])
            continue
        items.append(it)
    cats = _categorize(sorted({i["description"] for i in items if not i["is_refund"]}), category_names)
    for it in items:
        it["category"] = cats.get(it["description"])
    # Rule-based lines: the total can't be checked against the statement balance (it includes previous balance/payments)
    return {**header, "doc_type": "statement", "items": items, "source": "text"}


def parse_document(path: Path, mime: str, kind_hint: str, category_names: list[str]) -> dict:
    if not settings.ollama_url:
        raise ParseError("Ollama URL is not set (add-on configuration: ollama_url).")
    hint = KIND_HINTS.get(kind_hint, "Determine the document type yourself.")
    system = SYSTEM_PROMPT.format(categories=", ".join(category_names), base=prefs.base())
    if mime == "application/pdf" and statement_text.has_text_layer(str(path)):
        result = _parse_text_pdf(path, kind_hint, system, category_names)
        if result is not None:
            log.info("Document read (text layer): %s, %d items", path.name, len(result["items"]))
            return normalize_result(result)
    pages = document_pages(path, mime)
    if len(pages) == 1:
        result = _chat(pages[0], system, f"{hint} Read the document and return only the requested JSON.")
    else:
        # Small models may skip lines across many pages at once; each page is read separately
        results = []
        for i, page in enumerate(pages, 1):
            prompt = (
                f"{hint} This is page {i} of a {len(pages)}-page document. Extract only the information on this page and "
                "ALL transaction lines on this page, without omissions. Return only the requested JSON."
            )
            results.append(_chat(page, system, prompt))
        result = _merge_pages(results)
    log.info("Document read: %s (%d pages, %d items)", path.name, len(pages), len(result.get("items") or []))
    return normalize_result(result)
