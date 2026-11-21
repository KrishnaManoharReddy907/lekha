"""Supply-network intelligence (N3): circular trading, shell suppliers, template reuse.

Built only from one firm's own data (its clients' purchase invoices). Nodes are GSTINs; an edge
S → B carries the value of invoices S issued to B. When a firm manages several businesses that
trade with each other, value that goes round in a loop (A → B → C → A) with little real activity is
the signature of circular trading used to generate fake ITC.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import networkx as nx


@dataclass
class Edge:
    supplier: str
    buyer: str
    value: Decimal
    invoice_date: date | None
    number: str
    layout_hash: str | None = None
    round_amount: bool = False


@dataclass
class Signal:
    kind: str
    score: float
    title: str
    detail: str
    members: list[str]
    amount: Decimal
    evidence: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return f"{self.kind}:{'|'.join(sorted(self.members))}"


def _hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


def find_cycles(edges: list[Edge], max_len: int = 5, min_value: Decimal = Decimal(100000)) -> list[Signal]:
    g = nx.DiGraph()
    for e in edges:
        if e.supplier == e.buyer:
            continue
        w = g.get_edge_data(e.supplier, e.buyer, {}).get("value", Decimal(0))
        g.add_edge(e.supplier, e.buyer, value=w + e.value)
    out: list[Signal] = []
    for cyc in nx.simple_cycles(g, length_bound=max_len):
        if len(cyc) < 2:
            continue
        vals = [g[cyc[i]][cyc[(i + 1) % len(cyc)]]["value"] for i in range(len(cyc))]
        flow = min(vals)
        if flow < min_value:
            continue
        # Similar values all the way round is more suspicious than one big and one tiny edge.
        balance = float(min(vals) / max(vals)) if max(vals) else 0
        score = min(1.0, 0.5 + 0.3 * balance + 0.2 * min(1.0, float(flow) / 2_000_000))
        path = " → ".join([*cyc, cyc[0]])
        out.append(Signal("cycle", round(score, 3), f"Circular trading loop of {len(cyc)} businesses",
                          f"Value flows in a loop: {path}. At least ₹{flow:,.0f} goes all the way round. Circular "
                          "invoicing is a common way to create input tax credit without real supplies.",
                          list(cyc), flow, {"edges": [{"from": cyc[i], "to": cyc[(i + 1) % len(cyc)],
                                                       "value": str(vals[i])} for i in range(len(cyc))]}))
    return out


def shell_scores(edges: list[Edge], first_seen: dict[str, date], today: date, client_gstins: set[str]) -> list[Signal]:
    by_sup: dict[str, list[Edge]] = defaultdict(list)
    for e in edges:
        by_sup[e.supplier].append(e)
    all_values = [float(e.value) for e in edges] or [1.0]
    median = statistics.median(all_values)
    out: list[Signal] = []
    for sup, es in by_sup.items():
        if sup in client_gstins:
            continue
        total = sum((e.value for e in es), Decimal(0))
        reasons: list[str] = []
        s = 0.0
        fs = first_seen.get(sup)
        if fs and (today - fs).days <= 90:
            s += 0.25
            reasons.append(f"first seen {(today - fs).days} days ago")
        big = [e for e in es if float(e.value) >= 5 * median and e.value >= 200000]
        if big:
            s += 0.25
            reasons.append(f"{len(big)} invoice(s) far larger than usual")
        if sum(1 for e in es if e.round_amount) >= max(1, len(es) // 2):
            s += 0.15
            reasons.append("round-figure amounts")
        buyers = {e.buyer for e in es}
        if len(buyers) >= 3 and len(es) <= len(buyers) + 1:
            s += 0.15
            reasons.append(f"sells once each to {len(buyers)} of your clients")
        nums = sorted((e.invoice_date, _serial(e.number)) for e in es if e.invoice_date and _serial(e.number) is not None)
        if len(nums) >= 2:
            months = max(1.0, (nums[-1][0] - nums[0][0]).days / 30)
            velocity = (nums[-1][1] - nums[0][1]) / months
            if 0 <= velocity < 3:
                s += 0.2
                reasons.append(f"invoice numbers advance only ~{velocity:.1f}/month")
        if s >= 0.45:
            out.append(Signal("shell", round(min(1.0, s), 3), f"Possible shell supplier {sup}",
                              "Signals: " + "; ".join(reasons) + ".", [sup], total, {"reasons": reasons}))
    return out


def template_reuse(edges: list[Edge], max_distance: int = 3) -> list[Signal]:
    """Different GSTINs issuing invoices with the same visual layout — fake-invoice 'factories' reuse templates."""
    reps: list[tuple[str, set[str]]] = []
    for e in edges:
        if not e.layout_hash:
            continue
        for rep, members in reps:
            if _hamming(e.layout_hash, rep) <= max_distance:
                members.add(e.supplier)
                break
        else:
            reps.append((e.layout_hash, {e.supplier}))
    out: list[Signal] = []
    for rep, members in reps:
        if len(members) >= 3:
            amount = sum((e.value for e in edges if e.supplier in members), Decimal(0))
            out.append(Signal("template_reuse", round(min(1.0, 0.4 + 0.1 * len(members)), 3),
                              f"{len(members)} different suppliers use an identical invoice layout",
                              "Invoices from these GSTINs look the same (same template, logo area and table). Genuine "
                              "businesses rarely share a layout exactly; check they are real and unrelated.",
                              sorted(members), amount, {"layout_hash": rep}))
    return out


def _serial(number: str) -> int | None:
    from lekha.engines.invoice import core_number

    c = core_number(number)
    return int(c) if c.isdigit() and len(c) <= 7 else None
