import random
from datetime import date, timedelta
from decimal import Decimal

from lekha.engines.forecast import Forecaster, Observation, brier
from lekha.engines.graph import Edge, find_cycles, shell_scores, template_reuse
from lekha.engines.ims import ImsInput, ims_deadline, recommend
from lekha.engines.risk import RiskInvoice, assess, ledger_totals
from lekha.engines.rules import default_rulebook
from lekha.engines.supplier_score import SupplierStats, score
from lekha.engines.table4 import Amount, Item, build

BOOK = default_rulebook()
D = Decimal


# --- IMS ---------------------------------------------------------------------------------------

def test_ims_accept_matched():
    r = recommend(ImsInput("invoice", True, "matched", itc_books=D(180), itc_2b=D(180), invoice_status="approved"))
    assert r.action == "accept" and r.reasons == ["MATCHED"]


def test_ims_reject_over_reported_and_wrong_head():
    assert recommend(ImsInput("invoice", True, "mismatch", ["TAX_AMOUNT_DIFFERENT"], D(100), D(150), "approved")).action == "reject"
    assert recommend(ImsInput("invoice", True, "mismatch", ["TAX_HEAD_DIFFERENT"], D(100), D(100), "approved")).reasons == ["WRONG_TAX_HEAD"]


def test_ims_pending_cases():
    assert recommend(ImsInput("invoice", True, "missing_in_books", record_age_days=10)).action == "pending"
    assert recommend(ImsInput("invoice", True, "missing_in_books", record_age_days=90)).action == "reject"
    assert recommend(ImsInput("invoice", True, "matched", invoice_status="approved", goods_received=False)).reasons == ["GOODS_NOT_RECEIVED"]
    assert recommend(ImsInput("invoice", True, "matched", invoice_status="approved", supplier_risk=0.9)).reasons == ["SUPPLIER_HIGH_RISK"]


def test_ims_credit_note_and_blocked():
    assert recommend(ImsInput("credit_note", True, "matched")).action == "accept"
    r = recommend(ImsInput("invoice", True, "matched", itc_books=D(5), itc_2b=D(5), invoice_status="approved", blocked_17_5=True))
    assert r.action == "accept" and "BLOCKED_17_5" in r.reasons


def test_ims_deadline():
    assert ims_deadline("102025") == date(2025, 11, 13)
    assert ims_deadline("122025") == date(2026, 1, 13)


# --- Risk --------------------------------------------------------------------------------------

def inv(**kw):
    base = dict(id="i1", supplier_gstin="36AAAAA0000A1Z5", supplier_name="Raja Glass", number="RG/221",
                invoice_date=date(2025, 6, 10), total=D(118000), itc=D(18000))
    base.update(kw)
    return RiskInvoice(**base)


def kinds(r):
    return {x.kind for x in r}


def test_rule37_upcoming_and_overdue():
    upcoming = assess(inv(), BOOK, today=date(2025, 11, 20))  # 180 days = 7 Dec 2025
    r37 = next(x for x in upcoming if x.kind == "R37_PAYMENT")
    assert r37.due_date == date(2025, 12, 7) and r37.amount == D(18000) and r37.interest == 0
    overdue = assess(inv(paid=D(59000)), BOOK, today=date(2026, 1, 6))
    r = next(x for x in overdue if x.kind == "R37_PAYMENT")
    assert r.amount == D("9000.00") and r.interest > 0 and r.severity == "critical"
    assert "R37_PAYMENT" not in kinds(assess(inv(paid=D(118000)), BOOK, today=date(2026, 1, 6)))


def test_not_in_2b_and_value_mismatch_only_after_2b_import():
    assert "NOT_IN_2B" not in kinds(assess(inv(match_status="missing_in_2b"), BOOK, date(2025, 7, 1)))
    k = kinds(assess(inv(match_status="missing_in_2b", latest_2b_period_covered=True, paid=D(118000)), BOOK, date(2025, 7, 20)))
    assert "NOT_IN_2B" in k
    vm = assess(inv(match_status="mismatch", mismatch_types=["TAX_AMOUNT_DIFFERENT"], itc_2b=D(16200),
                    latest_2b_period_covered=True, paid=D(118000)), BOOK, date(2025, 7, 20))
    v = next(x for x in vm if x.kind == "VALUE_MISMATCH")
    assert v.amount == D("1800.00")


def test_msme_43bh():
    r = assess(inv(supplier_msme="micro", invoice_date=date(2025, 10, 1)), BOOK, date(2025, 11, 10))
    m = next(x for x in r if x.kind == "MSME_43BH")
    assert m.due_date == date(2025, 11, 15)
    r2 = assess(inv(supplier_msme="small", supplier_has_agreement=False, invoice_date=date(2025, 10, 1)), BOOK, date(2025, 10, 10))
    assert next(x for x in r2 if x.kind == "MSME_43BH").due_date == date(2025, 10, 16)


def test_16_4_blocked_37a_not_received():
    r = assess(inv(invoice_date=date(2024, 12, 1), paid=D(118000), blocked_amount=D(1000), supplier_3b_filed=False,
                   goods_received=False), BOOK, date(2025, 11, 1))
    k = kinds(r)
    assert {"S16_4_DEADLINE", "S17_5_BLOCKED", "R37A_SUPPLIER_3B", "NOT_RECEIVED"} <= k
    assert next(x for x in r if x.kind == "S16_4_DEADLINE").due_date == date(2025, 11, 30)
    totals = ledger_totals(r)
    assert D(totals["itc_at_risk"]) > 0


