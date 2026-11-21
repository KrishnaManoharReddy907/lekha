import json
import random
from collections import Counter
from datetime import date
from decimal import Decimal

from lekha.bench.catalog import make_party
from lekha.bench.generator import make_invoice
from lekha.bench.gstr2b_gen import generate
from lekha.engines import gstr2b
from lekha.engines.matching import Doc, reconcile


def _books_and_2b(seed: int, n: int = 120, forced: dict[int, str] | None = None):
    rng = random.Random(seed)
    buyer = make_party(rng, "36")
    suppliers = [make_party(rng) for _ in range(12)]
    invs = [make_invoice(rng, buyer, on=date(2025, 10, rng.randint(1, 28)), supplier=rng.choice(suppliers),
                         seq=rng.randint(1, 9000)).truth() for _ in range(n)]
    # Unique numbers per supplier (real suppliers never reuse a number in a year).
    seen = set()
    invs = [i for i in invs if (i["supplier_gstin"], i["number"]) not in seen and not seen.add((i["supplier_gstin"], i["number"]))]
    doc, labels, _carry = generate(buyer["gstin"], "102025", invs, rng, extra_records=3, forced=forced)
    return buyer, invs, doc, labels


def _docs(invs, stmt):
    books = [Doc(id=i, supplier_gstin=inv["supplier_gstin"], number=inv["number"], date=date.fromisoformat(inv["date"]),
                 taxable=Decimal(inv["taxable_value"]), igst=Decimal(inv["igst"]), cgst=Decimal(inv["cgst"]),
                 sgst=Decimal(inv["sgst"]), cess=Decimal("0")) for i, inv in enumerate(invs)]
    recs = [Doc(id=f"r{j}", supplier_gstin=r.supplier_gstin, number=r.number, date=r.invoice_date, taxable=r.taxable_value,
                igst=r.igst, cgst=r.cgst, sgst=r.sgst, cess=r.cess, doc_type=r.doc_type, itc_available=r.itc_available,
                reason=r.itc_unavailable_reason) for j, r in enumerate(stmt.records)]
    return books, recs


def test_parse_portal_json_shape():
    _b, invs, doc, _l = _books_and_2b(1, 30)
    stmt = gstr2b.parse_json(json.dumps(doc))
    assert stmt.period == "102025" and stmt.gstin and stmt.generated_on == date(2025, 11, 14)
    assert stmt.records and all(len(r.supplier_gstin) == 15 for r in stmt.records)
    s = stmt.summary()
    assert s["count"] == len(stmt.records)


def test_parse_rejects_garbage():
    import pytest

    with pytest.raises(gstr2b.Gstr2bError):
        gstr2b.parse_json(b"not json")
    with pytest.raises(gstr2b.Gstr2bError):
        gstr2b.parse_json(b'{"hello": 1}')


def test_reconciliation_accuracy_on_planted_errors():
    """Every planted scenario must be classified as expected (precision/recall = 100% on synthetic data)."""
    total = Counter()
    wrong = []
    for seed in (2, 3, 4):
        _buyer, invs, doc, labels = _books_and_2b(seed)
        stmt = gstr2b.parse_json(doc)
        books, recs = _docs(invs, stmt)
        results = reconcile(books, recs)
        by_book = {bid: r for r in results for bid in r.book_ids}
        for lab in labels:
            got = by_book[lab["index"]].status
            total[lab["scenario"]] += 1
            if got != lab["expect"]:
                wrong.append((seed, lab["scenario"], lab["expect"], got, by_book[lab["index"]].explanation["summary"]))
        # Extra records the buyer never received surface as missing in books.
        assert sum(1 for r in results if r.status == "missing_in_books") >= 3
        # No record is ever used twice.
        used = [rid for r in results for rid in r.record_ids]
        assert len(used) == len(set(used))
    assert not wrong, wrong
    assert len(total) >= 8


def test_specific_mismatch_types():
    forced = {0: "value_diff", 1: "tax_head", 2: "itc_unavailable", 3: "number_format", 4: "date_diff"}
    _buyer, invs, doc, _labels = _books_and_2b(9, 10, forced=forced)
    books, recs = _docs(invs, gstr2b.parse_json(doc))
    by_book = {bid: r for r in reconcile(books, recs) for bid in r.book_ids}
    assert {"TAXABLE_VALUE_DIFFERENT", "TAX_AMOUNT_DIFFERENT"} & set(by_book[0].mismatch_types)
    assert "TAX_HEAD_DIFFERENT" in by_book[1].mismatch_types
    assert "ITC_NOT_AVAILABLE_IN_2B" in by_book[2].mismatch_types and by_book[2].itc_2b == 0
    assert by_book[3].status == "matched" and by_book[3].kind in ("normalised", "fuzzy")
    assert by_book[4].status == "matched" and "DATE_DIFFERENT" in by_book[4].mismatch_types
    # Explanations are human readable.
    assert any("Tax differs" in ln["text"] or "Taxable value differs" in ln["text"] for ln in by_book[0].explanation["lines"])


def test_books_gstin_typo_still_matches():
    rec = Doc(id="r", supplier_gstin="29AABCU9603R1ZJ", number="INV-9", date=date(2025, 10, 3),
              taxable=Decimal("1000"), igst=Decimal("180"), cgst=Decimal(0), sgst=Decimal(0), cess=Decimal(0))
    book = Doc(id="b", supplier_gstin="29AABCU9608R1ZJ", number="INV-9", date=date(2025, 10, 3),
               taxable=Decimal("1000"), igst=Decimal("180"), cgst=Decimal(0), sgst=Decimal(0), cess=Decimal(0))
    (res,) = [r for r in reconcile([book], [rec]) if r.book_ids]
    assert res.record_ids == ["r"] and "GSTIN_DIFFERENT" in res.mismatch_types


def test_hungarian_prevents_double_matching():
    def mk(i, n, tx):
        return Doc(id=i, supplier_gstin="29AABCU9603R1ZJ", number=n, date=date(2025, 10, 3), taxable=Decimal(tx),
                   igst=Decimal(tx) * Decimal("0.18"), cgst=Decimal(0), sgst=Decimal(0), cess=Decimal(0))
    books = [mk("b1", "INV-10", 1000), mk("b2", "INV-10", 2000)]  # duplicate entry in books
    recs = [mk("r1", "INV-10", 1000)]
    res = reconcile(books, recs)
    matched = [r for r in res if r.record_ids and r.book_ids]
    assert len(matched) == 1 and matched[0].book_ids == ["b1"]
    assert any(r.status == "missing_in_2b" and r.book_ids == ["b2"] for r in res)
