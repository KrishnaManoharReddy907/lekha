"""Supplier GST compliance scorecard (M10).

Score 0–100 from observable behaviour across all of the firm's clients:
  on-time reporting (appears in the 2B of its own period), lag when late, mismatch rate,
  missing rate, and response to follow-ups. Shrunk towards a neutral prior for suppliers with
  little history so one bad month does not condemn a new supplier.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SupplierStats:
    invoices: int = 0
    on_time: int = 0  # appeared in the 2B of its own period
    late: int = 0
    missing: int = 0  # still not in 2B after a 2B covering its period was imported
    mismatched: int = 0
    total_lag_months: int = 0
    followups: int = 0
    responded: int = 0


def score(s: SupplierStats, prior_weight: int = 5) -> dict:
    n = max(0, s.invoices)
    # Pseudo-counts: a neutral supplier is 80% on time, 10% mismatched.
    on_time_rate = (s.on_time + 0.8 * prior_weight) / (n + prior_weight)
    mismatch_rate = (s.mismatched + 0.1 * prior_weight) / (n + prior_weight)
    missing_rate = (s.missing + 0.05 * prior_weight) / (n + prior_weight)
    avg_lag = s.total_lag_months / s.late if s.late else 0.0
    response = (s.responded + 0.5) / (s.followups + 1)
    raw = (55 * on_time_rate + 20 * (1 - mismatch_rate) + 15 * (1 - missing_rate) + 10 * response
           - min(10.0, 3 * avg_lag))
    value = max(0.0, min(100.0, raw))
    grade = "A" if value >= 85 else "B" if value >= 70 else "C" if value >= 55 else "D"
    return {
        "score": round(value, 1), "grade": grade, "on_time_rate": round(on_time_rate, 3),
        "mismatch_rate": round(mismatch_rate, 3), "missing_rate": round(missing_rate, 3),
        "avg_lag_months": round(avg_lag, 2), "response_rate": round(response, 3), "invoices": n,
    }
