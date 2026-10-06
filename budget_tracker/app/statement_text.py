"""Rule-based extraction of transaction lines from credit card statement PDFs that have a text layer.

In the PDF statements banks send, each transaction line starts with a date and the amount sits in the
rightmost "Amount" ("Tutar") column. The column is detected from character coordinates, so bonus/points
figures on the same line, or lines with a value only in the bonus column, aren't mistaken for the amount.
Turkish bank statement wording is supported, with English equivalents where natural.
"""
import re
from dataclasses import dataclass, field
from datetime import date

import pypdfium2 as pdfium

MONTHS = {
    # German (Kontoauszüge)
    "januar": 1, "jän": 1, "februar": 2, "märz": 3, "mär": 3, "maerz": 3, "mai": 5, "juni": 6, "juli": 7,
    "okt": 10, "oktober": 10, "dez": 12, "dezember": 12,
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9, "ekim": 10, "kasım": 11,
    "kasim": 11, "aralık": 12, "aralik": 12,
    # English
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

DATE_AT_START = re.compile(
    r"^\s*(\d{1,2})[ ./\-]((?:\d{1,2})|[A-Za-zÇĞİÖŞÜçğıöşü]+)[ ./\-]((?:19|20)?\d{2})\b\s*(.*)$"
)
# Amount: Turkish 1.234,56 (most banks) or US format 1,234.56 (e.g. VakıfBank); may have a leading/trailing sign
AMOUNT_TR = re.compile(r"([+-]?)(\d{1,3}(?:\.\d{3})*,\d{2})([+-]?)")
AMOUNT_US = re.compile(r"([+-]?)(\d{1,3}(?:,\d{3})*\.\d{2})([+-]?)")
AMOUNT = AMOUNT_TR  # backward compatibility
# Distinctive samples for detecting the document's number format
_TR_HINT = re.compile(r"\d\.\d{3},\d{2}\b|(?<![\d.,])\d{1,3},\d{2}(?![\d,])")
_US_HINT = re.compile(r"\d,\d{3}\.\d{2}\b|(?<![\d.,])\d{1,3}\.\d{2}(?![\d.])")
# VakıfBank: "2. Taksit 144.00 4x144.00" (4 remaining) or "3.Taksit 392.12 Son Taksit" (last installment)
NTH_INSTALLMENT = re.compile(r"(?P<no>\d{1,2})\s*\.\s*Taksit", re.I)
REMAINING = re.compile(r"(?P<left>\d{1,2})\s*x\s*[\d.,]+|(?P<last>Son\s+Taksit|Last\s+Installment)", re.I)
INSTALLMENT_PATTERNS = [
    # Garanti: 1.328,00x3=3.984,00 3.Taksit
    re.compile(r"x\s*(?P<count>\d{1,2})\s*=\s*[\d.,]+\s*(?P<no>\d{1,2})\s*\.?\s*Taksit", re.I),
    # 3/6 Taksit, Taksit 3/6, (3/6)
    re.compile(r"(?P<no>\d{1,2})\s*/\s*(?P<count>\d{1,2})\s*Taks", re.I),
    re.compile(r"Taksit\s*:?\s*(?P<no>\d{1,2})\s*/\s*(?P<count>\d{1,2})", re.I),
    # English: "3/6 Installment", "Installment 3/6", "Installment 3 of 6"
    re.compile(r"(?P<no>\d{1,2})\s*/\s*(?P<count>\d{1,2})\s*Install", re.I),
    re.compile(r"Installment\s*:?\s*(?P<no>\d{1,2})\s*(?:/|of)\s*(?P<count>\d{1,2})", re.I),
]
# Non-spending lines: payments, carried-over balances, points promotions
NON_SPENDING = re.compile(
    r"teşekkür|tesekkur|ödemeniz|odemeniz|hesaba ödeme|otomatik ödeme|devir|önceki dönem|onceki donem|"
    r"(bonus|puan|chip|parafpara|maxipuan|worldpuan).*kampanya|kampanya.*(bonus|puan)|bedava alışveriş|"
    r"bol bonuslu|hesaptan ?aktar[ıi]m|"
    r"thank you|payment received|autopay|automatic payment|previous balance",
    re.I,
)
REFUND_WORDS = re.compile(r"iade|iptal|refund|reversal", re.I)
# Portion paid with points (VakıfBank "Puan Kullanımı"): the bank doesn't add it to the period total; not spending
POINTS_USED = re.compile(r"puan\s+kullan|points?\s+redeem|points?\s+redemption", re.I)
# Column detection tolerance (PDF units, ~1/72 inch)
COLUMN_TOLERANCE = 25


@dataclass
class Line:
    text: str
    # (text, right edge x) for each amount on the line
    amounts: list[tuple[re.Match, float]] = field(default_factory=list)


def detect_format(texts: list[str]) -> str:
    """"tr" (1.234,56) ya da "us" (1,234.56)."""
    joined = "\n".join(texts)
    return "us" if len(_US_HINT.findall(joined)) > len(_TR_HINT.findall(joined)) else "tr"


def _page_lines(page, amount_re: re.Pattern = AMOUNT_TR) -> list[Line]:
    tp = page.get_textpage()
    lines: list[Line] = []
    chars: list[str] = []
    rights: list[float] = []

    def flush():
        if chars:
            text = "".join(chars)
            line = Line(text=text)
            for m in amount_re.finditer(text):
                # "4x144.00" remaining installments / "(1.123,20)" installment total: not an amount
                if m.start() > 0 and text[m.start() - 1] in "xX(":
                    continue
                # Right edge of the amount's last digit
                line.amounts.append((m, rights[m.end(2) - 1]))
            lines.append(line)
        chars.clear()
        rights.clear()

    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if ch in ("\r", "\n"):
            flush()
            continue
        chars.append(ch)
        try:
            rights.append(tp.get_charbox(i)[2])
        except Exception:
            rights.append(rights[-1] if rights else 0.0)
    flush()
    return lines


def _amount_column(candidates) -> float:
    """Right edge of the amount column: where the amount is aligned on most transaction lines.

    The rightmost column isn't always the amount (MaxiPuan, Bankkart Lira, bonus); most lines carry a value
    in the amount column, while only some have one in the points column. On a tie the right one wins.
    """
    votes: dict[float, int] = {}
    for line, _, _ in candidates:
        for x in {round(right / COLUMN_TOLERANCE) for _, right in line.amounts}:
            votes[x] = votes.get(x, 0) + 1
    best = max(votes, key=lambda x: (votes[x], x))
    # Rightmost edge within the same cluster (right-aligned column)
    return max(r for line, _, _ in candidates for _, r in line.amounts if round(r / COLUMN_TOLERANCE) == best)


def _parse_date(day: str, month: str, year: str) -> date | None:
    try:
        m = int(month) if month.isdigit() else MONTHS.get(month.lower().replace("i̇", "i"))
        y = int(year) + (2000 if len(year) == 2 else 0)
        return date(y, m, int(day)) if m else None
    except ValueError:
        return None


def _amount(s: str, fmt: str = "tr") -> float:
    if fmt == "us":
        return float(s.replace(",", ""))
    return float(s.replace(".", "").replace(",", "."))


def _clean_description(rest: str, first_amount_start: int | None, inst: re.Match | None) -> str:
    cut = len(rest)
    if first_amount_start is not None:
        cut = min(cut, first_amount_start)
    if inst is not None:
        cut = min(cut, inst.start())
    desc = rest[:cut]
    # Also drop the amount at the start of an installment expression like "3.986,36x3=..."
    desc = re.sub(r"\s+[\d.,]+\s*$", "", desc)
    return re.sub(r"\s{2,}", " ", desc).strip(" -*")


def extract_transactions(path: str) -> list[dict]:
    """Transaction lines in the PDF that start with a date. Each item is in ai_parser's item format.

    Lines not in the amount column (e.g. with a figure only in the bonus column) are flagged with "column_mismatch".
    """
    pdf = pdfium.PdfDocument(path)
    fmt = detect_format([pdf[i].get_textpage().get_text_bounded() for i in range(len(pdf))])
    amount_re = AMOUNT_US if fmt == "us" else AMOUNT_TR
    items: list[dict] = []
    for page_index in range(len(pdf)):
        candidates = []
        for line in _page_lines(pdf[page_index], amount_re):
            m = DATE_AT_START.match(line.text)
            if not m or not line.amounts:
                continue
            on = _parse_date(m.group(1), m.group(2), m.group(3))
            if on is None:
                continue
            candidates.append((line, on, m))
        if not candidates:
            continue
        amount_column = _amount_column(candidates)
        for line, on, m in candidates:
            rest_offset = m.start(4)
            rest = line.text[rest_offset:]
            # The line's amount: the one aligned with the amount column; otherwise the last amount (flagged as off-column)
            last_match, last_right = min(line.amounts, key=lambda a: abs(a[1] - amount_column))
            if abs(last_right - amount_column) > COLUMN_TOLERANCE:
                last_match, last_right = line.amounts[-1]
            amount = _amount(last_match.group(2), fmt)
            if amount == 0:
                continue  # points promotion info lines (0,00)
            inst = None
            no = count = None
            for pat in INSTALLMENT_PATTERNS:
                inst = pat.search(rest)
                if inst:
                    no, count = int(inst.group("no")), int(inst.group("count"))
                    break
            if inst is None and (nth := NTH_INSTALLMENT.search(rest)):
                left = REMAINING.search(rest, nth.end())
                no = int(nth.group("no"))
                if left and left.group("left"):
                    count = no + int(left.group("left"))
                elif left:
                    count = no
                inst = nth
            first_amount = next((a.start() - rest_offset for a, _ in line.amounts if a.start() >= rest_offset), None)
            description = _clean_description(rest, first_amount, inst)
            signed_credit = last_match.group(1) in ("+", "-") or last_match.group(3) in ("+", "-")
            points = bool(POINTS_USED.search(description))
            if not description and items and not signed_credit:
                description = items[-1]["description"]  # continuation line with empty description (same merchant)
            items.append(
                {
                    "date": on.isoformat(),
                    "description": description,
                    "amount": amount,
                    "currency": "TRY",
                    "is_refund": signed_credit or bool(REFUND_WORDS.search(description)),
                    "installment_no": no if count and count > 1 else None,
                    "installment_count": count if count and count > 1 else None,
                    "category": None,
                    # A credit without description (e.g. payment rounding "+0,44") is not spending
                    "non_spending": bool(NON_SPENDING.search(description)) or points or (signed_credit and not description),
                    "column_mismatch": last_right < amount_column - COLUMN_TOLERANCE,
                }
            )
    return items


def has_text_layer(path: str) -> bool:
    pdf = pdfium.PdfDocument(path)
    total = sum(len(pdf[i].get_textpage().get_text_range().strip()) for i in range(len(pdf)))
    return total >= 200 * len(pdf)


def page_texts(path: str) -> list[str]:
    pdf = pdfium.PdfDocument(path)
    return [pdf[i].get_textpage().get_text_range().replace("\r", "") for i in range(len(pdf))]
