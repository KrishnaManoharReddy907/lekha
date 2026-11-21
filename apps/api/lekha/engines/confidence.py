"""Field confidence fusion.

Each extracted field gets several independent signals:
  certainty   — what the model itself reported (high / medium / low)
  grounded    — does the cited OCR text actually contain the value? (1 yes, 0 no, 0.5 no tokens cited)
  validator   — did deterministic checks pass for this field? (1 pass, 0.5 warning, 0 error)
  qr          — agreement with the signed e-invoice QR (1 agree, 0 disagree, absent if no QR)
  memory      — this supplier previously needed a human correction on this field (0) or not (1)
  ocr_conf    — mean OCR confidence of cited tokens
These are combined with a logistic model. The weights below are the defaults; `calibrate` refits
them from benchmark results (see lekha.bench.evaluate) so the score means "probability correct".
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lekha.engines.gstin import normalise as norm_gstin
from lekha.engines.invoice import normalise_number

CERTAINTY = {"high": 1.0, "medium": 0.5, "low": 0.0}
CRITICAL_FIELDS = ("supplier_gstin", "buyer_gstin", "number", "date", "taxable_value", "total")
WEIGHTS_FILE = Path(__file__).with_name("confidence_weights.json")

DEFAULT_WEIGHTS = {
    "bias": -2.2,
    "certainty": 2.4,
    "grounded": 1.6,
    "validator": 2.0,
    "qr": 1.8,
    "memory": 0.8,
    "ocr_conf": 1.0,
}


def load_weights() -> dict[str, float]:
    if WEIGHTS_FILE.exists():
        try:
            return {**DEFAULT_WEIGHTS, **json.loads(WEIGHTS_FILE.read_text())}
        except (OSError, ValueError):
            pass
    return dict(DEFAULT_WEIGHTS)


@dataclass
class FieldSignals:
    certainty: float
    grounded: float
    validator: float
    qr: float | None
    memory: float
    ocr_conf: float

    def vector(self) -> dict[str, float]:
        return {
            "certainty": self.certainty,
            "grounded": self.grounded,
            "validator": self.validator,
            # A missing QR is neutral: centre it so it neither helps nor hurts.
            "qr": 0.5 if self.qr is None else self.qr,
            "memory": self.memory,
            "ocr_conf": self.ocr_conf,
        }


def score(signals: FieldSignals, weights: dict[str, float] | None = None) -> float:
    w = weights or load_weights()
    z = w["bias"] + sum(w[k] * v for k, v in signals.vector().items())
    return 1 / (1 + math.exp(-z))


def _canon(field: str, value: str) -> str:
    v = value.strip()
    if field.endswith("gstin"):
        return norm_gstin(v)
    if field == "number":
        return normalise_number(v)
    if field in ("taxable_value", "igst", "cgst", "sgst", "cess", "total", "round_off"):
        return re.sub(r"[^\d.]", "", v).rstrip("0").rstrip(".") or "0"
    return re.sub(r"\s+", "", v).lower()


def grounded(field: str, value: str | None, token_texts: list[str]) -> float:
    if value is None:
        return 0.5
    if not token_texts:
        return 0.5
    joined = " ".join(token_texts)
    target = _canon(field, value)
    if not target:
        return 0.5
    if field == "date":
        # Dates are normalised (YYYY-MM-DD) and rarely appear verbatim; accept day+month digits present.
        digits = re.sub(r"\D", "", joined)
        _y, m, d = value.split("-") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else ("", "", "")
        return 1.0 if d and (d.lstrip("0") in digits and m.lstrip("0") in digits) else 0.3
    hay = _canon(field, joined) if field not in ("supplier_name", "buyer_name") else joined.lower().replace(" ", "")
    if field in ("taxable_value", "igst", "cgst", "sgst", "cess", "total", "round_off"):
        nums = {_canon(field, n) for n in re.findall(r"[\d,]+(?:\.\d+)?", joined)}
        return 1.0 if target in nums else 0.0
    return 1.0 if target in hay else 0.0


def invoice_confidence(field_scores: dict[str, float]) -> float:
    crit = [field_scores[f] for f in CRITICAL_FIELDS if f in field_scores]
    return round(min(crit), 4) if crit else 0.0


def calibrate(rows: list[tuple[dict[str, float], int]], epochs: int = 400, lr: float = 0.3) -> dict[str, Any]:
    """Fit logistic weights by gradient descent on (signal vector, was_correct) pairs."""
    keys = [k for k in DEFAULT_WEIGHTS if k != "bias"]
    w = dict(DEFAULT_WEIGHTS)
    n = max(1, len(rows))
    for _ in range(epochs):
        grad = dict.fromkeys(w, 0.0)
        for x, y in rows:
            z = w["bias"] + sum(w[k] * x[k] for k in keys)
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            err = p - y
            grad["bias"] += err
            for k in keys:
                grad[k] += err * x[k]
        for k in w:
            w[k] -= lr * grad[k] / n
    return w
