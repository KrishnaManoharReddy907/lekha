"""GSTR-3B Table 4 (Eligible ITC) builder (N6).

Table 4 rows:
  4A(1) Import of goods            4A(3) Inward supplies liable to reverse charge
  4A(4) Inward supplies from ISD   4A(5) All other ITC
  4B(1) ITC reversed — as per Rule 38, 42, 43 and Sec 17(5) (permanent)
  4B(2) ITC reversed — others (temporary: Rule 37, 37A, not received, under-reported, not in books)
  4C    Net ITC available = 4A − 4B
  4D(1) ITC reclaimed which was reversed under 4B(2) in an earlier period
  4D(2) Ineligible ITC under Sec 16(4) & ITC restricted by place-of-supply rules (memo)
Each cell keeps the ids of the records/invoices that make it up, so every number can be traced.
Approach follows the CBIC instruction that ITC auto-populated from GSTR-2B is reported in 4A and
ineligible/temporarily-ineligible amounts are reversed in 4B.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from lekha.engines.money import ZERO

HEADS = ("igst", "cgst", "sgst", "cess")
ROWS = ["4A1", "4A3", "4A4", "4A5", "4B1", "4B2", "4C", "4D1", "4D2"]
LABELS = {
    "4A1": "4A(1) Import of goods",
    "4A3": "4A(3) Inward supplies liable to reverse charge",
    "4A4": "4A(4) Inward supplies from ISD",
    "4A5": "4A(5) All other ITC",
    "4B1": "4B(1) Reversed — Rules 38, 42, 43 & Sec 17(5)",
    "4B2": "4B(2) Reversed — Others",
    "4C": "4C Net ITC available (A − B)",
    "4D1": "4D(1) ITC reclaimed (reversed earlier under 4B(2))",
    "4D2": "4D(2) Ineligible ITC — Sec 16(4) & place of supply",
}


@dataclass
class Amount:
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO

    def add(self, other: Amount, sign: int = 1) -> None:
        for h in HEADS:
            setattr(self, h, getattr(self, h) + sign * getattr(other, h))

    def scaled(self, f: Decimal) -> Amount:
        return Amount(*(getattr(self, h) * f for h in HEADS)).rounded()

    def rounded(self) -> Amount:
        return Amount(*(getattr(self, h).quantize(Decimal("0.01")) for h in HEADS))

    @property
    def total(self) -> Decimal:
        return self.igst + self.cgst + self.sgst + self.cess


@dataclass
class Item:
    """One 2B record (or book invoice for RCM) with what should happen to it in Table 4."""

    ref: str  # "rec:<id>" or "inv:<id>"
    label: str
    amount: Amount
    row: str  # 4A1 | 4A3 | 4A4 | 4A5 | none
    sign: int = 1  # credit notes reduce
    reversals: list[tuple[str, Decimal, str]] = field(default_factory=list)  # (row 4B1/4B2, fraction 0..1, reason)
    ineligible_reason: str | None = None  # → 4D(2)
    reclaim: Decimal = ZERO  # fraction reclaimed into 4D(1)


@dataclass
class Table4:
    cells: dict[str, Amount]
    sources: dict[str, list[dict[str, Any]]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cells": {r: {h: str(getattr(a, h)) for h in HEADS} | {"total": str(a.total), "label": LABELS[r]}
                      for r, a in self.cells.items()},
            "sources": self.sources,
        }


def build(items: list[Item]) -> Table4:
    cells = {r: Amount() for r in ROWS}
    sources: dict[str, list[dict[str, Any]]] = {r: [] for r in ROWS}
    for it in items:
        if it.ineligible_reason:
            cells["4D2"].add(it.amount)
            sources["4D2"].append({"ref": it.ref, "label": it.label, "amount": str(it.amount.total),
                                   "reason": it.ineligible_reason})
            continue
        if it.row in ("4A1", "4A3", "4A4", "4A5"):
            cells[it.row].add(it.amount, it.sign)
            sources[it.row].append({"ref": it.ref, "label": it.label, "amount": str(it.sign * it.amount.total)})
        for row, frac, reason in it.reversals:
            part = it.amount.scaled(frac)
            cells[row].add(part)
            sources[row].append({"ref": it.ref, "label": it.label, "amount": str(part.total), "reason": reason})
        if it.reclaim > 0:
            part = it.amount.scaled(it.reclaim)
            cells["4D1"].add(part)
            sources["4D1"].append({"ref": it.ref, "label": it.label, "amount": str(part.total),
                                   "reason": "Condition cleared (paid / received / reported)"})
    net = Amount()
    for r in ("4A1", "4A3", "4A4", "4A5"):
        net.add(cells[r])
    for r in ("4B1", "4B2"):
        net.add(cells[r], -1)
    cells["4C"] = net
    return Table4({r: a.rounded() for r, a in cells.items()}, sources)
