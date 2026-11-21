"""Benchmark harness (M15): measure extraction accuracy on synthetic GST invoices with ground truth.

Reports field-level accuracy for each document kind (digital PDF, clean scan, phone photo) for:
  offline   — OCR + rules, no AI
  ai        — the configured AI provider (raw model output)
  ai+qr     — AI output after signed-QR reconciliation (what Lekha actually stores)
It also collects (signals, correct?) pairs to calibrate field confidence (engines/confidence.py) so a
displayed 90% means "right about 90% of the time".
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from lekha.ai.client import ai_enabled, provider
from lekha.ai.extract import extract_with_claude
from lekha.ai.offline import extract_offline
from lekha.engines import confidence as C
from lekha.engines import gstin as G
from lekha.engines import qr as Q
from lekha.engines.invoice import normalise_number
from lekha.engines.money import d
from lekha.ocr import pages as P
from lekha.services.pipeline import _extracted_to_invoice_dict, _run_cpu

FIELDS = ["supplier_gstin", "buyer_gstin", "number", "date", "place_of_supply", "taxable_value", "igst", "cgst", "sgst",
          "total", "irn"]
MONEY = {"taxable_value", "igst", "cgst", "sgst", "total"}


def _same(field: str, got: Any, want: Any) -> bool:
    if want in (None, "") and got in (None, "", "0", "0.00"):
        return True
    if got in (None, ""):
        return False
    if field in MONEY:
        try:
            return abs(d(got) - d(want)) <= d("1")
        except ValueError:
            return False
    if field.endswith("gstin"):
        return G.normalise(str(got)) == G.normalise(str(want))
    if field == "number":
        return normalise_number(str(got)) == normalise_number(str(want))
    if field == "irn":
        return str(got).strip().lower() == str(want).strip().lower()
    return str(got).strip() == str(want).strip()


def _score(fields: dict[str, Any], truth: dict[str, Any]) -> dict[str, bool]:
    return {f: _same(f, fields.get(f), truth.get(f)) for f in FIELDS}


async def _one(path: Path, item: dict[str, Any], use_ai: bool, sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        data = path.read_bytes()
        mime = P.sniff_mime(data)
        t0 = time.perf_counter()
        pages = await _run_cpu(P.process, data, mime)
        ocr_ms = int((time.perf_counter() - t0) * 1000)
        qrs = await _run_cpu(Q.decode_images, [p.image for p in pages])
        einv = next((q for q in qrs if q.kind == "einvoice"), None)
        truth = item["truth"]
        buyer = {truth["buyer_gstin"]}
        out: dict[str, Any] = {"file": item["file"], "kind": item["kind"], "template": item["template"], "ocr_ms": ocr_ms,
                               "qr": bool(einv)}
        off = _extracted_to_invoice_dict(extract_offline(pages, buyer))
        out["offline"] = _score(off, truth)
        if use_ai:
            t1 = time.perf_counter()
            try:
                raw, res = await extract_with_claude(pages, [])
                out["ai_ms"] = int((time.perf_counter() - t1) * 1000)
                out["model"] = res.model
                ai = _extracted_to_invoice_dict(raw)
                out["ai"] = _score(ai, truth)
                fixed = dict(ai)
                if einv and einv.payload:
                    fixed, _src, corr = Q.reconcile_with_qr(fixed, einv.payload, P.full_text(pages))
                    out["qr_corrections"] = len(corr)
                out["ai_qr"] = _score(fixed, truth)
                tokens = {t.id: t for p in pages for t in p.tokens}
                cal = []
                for f in FIELDS:
                    fr = raw["fields"].get(f if f != "date" else "date") or {}
                    toks = [tokens[i] for i in fr.get("token_ids") or [] if i in tokens]
                    sig = C.FieldSignals(
                        certainty=C.CERTAINTY.get(fr.get("certainty", "low"), 0.0),
                        grounded=C.grounded(f, fr.get("value"), [t.text for t in toks]),
                        validator=1.0, qr=None if not einv else float(_same(f, fixed.get(f), ai.get(f))),
                        memory=1.0, ocr_conf=sum(t.conf for t in toks) / len(toks) if toks else 0.6)
                    cal.append((sig.vector(), int(out["ai"][f])))
                out["calibration"] = cal
            except Exception as e:
                out["ai_error"] = f"{type(e).__name__}: {e}"[:300]
        return out


def _agg(results: list[dict[str, Any]], key: str) -> dict[str, Any]:
    rows = [r for r in results if key in r]
    if not rows:
        return {}
    per_field = {f: sum(r[key][f] for r in rows) / len(rows) for f in FIELDS}
    by_kind: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(sum(r[key].values()) / len(FIELDS))
    all_correct = sum(all(r[key].values()) for r in rows) / len(rows)
    return {"n": len(rows), "field_accuracy": round(sum(per_field.values()) / len(FIELDS), 4),
            "documents_fully_correct": round(all_correct, 4),
            "per_field": {k: round(v, 4) for k, v in per_field.items()},
            "by_kind": {k: round(statistics.mean(v), 4) for k, v in by_kind.items()}}


async def evaluate(data_dir: str, limit: int, offline_only: bool) -> dict[str, Any]:
    root = Path(data_dir)
    manifest = json.loads((root / "manifest.json").read_text())
    items = manifest["items"][:limit]
    use_ai = ai_enabled() and not offline_only
    sem = asyncio.Semaphore(3)
    results = await asyncio.gather(*[_one(root / it["file"], it, use_ai, sem) for it in items])
    report = {"date": date.today().isoformat(), "documents": len(items), "provider": provider() if use_ai else None,
              "offline": _agg(results, "offline"), "ai": _agg(results, "ai"), "ai_qr": _agg(results, "ai_qr"),
              "ocr_ms_median": statistics.median(r["ocr_ms"] for r in results),
              "ai_ms_median": statistics.median([r["ai_ms"] for r in results if "ai_ms" in r] or [0]),
              "ai_errors": sum(1 for r in results if "ai_error" in r),
              "qr_documents": sum(1 for r in results if r["qr"]),
              "qr_corrections": sum(r.get("qr_corrections", 0) for r in results)}
    cal = [pair for r in results for pair in r.get("calibration", [])]
    if len(cal) >= 50:
        w = C.calibrate([(x, y) for x, y in cal])
        report["calibration"] = {"pairs": len(cal), "weights": {k: round(v, 3) for k, v in w.items()}}
        before = _brier(cal, C.DEFAULT_WEIGHTS)
        after = _brier(cal, w)
        report["calibration"]["brier_before"] = round(before, 4)
        report["calibration"]["brier_after"] = round(after, 4)
        if after < before:
            C.WEIGHTS_FILE.write_text(json.dumps(report["calibration"]["weights"], indent=2))
    return {"report": report, "results": results}


def _brier(pairs: list[tuple[dict[str, float], int]], w: dict[str, float]) -> float:
    import math

    tot = 0.0
    for x, y in pairs:
        z = w["bias"] + sum(w[k] * x[k] for k in x)
        p = 1 / (1 + math.exp(-max(-30, min(30, z))))
        tot += (p - y) ** 2
    return tot / len(pairs)


def to_markdown(rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
    def pct(x: float | None) -> str:
        return "—" if x is None else f"{x * 100:.1f}%"

    kinds = ["pdf", "clean", "photo"]
    kind_label = {"pdf": "Digital PDF", "clean": "Clean scan", "photo": "Phone photo"}
    counts = {k: sum(1 for r in results if r["kind"] == k) for k in kinds}
    models = sorted({r.get("model") for r in results if r.get("model")})
    lines = [
        "# Benchmark",
        "",
        f"Run on {rep['date']} over **{rep['documents']} synthetic GST invoices** generated by `lekha bench` "
        f"({', '.join(f'{counts[k]} {kind_label[k].lower()}s' for k in kinds)}; {rep['qr_documents']} carry a signed "
        "e-invoice QR). Each has exact ground truth. Reproduce with `uv run lekha bench --n 60 --seed 2026 && "
        "uv run lekha bench-eval`.",
        "",
        f"AI provider: **{rep['provider'] or 'none (offline only)'}**" + (f" — models that served requests: "
                                                                         f"{', '.join(models)}" if models else "") + ".",
        "",
        "## Field accuracy",
        "",
        "Share of key fields exactly right (GSTINs, invoice number, date, place of supply, taxable value, IGST/CGST/SGST, "
        "total, IRN; amounts within ₹1).",
        "",
        "| Reader | Documents scored | All documents | " + " | ".join(kind_label[k] for k in kinds) + " | Documents 100% correct |",
        "|---|---|---|" + "---|" * len(kinds) + "---|",
    ]
    for key, label in (("offline", "Offline (OCR + rules, no AI)"), ("ai", "AI (raw model output)"),
                       ("ai_qr", "AI + signed-QR reconciliation (stored by Lekha)")):
        a = rep.get(key) or {}
        if not a:
            continue
        lines.append(f"| {label} | {a['n']} | **{pct(a['field_accuracy'])}** | " +
                     " | ".join(pct(a["by_kind"].get(k)) for k in kinds) + f" | {pct(a['documents_fully_correct'])} |")
    best = rep.get("ai_qr") or rep.get("ai") or rep.get("offline")
    if best:
        lines += ["", "### Per field (best reader)", "", "| Field | Accuracy |", "|---|---|"]
        lines += [f"| `{f}` | {pct(v)} |" for f, v in best["per_field"].items()]
    lines += ["", "## Speed", "",
              f"- OCR median: {rep['ocr_ms_median'] / 1000:.1f} s per document (laptop CPU, process pool).",
              f"- AI extraction median: {rep['ai_ms_median'] / 1000:.1f} s per document (free tier, includes failover)."
              if rep.get("ai") else "- AI extraction: not run.",
              f"- AI unavailable for {rep['ai_errors']} of {rep['documents']} documents (every free-tier model rate-limited or "
              "timing out at the time of the run). AI rows are scored only on documents the AI answered; in the product, "
              "those documents fall back to the offline reader automatically.",
              f"- Signed-QR corrections applied: {rep['qr_corrections']}."]
    if rep.get("calibration"):
        c = rep["calibration"]
        lines += ["", "## Confidence calibration", "",
                  f"Field confidence weights were refitted on {c['pairs']} (signals, correct?) pairs. Brier score "
                  f"{c['brier_before']} → {c['brier_after']} (lower is better)."
                  + (" The new weights were saved to `engines/confidence_weights.json`." if c["brier_after"] < c["brier_before"] else "")]
    lines += ["", "## Honest limits", "",
              "- Synthetic invoices use four layouts and generated degradation; real bills are more varied (handwriting, "
              "stamps, multi-page, regional languages). Treat these numbers as a regression baseline, not a guarantee.",
              "- Free-tier AI capacity varies by hour; failover to smaller models can lower accuracy at busy times.",
              "- Every field still passes deterministic GST validation and, by default, human review."]
    return "\n".join(lines) + "\n"


async def run_cli(data_dir: str, limit: int, offline_only: bool) -> None:
    out = await evaluate(data_dir, limit, offline_only)
    rep = out["report"]
    Path(data_dir, "results.json").write_text(json.dumps(out, indent=1, default=str))
    from lekha.core.config import API_ROOT

    (API_ROOT.parent.parent / "docs" / "BENCHMARK.md").write_text(to_markdown(rep, out["results"]), encoding="utf-8")
    print(json.dumps(rep, indent=2))
