"""Rule-based extraction from OCR tokens — used when no AI key is configured, in tests, and as a
cross-check signal. Produces the same shape as the Claude extractor."""

from __future__ import annotations

import re
from typing import Any

from lekha.ai.extract import HEADER_FIELDS
from lekha.engines import gstin as G
from lekha.engines.states import parse_place_of_supply
from lekha.ocr.pages import Page, Token

# Strict layout and no word boundaries: text such as "GSTIN27ABCDE1234F1Z5Email" loses its spaces in OCR.
GSTIN_RE = re.compile(r"(?<![0-9])\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]")
AMOUNT_RE = re.compile(r"-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?")
DATE_RE = re.compile(
    r"\b(\d{1,2}[-/.](?:\d{1,2}|[A-Za-z]{3})[-/.]\d{2,4}|\d{4}-\d{2}-\d{2})\b"
)
LABELS = {
    "number": r"(?:invoice|inv|bill|document|doc)\.?\s*(?:no|number|#)\.?",
    "date": r"(?:invoice|inv|bill|document|doc)?\s*dated?\b",
    "taxable_value": r"(?:total\s+)?taxable\s*(?:value|amount|amt)?",
    "igst": r"\bigst\b",
    "cgst": r"\bcgst\b",
    "sgst": r"\b(?:sgst|utgst)\b",
    "cess": r"\bcess\b",
    "round_off": r"round(?:ing)?\s*off",
    "total": r"(?:grand\s+total|invoice\s+total|total\s+(?:invoice\s+)?(?:value|amount)|net\s+amount|amount\s+payable)",
    "place_of_supply": r"place\s+of\s+supply",
    "irn": r"\birn\b",
}


def _field(value: Any, tokens: list[Token] | None = None, certainty: str = "medium") -> dict[str, Any]:
    return {"value": None if value is None else str(value), "token_ids": [t.id for t in tokens or []],
            "certainty": certainty if value is not None else "low"}


def _right_or_below(tokens: list[Token], anchor: Token) -> list[Token]:
    ay = (anchor.box[1] + anchor.box[3]) / 2
    same_row = [t for t in tokens if t is not anchor and abs((t.box[1] + t.box[3]) / 2 - ay) < 0.012
                and t.box[0] >= anchor.box[0]]
    same_row.sort(key=lambda t: t.box[0])
    below = [t for t in tokens if 0 < t.box[1] - anchor.box[3] < 0.03 and abs(t.box[0] - anchor.box[0]) < 0.12]
    below.sort(key=lambda t: t.box[1])
    return same_row + below


def _after_label(text: str, pattern: str) -> str | None:
    m = re.search(pattern + r"\s*[:\-]?\s*(.+)$", text, re.I)
    return m.group(1).strip() if m and m.group(1).strip() else None


def _find(tokens: list[Token], key: str, value_re: re.Pattern[str], last: bool = False) -> tuple[str | None, list[Token]]:
    pattern = LABELS[key]
    hits = [t for t in tokens if re.search(pattern, t.text, re.I)]
    if last:
        hits = list(reversed(hits))
    for t in hits:
        tail = _after_label(t.text, pattern)
        if tail:
            m = value_re.search(tail.replace(" ", "") if key == "number" else tail)
            if m:
                return m.group(0), [t]
        for nt in _right_or_below(tokens, t)[:3]:
            m = value_re.search(nt.text)
            if m:
                return m.group(0), [t, nt]
    return None, []


def extract_offline(pages: list[Page], client_gstins: set[str] | None = None) -> dict[str, Any]:
    tokens = [t for p in pages for t in p.tokens]
    fields: dict[str, Any] = {f: _field(None) for f in HEADER_FIELDS}

    # GSTINs: buyer is the one that is ours or appears after "bill to"/"buyer"; supplier is the other.
    found: list[tuple[str, Token]] = []
    for t in tokens:
        for m in GSTIN_RE.finditer(t.text.upper().replace(" ", "")):
            found.append((m.group(0), t))
    seen: list[tuple[str, Token]] = []
    for g, t in found:
        if g not in [s[0] for s in seen]:
            seen.append((g, t))
    buyer = next(((g, t) for g, t in seen if client_gstins and g in client_gstins), None)
    if not buyer:
        bill_to = next((t for t in tokens if re.search(r"bill(?:ed)?\s*to|buyer|recipient|consignee", t.text, re.I)), None)
        if bill_to:
            buyer = next(((g, t) for g, t in seen if t.box[1] >= bill_to.box[1]), None)
    supplier = next(((g, t) for g, t in seen if not buyer or g != buyer[0]), None)
    if supplier:
        fields["supplier_gstin"] = _field(supplier[0], [supplier[1]], "high" if G.is_valid(supplier[0]) else "low")
        name_tok = next((t for t in tokens if t.box[1] < supplier[1].box[1] and len(t.text) > 4
                         and not re.search(r"invoice|gstin|tax", t.text, re.I)), None)
        if name_tok:
            fields["supplier_name"] = _field(name_tok.text, [name_tok], "low")
    if buyer:
        fields["buyer_gstin"] = _field(buyer[0], [buyer[1]], "high" if G.is_valid(buyer[0]) else "low")

    num, toks = _find(tokens, "number", re.compile(r"(?=[A-Za-z/\-]*\d)[A-Za-z0-9][A-Za-z0-9/\-]{0,20}"))
    if num:
        fields["number"] = _field(num, toks)
    dt, toks = _find(tokens, "date", DATE_RE)
    if not dt:
        # Unlabelled dates (thermal bills): first date-like token in the top half of the first page.
        top = next((t for t in tokens if t.id < 10000 and t.box[1] < 0.5 and DATE_RE.search(t.text)), None)
        if top:
            dt, toks = DATE_RE.search(top.text).group(0), [top]
    if dt:
        from lekha.engines.dates import parse_date

        pd = parse_date(dt)
        fields["date"] = _field(pd.isoformat() if pd else None, toks)
    pos, toks = _find(tokens, "place_of_supply", re.compile(r"\d{2}|[A-Za-z][A-Za-z ]+"))
    if pos:
        fields["place_of_supply"] = _field(parse_place_of_supply(pos), toks)
    for key in ("taxable_value", "igst", "cgst", "sgst", "cess", "round_off", "total"):
        val, toks = _find(tokens, key, AMOUNT_RE, last=key in ("total", "taxable_value"))
        if val:
            fields[key] = _field(val.replace(",", ""), toks)
        elif key in ("igst", "cgst", "sgst", "cess", "round_off"):
            fields[key] = _field("0.00", [], "medium")
    irn, toks = _find(tokens, "irn", re.compile(r"[0-9a-f]{64}"))
    if irn:
        fields["irn"] = _field(irn, toks, "high")
    fields["reverse_charge"] = _field("false", [], "medium")
    is_invoice = any(re.search(r"tax\s+invoice|invoice|bill\s+of\s+supply|credit\s+note|debit\s+note", t.text, re.I)
                     for t in tokens)
    doc_type = "invoice"
    for t in tokens[:40]:
        if re.search(r"credit\s+note", t.text, re.I):
            doc_type = "credit_note"
        elif re.search(r"debit\s+note", t.text, re.I):
            doc_type = "debit_note"
        elif re.search(r"bill\s+of\s+supply", t.text, re.I):
            doc_type = "bill_of_supply"
    return {"is_invoice": is_invoice, "doc_type": doc_type, "fields": fields, "lines": [],
            "notes": "Extracted without AI (offline rules); line items not extracted."}
