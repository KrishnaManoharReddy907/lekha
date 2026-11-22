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


def test_offline_reader_skips_tax_rates_when_reading_amounts():
    from PIL import Image

    from lekha.ai.offline import extract_offline
    from lekha.ocr.pages import Page, Token

    def tok(i, text, x0, y0, x1, y1):
        return Token(i, text, (x0, y0, x1, y1))

    page = Page(Image.new("RGB", (10, 10)), [
        tok(0, "Taxable Value", 0.60, 0.50, 0.70, 0.51), tok(1, "8,883.00", 0.85, 0.50, 0.92, 0.51),
        tok(2, "IGST @ 5%", 0.60, 0.52, 0.68, 0.53), tok(3, "444.15", 0.86, 0.52, 0.92, 0.53),
        tok(4, "CGST @", 0.60, 0.54, 0.65, 0.55), tok(5, "9%", 0.66, 0.54, 0.68, 0.55), tok(6, "12.60", 0.87, 0.54, 0.92, 0.55),
        tok(7, "Invoice Total (Rs.)", 0.60, 0.58, 0.72, 0.59), tok(8, "9,327.00", 0.85, 0.58, 0.92, 0.59),
    ], source="ocr")
    f = extract_offline([page])["fields"]
    assert f["igst"]["value"] == "444.15"
    assert f["cgst"]["value"] == "12.60"
    assert f["taxable_value"]["value"] == "8883.00" and f["total"]["value"] == "9327.00"
