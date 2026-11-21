"""ITC Recovery Forecaster (N1).

Question: an invoice is missing from GSTR-2B. Will the supplier report it next month, before the
Sec 16(4) deadline, or never?

Model: discrete-time survival. For every past invoice we know the lag (in months) between its own
period and the 2B period in which it first appeared — or that it has not appeared yet (censored).
The hazard h_k = P(appears at lag k | not appeared before) is estimated per supplier, shrunk towards
the firm-wide population hazard with a Beta prior (empirical Bayes), so a new supplier borrows
strength from everyone else while a supplier with a long history speaks for itself.

  P(next 2B)        = h_{c+1}
  P(by deadline D)  = 1 − Π_{k=c+1..D} (1 − h_k)
  P(never)          ≈ Π_{k=c+1..H} (1 − h_k)   with horizon H = 12 months
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

MAX_LAG = 12
PRIOR_STRENGTH = 6.0  # pseudo-observations per lag from the population
MODEL_VERSION = "dt-hazard-eb-1"


@dataclass
class Observation:
    supplier: str
    lag: int | None  # months until it appeared in 2B; None = not yet
    exposure: int  # months observed so far (for censored rows: months since its period)


@dataclass
class HazardTable:
    events: list[float]
    at_risk: list[float]

    def hazard(self, k: int) -> float:
        k = min(k, MAX_LAG)
        e, n = self.events[k], self.at_risk[k]
        return e / n if n > 0 else 0.0


def _table(obs: Iterable[Observation]) -> HazardTable:
    events = [0.0] * (MAX_LAG + 1)
    at_risk = [0.0] * (MAX_LAG + 1)
    for o in obs:
        last = min(o.lag if o.lag is not None else o.exposure, MAX_LAG)
        for k in range(0, last + 1):
            if o.lag is not None and k == o.lag:
                at_risk[k] += 1
                events[k] += 1
                break
            if o.lag is None and k == last:
                # Censored: still at risk at lags < exposure only (the current month is not over).
                break
            at_risk[k] += 1
    return HazardTable(events, at_risk)


class Forecaster:
    def __init__(self, observations: list[Observation]):
        self.population = _table(observations)
        by: dict[str, list[Observation]] = {}
        for o in observations:
            by.setdefault(o.supplier, []).append(o)
        self.by_supplier = {s: _table(v) for s, v in by.items()}
        self.n_by_supplier = {s: len(v) for s, v in by.items()}

    def _pop_hazard(self, k: int) -> float:
        h = self.population.hazard(k)
        # Population fallback when even the firm has no data at this lag: gentle decay.
        if self.population.at_risk[min(k, MAX_LAG)] == 0:
            return max(0.05, 0.6 * (0.6 ** k))
        return min(0.98, max(0.02, h))

    def hazard(self, supplier: str, k: int) -> float:
        k = min(k, MAX_LAG)
        prior = self._pop_hazard(k)
        t = self.by_supplier.get(supplier)
        a, b = prior * PRIOR_STRENGTH, (1 - prior) * PRIOR_STRENGTH
        if not t:
            return prior
        return (a + t.events[k]) / (a + b + t.at_risk[k])

    def predict(self, supplier: str, current_lag: int, deadline_lag: int) -> dict[str, float | str | int]:
        nxt = current_lag + 1
        p_next = self.hazard(supplier, nxt)
        surv = 1.0
        for k in range(nxt, max(nxt, deadline_lag) + 1):
            surv *= 1 - self.hazard(supplier, k)
        p_by_deadline = 1 - surv if deadline_lag >= nxt else 0.0
        never = 1.0
        for k in range(nxt, MAX_LAG + 1):
            never *= 1 - self.hazard(supplier, k)
        # Expected lag given it eventually appears (within horizon).
        surv_k, exp_lag, mass = 1.0, 0.0, 0.0
        for k in range(nxt, MAX_LAG + 1):
            h = self.hazard(supplier, k)
            pk = surv_k * h
            exp_lag += k * pk
            mass += pk
            surv_k *= 1 - h
        expected = exp_lag / mass if mass > 0 else None
        if deadline_lag < nxt:
            rec = "write_off"
        elif p_next >= 0.6:
            rec = "wait"
        elif p_by_deadline >= 0.7:
            rec = "chase"
        else:
            rec = "escalate"
        history = self.n_by_supplier.get(supplier, 0)
        return {"p_next": round(p_next, 4), "p_by_deadline": round(p_by_deadline, 4), "p_never": round(never, 4),
                "expected_lag": round(expected, 2) if expected else None, "recommendation": rec,
                "history": history}


def explain(pred: dict, supplier_name: str, history_note: str) -> str:
    rec = pred["recommendation"]
    head = {
        "wait": "Likely to appear next month — no action needed yet.",
        "chase": "May come late — send the supplier a reminder.",
        "escalate": "Unlikely to appear in time — escalate with the supplier now.",
        "write_off": "The claim deadline has passed for this credit.",
    }[rec]
    return (f"{head} {supplier_name}: {pred['p_next']:.0%} chance in the next GSTR-2B, "
            f"{pred['p_by_deadline']:.0%} before the Sec 16(4) deadline. {history_note}")


def brier(pairs: list[tuple[float, int]]) -> float:
    """Mean squared error of probabilistic predictions (lower is better; 0.25 = coin flip)."""
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else 0.0
