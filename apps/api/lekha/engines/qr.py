"""E-invoice QR decoding and cross-checking.

A GST e-invoice carries a QR code signed by the Invoice Registration Portal (IRP). Its content
is a JWT whose payload contains a JSON string `data` with the seller/buyer GSTIN, document
number, date, total value, item count, main HSN and the IRN. Comparing it with what is printed
on the invoice is a strong tamper / data-entry check. Signature verification is performed when
the IRP public key is configured (LEKHA_IRP_PUBLIC_KEY); otherwise the payload is compared only.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from lekha.engines.dates import parse_date
from lekha.engines.gstin import normalise
from lekha.engines.invoice import InvoiceData, normalise_number
from lekha.engines.money import d


@dataclass
class QrResult:
    kind: str  # einvoice | upi | url | other
    raw: str
    payload: dict[str, Any] | None = None
    signature_verified: bool | None = None


def decode_images(images: list) -> list[QrResult]:
    try:
        import zxingcpp
    except ImportError:  # pragma: no cover
        return []
    out: list[QrResult] = []
    for img in images:
        try:
            results = zxingcpp.read_barcodes(img)
        except Exception:  # noqa: BLE001
            continue
        for r in results:
            if r.text:
                out.append(classify(r.text))
    return out


def _b64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def classify(text: str) -> QrResult:
    t = text.strip()
    if t.count(".") == 2 and len(t) > 100:
        try:
            header_b64, payload_b64, _sig = t.split(".")
            payload = json.loads(_b64url(payload_b64))
            data = payload.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict) and ("SellerGstin" in data or "Irn" in data):
                return QrResult("einvoice", t, data, _verify(t))
        except (ValueError, json.JSONDecodeError):
            pass
    if t.lower().startswith("upi://"):
        return QrResult("upi", t)
    if t.lower().startswith(("http://", "https://")):
        return QrResult("url", t)
    return QrResult("other", t)


def _verify(token: str) -> bool | None:
    key = os.environ.get("LEKHA_IRP_PUBLIC_KEY")
    if not key:
        return None
    try:
        import jwt

        jwt.decode(token, key, algorithms=["RS256"], options={"verify_aud": False})
        return True
    except Exception:  # noqa: BLE001
        return False


def compare(inv: InvoiceData, qr: dict[str, Any]) -> dict[str, Any]:
    """Field-by-field comparison of printed invoice vs signed QR payload."""
    checks: dict[str, dict[str, Any]] = {}

    def add(field: str, printed: Any, signed: Any, ok: bool) -> None:
        checks[field] = {"printed": printed, "qr": signed, "ok": ok}

    if qr.get("SellerGstin"):
        add("supplier_gstin", inv.supplier_gstin, qr["SellerGstin"],
            normalise(inv.supplier_gstin) == normalise(qr["SellerGstin"]))
    if qr.get("BuyerGstin"):
        add("buyer_gstin", inv.buyer_gstin, qr["BuyerGstin"], normalise(inv.buyer_gstin) == normalise(qr["BuyerGstin"]))
    if qr.get("DocNo"):
        add("number", inv.number, qr["DocNo"], normalise_number(inv.number) == normalise_number(str(qr["DocNo"])))
    if qr.get("DocDt"):
        qd = parse_date(qr["DocDt"])
        add("date", inv.date.isoformat() if inv.date else None, qd.isoformat() if qd else qr["DocDt"], inv.date == qd)
    if qr.get("TotInvVal") is not None:
        add("total", str(inv.total), str(qr["TotInvVal"]), abs(inv.total - d(qr["TotInvVal"])) <= d("1"))
    status = "verified" if checks and all(c["ok"] for c in checks.values()) else "mismatch" if checks else "none"
    return {"status": status, "checks": checks, "irn": qr.get("Irn"), "irn_date": qr.get("IrnDt"),
            "main_hsn": qr.get("MainHsnCode"), "item_count": qr.get("ItemCnt")}


def make_einvoice_qr_text(data: dict[str, Any]) -> str:
    """Build an (unsigned) e-invoice-style JWT. Used by the synthetic invoice generator and tests."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps({"data": json.dumps(data), "iss": "NIC Sandbox"}).encode()).rstrip(
        b"=").decode()
    sig = base64.urlsafe_b64encode(b"synthetic-signature-not-valid").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"
