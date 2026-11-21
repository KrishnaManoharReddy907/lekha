"""Synthetic GSTR-2B generator with planted, labelled discrepancies (M15).

Given the invoices a buyer received in a month, produce the GSTR-2B JSON a portal would generate,
after simulating what really goes wrong at suppliers: not filing, typing the number differently,
reporting a different value, IGST instead of CGST+SGST, a different date, ITC marked unavailable,
splitting one invoice into two records, and records that the buyer never received.
Each book invoice gets a label of what reconciliation should conclude.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from lekha.bench.catalog import make_party
from lekha.engines.dates import add_months, period_of

D2 = Decimal("0.01")

SCENARIOS = [
    ("clean", 55),
    ("missing", 9),
    ("number_format", 8),
    ("value_diff", 6),
    ("date_diff", 5),
    ("tax_head", 3),
    ("itc_unavailable", 3),
    ("split", 3),
    ("late_next_period", 8),
]

EXPECT = {
    "clean": "matched",
    "missing": "missing_in_2b",
    "number_format": "matched",
    "value_diff": "mismatch",
    "date_diff": "matched",
    "tax_head": "mismatch",
    "itc_unavailable": "mismatch",
    "split": "matched",
    "late_next_period": "missing_in_2b",
}


def _reformat(number: str, rng: random.Random) -> str:
    import re

    options = [
        number.replace("/", "-"),
        number.replace("-", "/"),
        re.sub(r"(\d+)$", lambda m: str(int(m.group(1))), number),
        re.sub(r"/\d{2}-\d{2}/", "/", number),
        number.lower(),
        number.replace("/", ""),
    ]
    options = [o for o in options if o != number] or [number + " "]
    return rng.choice(options).strip() or number


def _items(inv: dict[str, Any], scale: Decimal = Decimal(1), swap_head: bool = False) -> list[dict[str, Any]]:
    out = []
    for i, ln in enumerate(inv["lines"]):
        tx = (Decimal(ln["taxable_value"]) * scale).quantize(D2)
        rate = Decimal(str(ln["tax_rate"]))
        tax = (tx * rate / 100).quantize(D2)
        inter = Decimal(inv["igst"]) > 0
        if swap_head:
            inter = not inter
        out.append({"num": i + 1, "rt": float(rate), "txval": float(tx),
                    "igst": float(tax) if inter else 0.0,
                    "cgst": 0.0 if inter else float((tax / 2).quantize(D2)),
                    "sgst": 0.0 if inter else float((tax / 2).quantize(D2)), "cess": 0.0})
    return out


def generate(buyer_gstin: str, period: str, invoices: list[dict[str, Any]], rng: random.Random,
             extra_records: int = 2, scenario_weights: list[tuple[str, int]] | None = None,
             forced: dict[int, str] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (gstr2b_json, labels, carry_over).

    labels: one per book invoice {index, scenario, expect}. carry_over: invoices the supplier will report
    late (to place in a later period's 2B).
    """
    weights = scenario_weights or SCENARIOS
    names = [n for n, _ in weights]
    w = [x for _, x in weights]
    by_supplier: dict[str, dict[str, Any]] = {}
    labels: list[dict[str, Any]] = []
    carry: list[dict[str, Any]] = []
    first = date(int(period[2:]), int(period[:2]), 1)
    filing = add_months(first, 1) + timedelta(days=rng.randint(5, 10))

    def sup(inv: dict[str, Any]) -> dict[str, Any]:
        g = inv["supplier_gstin"]
        if g not in by_supplier:
            by_supplier[g] = {"ctin": g, "trdnm": inv["supplier_name"], "supfildt": filing.strftime("%d-%m-%Y"),
                              "supprd": period, "inv": []}
        return by_supplier[g]

    for idx, inv in enumerate(invoices):
        scen = (forced or {}).get(idx) or rng.choices(names, weights=w)[0]
        if scen == "split" and len(inv["lines"]) < 2:
            scen = "clean"
        labels.append({"index": idx, "scenario": scen, "expect": EXPECT[scen]})
        if scen in ("missing",):
            continue
        if scen == "late_next_period":
            carry.append(inv)
            continue
        base = {
            "inum": inv["number"], "typ": "R", "dt": date.fromisoformat(inv["date"]).strftime("%d-%m-%Y"),
            "val": float(Decimal(inv["total"])), "pos": inv["place_of_supply"], "rev": "N", "itcavl": "Y", "rsn": "",
            "diffprcnt": 1, "srctyp": "e-Invoice" if inv.get("irn") else "", "irn": inv.get("irn") or "",
            "items": _items(inv),
        }
        if scen == "number_format":
            base["inum"] = _reformat(inv["number"], rng)
        elif scen == "value_diff":
            scale = Decimal(str(rng.choice([0.85, 0.9, 0.95, 1.05, 1.1])))
            base["items"] = _items(inv, scale)
            base["val"] = float(sum(Decimal(str(i["txval"] + i["igst"] + i["cgst"] + i["sgst"])) for i in base["items"]))
        elif scen == "date_diff":
            base["dt"] = (date.fromisoformat(inv["date"]) + timedelta(days=rng.choice([-2, -1, 1, 2]))).strftime("%d-%m-%Y")
        elif scen == "tax_head":
            base["items"] = _items(inv, swap_head=True)
        elif scen == "itc_unavailable":
            base["itcavl"] = "N"
            base["rsn"] = rng.choice(["POS and supplier state are same but recipient state is different",
                                      "Return filed post annual cut-off"])
        if scen == "split":
            for li in range(2):
                part = dict(base)
                half = base["items"][li::2]
                part["items"] = [dict(x, num=n + 1) for n, x in enumerate(half)]
                part["val"] = float(sum(Decimal(str(i["txval"] + i["igst"] + i["cgst"] + i["sgst"])) for i in half))
                sup(inv)["inv"].append(part)
        else:
            sup(inv)["inv"].append(base)

    for _ in range(extra_records):
        s = make_party(rng)
        tx = Decimal(rng.randint(5000, 90000))
        inter = s["state"] != buyer_gstin[:2]
        tax = (tx * Decimal(18) / 100).quantize(D2)
        d0 = first + timedelta(days=rng.randint(0, 27))
        by_supplier.setdefault(s["gstin"], {"ctin": s["gstin"], "trdnm": s["name"], "supfildt": filing.strftime("%d-%m-%Y"),
                                            "supprd": period, "inv": []})["inv"].append({
            "inum": f"X{rng.randint(100, 9999)}", "typ": "R", "dt": d0.strftime("%d-%m-%Y"), "val": float(tx + tax),
            "pos": buyer_gstin[:2], "rev": "N", "itcavl": "Y", "rsn": "", "irn": "",
            "items": [{"num": 1, "rt": 18.0, "txval": float(tx), "igst": float(tax) if inter else 0.0,
                       "cgst": 0.0 if inter else float(tax / 2), "sgst": 0.0 if inter else float(tax / 2), "cess": 0.0}],
        })

    gen_date = add_months(first, 1).replace(day=14)
    doc = {"chksum": "synthetic", "data": {"gstin": buyer_gstin, "rtnprd": period, "version": "1.0",
                                           "gendt": gen_date.strftime("%d-%m-%Y"),
                                           "docdata": {"b2b": list(by_supplier.values())}}}
    return doc, labels, carry


def late_records(buyer_gstin: str, period: str, invoices: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """Supplier entries for invoices reported a month (or more) late, to merge into a later 2B."""
    first = date(int(period[2:]), int(period[:2]), 1)
    filing = add_months(first, 1) + timedelta(days=rng.randint(8, 12))
    out: dict[str, dict[str, Any]] = {}
    for inv in invoices:
        g = inv["supplier_gstin"]
        out.setdefault(g, {"ctin": g, "trdnm": inv["supplier_name"], "supfildt": filing.strftime("%d-%m-%Y"),
                           "supprd": period, "inv": []})["inv"].append({
            "inum": inv["number"], "typ": "R", "dt": date.fromisoformat(inv["date"]).strftime("%d-%m-%Y"),
            "val": float(Decimal(inv["total"])), "pos": inv["place_of_supply"], "rev": "N", "itcavl": "Y", "rsn": "",
            "irn": inv.get("irn") or "", "items": _items(inv)})
    return list(out.values())


_ = period_of
