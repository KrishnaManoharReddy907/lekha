"""Invoice extraction with Claude vision, grounded to OCR tokens."""

from __future__ import annotations

import base64
from typing import Any

from lekha.ai.client import AIResult, complete
from lekha.ocr.pages import AI_MAX_SIDE, Page, png_bytes

PROMPT_VERSION = "ext-2026.11.1"

HEADER_FIELDS = [
    "supplier_gstin", "supplier_name", "buyer_gstin", "buyer_name", "number", "date", "place_of_supply",
    "reverse_charge", "taxable_value", "igst", "cgst", "sgst", "cess", "round_off", "total", "irn",
]
LINE_FIELDS = ["description", "hsn", "quantity", "unit", "unit_price", "taxable_value", "tax_rate", "igst", "cgst",
               "sgst", "cess"]

_NSTR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_FIELD = {
    "type": "object",
    "properties": {
        "value": _NSTR,
        "token_ids": {"type": "array", "items": {"type": "integer"}},
        "certainty": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["value", "token_ids", "certainty"],
    "additionalProperties": False,
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_invoice": {"type": "boolean"},
        "doc_type": {"type": "string", "enum": ["invoice", "credit_note", "debit_note", "bill_of_supply", "other"]},
        "fields": {
            "type": "object",
            "properties": {f: _FIELD for f in HEADER_FIELDS},
            "required": HEADER_FIELDS,
            "additionalProperties": False,
        },
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {**{f: _NSTR for f in LINE_FIELDS},
                               "token_ids": {"type": "array", "items": {"type": "integer"}}},
                "required": [*LINE_FIELDS, "token_ids"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["is_invoice", "doc_type", "fields", "lines", "notes"],
    "additionalProperties": False,
}

SYSTEM = """You extract data from Indian GST purchase documents (tax invoices, bills of supply, credit and debit notes) for an accounting system. Accuracy matters more than completeness: a wrong value costs the business real money, a null value only asks a human to check.

You receive the page image(s) and the OCR tokens found on them. Each token has an id, its text and its position (x0,y0 = top-left, 0..1 of page width/height). For every field, return:
- value: normalised as described below, or null if it is not on the document.
- token_ids: the ids of the OCR tokens the value was read from (empty if you read it only from the image, e.g. when OCR missed it).
- certainty: high (clearly printed, unambiguous), medium (readable but some doubt), low (guess, smudged, handwritten or conflicting).

Normalisation:
- supplier = the business that ISSUED the document (seller). buyer = the business it is billed to ("Bill to", "Buyer", "Recipient", "M/s").
- GSTINs: 15 characters, uppercase, no spaces. Copy exactly what is printed even if it looks wrong; never invent or "fix" one.
- number: invoice / document number exactly as printed (keep slashes and dashes).
- date: YYYY-MM-DD. Indian documents write day first (03/04/2025 = 3 April 2025).
- place_of_supply: 2-digit state code (e.g. "36" for Telangana). If only a state name is printed, convert it. If absent, null.
- reverse_charge: "true" or "false".
- Amounts: plain decimal strings with no commas or currency symbols, e.g. "118000.00". taxable_value = total before tax. igst/cgst/sgst/cess = total of each tax head on the document (0.00 when that head is not charged). round_off may be negative. total = final invoice value.
- irn: the 64-character Invoice Reference Number if printed, else null.
- Lines: one entry per item row with HSN/SAC as printed, tax_rate as a percentage number string ("18"), amounts as above; token_ids for the row.
- doc_type: "bill_of_supply" when no GST is charged by a composition/exempt supplier; "other" if the document is not an invoice at all (set is_invoice false).
Put anything a reviewer should know (handwritten corrections, multiple invoices on one page, stamps over numbers) in notes."""


def _token_listing(pages: list[Page], limit_per_page: int = 900) -> str:
    rows = []
    for pi, p in enumerate(pages):
        rows.append(f"--- page {pi + 1} ---")
        for t in p.tokens[:limit_per_page]:
            x0, y0, _x1, _y1 = t.box
            rows.append(f"{t.id}\t{x0:.2f},{y0:.2f}\t{t.text}")
        if len(p.tokens) > limit_per_page:
            rows.append(f"(+{len(p.tokens) - limit_per_page} more tokens on this page not listed; read them from the image)")
    return "\n".join(rows)


async def extract_with_claude(pages: list[Page], hints: list[str]) -> tuple[dict[str, Any], AIResult]:
    content: list[dict[str, Any]] = []
    for p in pages:
        b64 = base64.standard_b64encode(png_bytes(p.image, AI_MAX_SIDE, fmt="JPEG")).decode()
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    text = "OCR tokens (id, position, text):\n" + _token_listing(pages)
    if hints:
        text += ("\n\nKnown corrections for this supplier from earlier reviews (apply when the same situation "
                 "occurs):\n- " + "\n- ".join(hints))
    content.append({"type": "text", "text": text})
    result = await complete(system=SYSTEM, content=content, schema=SCHEMA, max_tokens=16000)
    return result.data, result
