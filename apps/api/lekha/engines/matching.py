"""Explainable reconciliation of the purchase register against GSTR-2B (M7).

1. Candidate generation: a book invoice and a 2B record are candidates when their supplier GSTINs
   are identical, share the same PAN (another state registration of the same business), or are one
   or two characters apart (a typo in the books).
2. Feature scoring for every candidate pair (GSTIN, number, date, taxable value, tax, document type)
   with a weighted score in 0..1.
3. Optimal one-to-one assignment per supplier with the Hungarian algorithm, so one record can never
   be matched to two invoices (the classic VLOOKUP failure).
4. Split/merge search among what is left: one invoice reported as several records with the same
   number (or the reverse) whose amounts add up.
5. Classification into matched / mismatch (with typed reasons) / missing_in_2b / missing_in_books,
   and a plain-language explanation built from the per-feature contributions.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein
from scipy.optimize import linear_sum_assignment

from lekha.engines.invoice import core_number, normalise_number
from lekha.engines.money import ZERO

WEIGHTS = {"gstin": 0.20, "number": 0.35, "date": 0.10, "taxable": 0.15, "tax": 0.15, "type": 0.05}
ACCEPT = 0.62


@dataclass
class Doc:
    """Either side of the reconciliation (a book invoice or a 2B record)."""

    id: Any
    supplier_gstin: str
    number: str
    date: date | None
    taxable: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    cess: Decimal
    doc_type: str = "invoice"
    itc_available: bool = True
    reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def tax(self) -> Decimal:
        return self.igst + self.cgst + self.sgst + self.cess

    @property
    def norm(self) -> str:
        return normalise_number(self.number)


@dataclass
class MatchResult:
    book_ids: list[Any]
    record_ids: list[Any]
    kind: str  # exact | normalised | fuzzy | split | unmatched
    status: str  # matched | mismatch | missing_in_2b | missing_in_books
    mismatch_types: list[str]
    score: float
    features: dict[str, float]
    explanation: dict[str, Any]
    itc_books: Decimal
    itc_2b: Decimal
    group_id: uuid.UUID | None = None


def _gstin_feature(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if len(a) == 15 and len(b) == 15 and a[2:12] == b[2:12]:
        return 0.7
    if Levenshtein.distance(a, b) <= 2:
        return 0.5
    return 0.0


def _number_feature(a: Doc, b: Doc) -> float:
    if a.number.strip().upper() == b.number.strip().upper():
        return 1.0
    if a.norm and a.norm == b.norm:
        return 0.95
    # Same characters once every separator is removed ('GST/6816/25-26' vs 'GST681625-26').
    ca, cb = (re.sub(r"[^0-9A-Z]", "", x.upper()) for x in (a.number, b.number))
    if ca and ca == cb:
        return 0.95
    if core_number(a.number) and core_number(a.number) == core_number(b.number):
        return 0.8
    return round(fuzz.ratio(a.norm, b.norm) / 100 * 0.7, 4)


def _date_feature(a: date | None, b: date | None) -> float:
    if not a or not b:
        return 0.5
    gap = abs((a - b).days)
    if gap == 0:
        return 1.0
    if gap <= 3:
        return 0.9
    if gap <= 31:
        return 0.6
    return 0.2


def _amount_feature(a: Decimal, b: Decimal, tol: Decimal) -> float:
    if abs(a - b) <= tol:
        return 1.0
    big = max(abs(a), abs(b), Decimal(1))
    return max(0.0, float(1 - abs(a - b) / big))


def features(book: Doc, rec: Doc, tol: Decimal) -> dict[str, float]:
    return {
        "gstin": _gstin_feature(book.supplier_gstin, rec.supplier_gstin),
        "number": _number_feature(book, rec),
        "date": _date_feature(book.date, rec.date),
        "taxable": _amount_feature(book.taxable, rec.taxable, tol),
        "tax": _amount_feature(book.tax, rec.tax, tol),
        "type": 1.0 if book.doc_type == rec.doc_type else 0.3,
    }


def score(f: dict[str, float]) -> float:
    return round(sum(WEIGHTS[k] * v for k, v in f.items()), 4)


def _acceptable(f: dict[str, float], s: float) -> bool:
    if f["gstin"] == 0:
        return False
    # Either the number agrees, or the amounts agree exactly and the date is close (supplier mistyped number).
    return s >= ACCEPT and (f["number"] >= 0.5 or (f["taxable"] == 1 and f["tax"] == 1 and f["date"] >= 0.6))


def _classify(book: Doc, rec: Doc, f: dict[str, float], tol: Decimal) -> tuple[str, list[str], str]:
    types: list[str] = []
    if f["gstin"] < 1:
        types.append("GSTIN_DIFFERENT" if f["gstin"] < 0.7 else "OTHER_STATE_REGISTRATION")
    if f["number"] < 0.95:
        types.append("NUMBER_DIFFERENT")
    if f["date"] < 1 and book.date and rec.date:
        types.append("DATE_DIFFERENT")
    if abs(book.taxable - rec.taxable) > tol:
        types.append("TAXABLE_VALUE_DIFFERENT")
    if abs(book.tax - rec.tax) > tol:
        types.append("TAX_AMOUNT_DIFFERENT")
    elif (book.igst > 0) != (rec.igst > 0):
        types.append("TAX_HEAD_DIFFERENT")
    if book.doc_type != rec.doc_type:
        types.append("DOC_TYPE_DIFFERENT")
    if not rec.itc_available:
        types.append("ITC_NOT_AVAILABLE_IN_2B")
    raw_equal = book.number.strip().upper() == rec.number.strip().upper()
    kind = "exact" if raw_equal and not types else "normalised" if f["number"] >= 0.95 else "fuzzy"
    # Only money-affecting or legally relevant differences make it a mismatch.
    material = [t for t in types if t not in ("DATE_DIFFERENT",) or f["date"] < 0.6]
    material = [t for t in material if t != "NUMBER_DIFFERENT" or f["number"] < 0.8]
    status = "mismatch" if material else "matched"
    return kind, types, status


FEATURE_TEXT = {
    "gstin": {1.0: "Supplier GSTIN is identical", 0.7: "Same PAN but a different state registration",
              0.5: "Supplier GSTIN differs by one or two characters (typo?)"},
}


def explain(book: Doc | None, rec: Doc | None, f: dict[str, float] | None, types: list[str], tol: Decimal) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    if book and rec and f:
        g = f["gstin"]
        lines.append({"feature": "gstin", "ok": g == 1, "weight": WEIGHTS["gstin"], "value": g,
                      "text": FEATURE_TEXT["gstin"].get(g, "Supplier GSTIN differs")})
        n = f["number"]
        if n == 1:
            t = f"Invoice number identical ({book.number})"
        elif n >= 0.95:
            t = f"Same invoice number written differently: books '{book.number}' vs 2B '{rec.number}'"
        elif n >= 0.8:
            t = f"Numbers share the serial {core_number(book.number)}: '{book.number}' vs '{rec.number}'"
        else:
            t = f"Invoice numbers differ: '{book.number}' vs '{rec.number}' ({n:.0%} similar)"
        lines.append({"feature": "number", "ok": n >= 0.95, "weight": WEIGHTS["number"], "value": n, "text": t})
        if book.date and rec.date:
            gap = (rec.date - book.date).days
            lines.append({"feature": "date", "ok": gap == 0, "weight": WEIGHTS["date"], "value": f["date"],
                          "text": "Same invoice date" if gap == 0 else
                          f"Dates differ by {abs(gap)} day{'s' if abs(gap) != 1 else ''} ({book.date} vs {rec.date})"})
        dtx = rec.taxable - book.taxable
        lines.append({"feature": "taxable", "ok": abs(dtx) <= tol, "weight": WEIGHTS["taxable"], "value": f["taxable"],
                      "text": "Taxable value agrees" if abs(dtx) <= tol else
                      f"Taxable value differs by ₹{abs(dtx):,.2f} (books {book.taxable:,.2f}, 2B {rec.taxable:,.2f})"})
        dt = rec.tax - book.tax
        lines.append({"feature": "tax", "ok": abs(dt) <= tol, "weight": WEIGHTS["tax"], "value": f["tax"],
                      "text": "Tax amount agrees" if abs(dt) <= tol else
                      f"Tax differs by ₹{abs(dt):,.2f}: ITC in 2B is {'lower' if dt < 0 else 'higher'} than in your books"})
        if "TAX_HEAD_DIFFERENT" in types:
            lines.append({"feature": "tax_head", "ok": False, "weight": 0, "value": 0,
                          "text": "Tax heads differ (IGST in one, CGST+SGST in the other): place of supply was reported differently"})
        if not rec.itc_available:
            lines.append({"feature": "itc", "ok": False, "weight": 0, "value": 0,
                          "text": f"GSTR-2B says ITC is NOT available{': ' + rec.reason if rec.reason else ''}"})
    elif book and not rec:
        lines.append({"feature": "presence", "ok": False, "weight": 1, "value": 0,
                      "text": "Not in GSTR-2B: the supplier has not reported this invoice (or reported it against "
                              "a different GSTIN / period)."})
    elif rec and not book:
        lines.append({"feature": "presence", "ok": False, "weight": 1, "value": 0,
                      "text": "In GSTR-2B but not in your books: the invoice was not received or not entered — or "
                              "it is not yours."})
    return {"lines": lines, "summary": _summary(book, rec, types)}


def _summary(book: Doc | None, rec: Doc | None, types: list[str]) -> str:
    if book and not rec:
        return "Missing in GSTR-2B"
    if rec and not book:
        return "Missing in books"
    if not types:
        return "Exact match"
    nice = {"NUMBER_DIFFERENT": "number written differently", "DATE_DIFFERENT": "date differs",
            "TAXABLE_VALUE_DIFFERENT": "taxable value differs", "TAX_AMOUNT_DIFFERENT": "tax differs",
            "TAX_HEAD_DIFFERENT": "IGST vs CGST/SGST", "DOC_TYPE_DIFFERENT": "document type differs",
            "GSTIN_DIFFERENT": "GSTIN typo", "OTHER_STATE_REGISTRATION": "other state GSTIN",
            "ITC_NOT_AVAILABLE_IN_2B": "ITC not available"}
    return "Matched — " + ", ".join(nice.get(t, t) for t in types)


def reconcile(books: list[Doc], records: list[Doc], tol: Decimal = Decimal("1.00")) -> list[MatchResult]:
    results: list[MatchResult] = []
    used_b: set[Any] = set()
    used_r: set[Any] = set()

    # Split / merge first: one invoice reported as several records with the same number (or the reverse)
    # whose amounts add up. Done before the one-to-one assignment, which would otherwise grab one half.
    left_b = [b for b in books if b.id not in used_b]
    left_r = [r for r in records if r.id not in used_r]
    rb: dict[tuple[str, str], list[Doc]] = defaultdict(list)
    for r in left_r:
        rb[(r.supplier_gstin, r.norm)].append(r)
    bb: dict[tuple[str, str], list[Doc]] = defaultdict(list)
    for b in left_b:
        bb[(b.supplier_gstin, b.norm)].append(b)
    for k in set(rb) & set(bb):
        b_list, r_list = bb[k], rb[k]
        if len(b_list) == 1 and len(r_list) == 1:
            continue
        tb = sum((b.taxable for b in b_list), ZERO)
        tr = sum((r.taxable for r in r_list), ZERO)
        xb = sum((b.tax for b in b_list), ZERO)
        xr = sum((r.tax for r in r_list), ZERO)
        if abs(tb - tr) <= tol * max(len(b_list), len(r_list)) and abs(xb - xr) <= tol * max(len(b_list), len(r_list)):
            gid = uuid.uuid4()
            expl = {"lines": [{"feature": "split", "ok": True, "weight": 1, "value": 1,
                               "text": f"{len(b_list)} invoice(s) in books correspond to {len(r_list)} record(s) in "
                                       f"2B with the same number '{b_list[0].number}'; totals agree."}],
                    "summary": "Matched as split/merged entries"}
            results.append(MatchResult([b.id for b in b_list], [r.id for r in r_list], "split", "matched", ["SPLIT"],
                                       0.9, {}, expl, xb, sum((r.tax for r in r_list if r.itc_available), ZERO), gid))
            used_b.update(b.id for b in b_list)
            used_r.update(r.id for r in r_list)

    # Group by PAN so different-state registrations and GSTIN typos are still candidates.
    def key(g: str) -> str:
        return g[2:12] if len(g) == 15 else g

    groups: dict[str, tuple[list[Doc], list[Doc]]] = defaultdict(lambda: ([], []))
    for b in books:
        if b.id not in used_b:
            groups[key(b.supplier_gstin)][0].append(b)
    for r in records:
        if r.id not in used_r:
            groups[key(r.supplier_gstin)][1].append(r)
    # GSTIN typos can change the PAN part too: attach orphan books to the nearest record group.
    orphan_keys = [k for k, (bs, rs) in groups.items() if bs and not rs]
    record_keys = [k for k, (_bs, rs) in groups.items() if rs]
    for ok in orphan_keys:
        g = groups[ok][0][0].supplier_gstin
        near = [rk for rk in record_keys if groups[rk][1] and Levenshtein.distance(g, groups[rk][1][0].supplier_gstin) <= 2]
        if len(near) == 1:
            groups[near[0]][0].extend(groups[ok][0])
            del groups[ok]

    for bs, rs in groups.values():
        if not bs or not rs:
            continue
        m = np.zeros((len(bs), len(rs)))
        feats: dict[tuple[int, int], dict[str, float]] = {}
        for i, b in enumerate(bs):
            for j, r in enumerate(rs):
                f = features(b, r, tol)
                s = score(f)
                feats[(i, j)] = f
                m[i, j] = s if _acceptable(f, s) else 0.0
        rows, cols = linear_sum_assignment(-m)
        for i, j in zip(rows, cols, strict=True):
            if m[i, j] <= 0:
                continue
            b, r = bs[i], rs[j]
            f = feats[(i, j)]
            kind, types, status = _classify(b, r, f, tol)
            results.append(MatchResult([b.id], [r.id], kind, status, types, float(m[i, j]), f,
                                       explain(b, r, f, types, tol), b.tax, r.tax if r.itc_available else ZERO))
            used_b.add(b.id)
            used_r.add(r.id)

    for b in books:
        if b.id not in used_b:
            results.append(MatchResult([b.id], [], "unmatched", "missing_in_2b", ["NOT_IN_2B"], 0.0, {},
                                       explain(b, None, None, [], tol), b.tax, ZERO))
    for r in records:
        if r.id not in used_r:
            results.append(MatchResult([], [r.id], "unmatched", "missing_in_books", ["NOT_IN_BOOKS"], 0.0, {},
                                       explain(None, r, None, [], tol), ZERO, r.tax if r.itc_available else ZERO))
    return results