def test_rejected_invoices_carry_no_risk():
    assert assess(inv(status="rejected"), BOOK, date(2026, 1, 1)) == []


# --- Table 4 -----------------------------------------------------------------------------------

def test_table4_rows_and_net():
    items = [
        Item("rec:1", "A", Amount(igst=D(18000)), "4A5"),
        Item("rec:2", "B", Amount(cgst=D(900), sgst=D(900)), "4A5", reversals=[("4B1", D(1), "17(5) food")]),
        Item("rec:3", "C", Amount(igst=D(5000)), "4A5", reversals=[("4B2", D("0.5"), "Rule 37 unpaid half")]),
        Item("rec:4", "CN", Amount(igst=D(1000)), "4A5", sign=-1),
        Item("inv:5", "RCM", Amount(cgst=D(250), sgst=D(250)), "4A3"),
        Item("rec:6", "Old", Amount(igst=D(700)), "none", ineligible_reason="Sec 16(4)"),
        Item("inv:7", "Paid now", Amount(igst=D(4000)), "none", reclaim=D(1)),
    ]
    t = build(items)
    assert t.cells["4A5"].total == D(18000 + 1800 + 5000 - 1000)
    assert t.cells["4B1"].total == D(1800) and t.cells["4B2"].total == D(2500)
    assert t.cells["4C"].total == t.cells["4A5"].total + t.cells["4A3"].total - D(4300)
    assert t.cells["4D2"].total == D(700) and t.cells["4D1"].total == D(4000)
    assert len(t.sources["4A5"]) == 4 and t.to_dict()["cells"]["4C"]["label"].startswith("4C")


# --- Forecaster --------------------------------------------------------------------------------

def test_forecaster_learns_supplier_behaviour_and_beats_coin_flip():
    rng = random.Random(1)
    obs = []
    # Prompt supplier: almost always lag 0. Late supplier: lag 2-3. Bad supplier: often never.
    for _ in range(40):
        obs.append(Observation("PROMPT", 0 if rng.random() < 0.95 else 1, 6))
        obs.append(Observation("LATE", rng.choice([2, 2, 3]), 6))
        obs.append(Observation("BAD", None if rng.random() < 0.6 else 1, 6))
    f = Forecaster(obs)
    late = f.predict("LATE", current_lag=1, deadline_lag=8)
    bad = f.predict("BAD", current_lag=1, deadline_lag=8)
    prompt_new = f.predict("PROMPT", current_lag=0, deadline_lag=8)
    # Empirical conditional rate at lag 2 in this sample is 20/40 = 0.5; shrinkage keeps it close.
    assert 0.4 < late["p_next"] < 0.6 and late["p_by_deadline"] > 0.9
    assert bad["p_never"] > late["p_never"] and bad["recommendation"] in ("chase", "escalate")
    assert f.predict("NEWCOMER", 0, 8)["history"] == 0
    assert prompt_new["expected_lag"] is not None
    # Backtest on fresh data from the same behaviours: Brier score well below 0.25.
    pairs = []
    for _ in range(200):
        sup = rng.choice(["LATE", "BAD"])
        truth = (rng.choice([2, 2, 3]) == 2) if sup == "LATE" else (rng.random() >= 0.6 and False)
        pairs.append((f.predict(sup, 1, 8)["p_next"], int(truth)))
    assert brier(pairs) < 0.25


# --- Graph -------------------------------------------------------------------------------------

def test_cycle_detection():
    e = [Edge("A", "B", D(900000), date(2025, 8, 1), "1"), Edge("B", "C", D(880000), date(2025, 8, 5), "2"),
         Edge("C", "A", D(870000), date(2025, 8, 9), "3"), Edge("X", "A", D(5000), date(2025, 8, 9), "9")]
    sig = find_cycles(e)
    assert len(sig) == 1 and set(sig[0].members) == {"A", "B", "C"} and sig[0].amount == D(870000)


def test_shell_and_template_reuse():
    today = date(2025, 11, 1)
    edges = [Edge(f"C{i}", f"CL{i}", D(20000), today - timedelta(days=200), str(100 + i)) for i in range(20)]
    edges += [Edge("SHELL", b, D(900000), today - timedelta(days=d), str(n), round_amount=True)
              for b, d, n in (("CL1", 40, 12), ("CL2", 20, 13), ("CL3", 5, 14))]
    sig = shell_scores(edges, {"SHELL": today - timedelta(days=45)}, today, {f"CL{i}" for i in range(20)})
    assert [s.members for s in sig] == [["SHELL"]]
    same = "f0f0f0f0f0f0f0f0"
    tpl = [Edge(f"T{i}", "CL1", D(10000), today, "1", layout_hash=same) for i in range(4)]
    assert template_reuse(tpl)[0].members == ["T0", "T1", "T2", "T3"]


# --- Supplier score ----------------------------------------------------------------------------

def test_supplier_score_ordering():
    good = score(SupplierStats(invoices=30, on_time=29, late=1, total_lag_months=1, followups=1, responded=1))
    bad = score(SupplierStats(invoices=30, on_time=10, late=10, missing=10, mismatched=8, total_lag_months=25))
    new = score(SupplierStats(invoices=0))
    assert good["score"] > new["score"] > bad["score"] and good["grade"] == "A" and bad["grade"] == "D"
