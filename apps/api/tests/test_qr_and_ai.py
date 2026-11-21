from lekha.ai.gemini import _parts, _strip_unsupported
from lekha.engines.qr import classify, make_einvoice_qr_text, reconcile_with_qr

QR = {"SellerGstin": "29FBLCC2611D1ZR", "BuyerGstin": "36MVRCR9190R1ZI", "DocNo": "SB-1602", "DocDt": "03/05/2026",
      "TotInvVal": 8733435.0, "Irn": "a" * 64}


def test_qr_jwt_roundtrip():
    r = classify(make_einvoice_qr_text(QR))
    assert r.kind == "einvoice" and r.payload["DocNo"] == "SB-1602"
    assert classify("upi://pay?pa=x@ybl").kind == "upi"


def test_qr_fills_gaps():
    out, src, corr = reconcile_with_qr({"supplier_gstin": None, "total": None}, QR, "")
    assert out["supplier_gstin"] == QR["SellerGstin"] and out["total"] == "8733435.00"
    assert src["supplier_gstin"] == "qr" and corr == []


def test_qr_corrects_misread_gstin_even_when_checksum_passes():
    # Real case from a degraded photo: the misread GSTIN happens to have a valid checksum.
    out, _src, corr = reconcile_with_qr({"supplier_gstin": "33FBLCG2611D1ZY"}, QR, "nothing useful")
    assert out["supplier_gstin"] == QR["SellerGstin"] and corr[0]["why"] == "near-identical misread"


def test_qr_corrects_invalid_gstin():
    out, _src, corr = reconcile_with_qr({"supplier_gstin": "07ZZZZZ0000Z1ZZ"}, QR, "")
    assert out["supplier_gstin"] == QR["SellerGstin"] and corr[0]["why"] == "invalid checksum"


def test_forged_gstin_is_not_overwritten():
    from lekha.engines import gstin as G

    forged = G.make("27", "ABCDE1234F")  # valid, but a completely different business
    out, _src, corr = reconcile_with_qr({"supplier_gstin": forged}, QR, "")
    assert out["supplier_gstin"] == forged and corr == []


def test_qr_corrects_value_printed_on_page():
    out, _src, corr = reconcile_with_qr({"number": "SB-1662"}, QR, "Invoice No. SB-1602 Dated 03-05-2026")
    assert out["number"] == "SB-1602" and corr


def test_genuine_total_mismatch_is_kept():
    # Printed total is clearly different and the QR's total is nowhere on the page → possible tampering.
    out, _src, corr = reconcile_with_qr({"total": "9733435.00"}, QR, "Invoice Total 97,33,435.00")
    assert out["total"] == "9733435.00" and corr == []


def test_gemini_parts_and_schema():
    parts = _parts([{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAA"}},
                    {"type": "text", "text": "hi"}])
    assert parts == [{"inlineData": {"mimeType": "image/jpeg", "data": "AAA"}}, {"text": "hi"}]
    s = _strip_unsupported({"type": "object", "additionalProperties": False, "properties": {
        "a": {"type": "object", "additionalProperties": False}}})
    assert "additionalProperties" not in s and "additionalProperties" not in s["properties"]["a"]
