"""GSTR-2B parsing (portal JSON and Excel) into normalised records.

GSTR-2B is the GST portal's monthly, static statement of input tax credit available to a buyer,
built from what suppliers reported in GSTR-1/IFF/GSTR-5. The downloaded JSON nests documents under
data.docdata.{b2b, b2ba, cdnr, cdnra, isd, impg} with supplier-wise lists. Field names follow the
portal (ctin, trdnm, inum, dt, val, pos, rev, itcavl, rsn, items[txval, igst, cgst, sgst, cess]).
The parser is deliberately tolerant: wrappers and optional fields vary between portal versions.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from lekha.engines.dates import parse_date
from lekha.engines.gstin import normalise as norm_gstin
from lekha.engines.invoice import normalise_number
from lekha.engines.money import ZERO, d


class Gstr2bError(ValueError):
    pass


@dataclass
class Record:
    section: str
    doc_type: str
    supplier_gstin: str
    supplier_name: str | None
    number: str
    invoice_date: date | None
    place_of_supply: str | None
    reverse_charge: bool
    taxable_value: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    cess: Decimal
    total: Decimal
    itc_available: bool = True
    itc_unavailable_reason: str | None = None
    supplier_filing_date: date | None = None
    supplier_period: str | None = None
    irn: str | None = None
    original_number: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def number_norm(self) -> str:
        return normalise_number(self.number)

    @property
    def itc(self) -> Decimal:
        return self.igst + self.cgst + self.sgst + self.cess

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["number_norm"] = self.number_norm
        return out


@dataclass
class Statement:
    gstin: str | None
    period: str | None  # MMYYYY
    generated_on: date | None
    records: list[Record]

    def summary(self) -> dict[str, Any]:
        by: dict[str, dict[str, Any]] = {}
        for r in self.records:
            s = by.setdefault(r.section, {"count": 0, "taxable": ZERO, "itc": ZERO})
            s["count"] += 1
            s["taxable"] += r.taxable_value * (-1 if r.doc_type == "credit_note" else 1)
            s["itc"] += r.itc * (-1 if r.doc_type == "credit_note" else 1)
        avail = sum((r.itc * (-1 if r.doc_type == "credit_note" else 1) for r in self.records if r.itc_available), ZERO)
        unavail = sum((r.itc for r in self.records if not r.itc_available), ZERO)
        return {"sections": {k: {**v, "taxable": str(v["taxable"]), "itc": str(v["itc"])} for k, v in by.items()},
                "itc_available": str(avail), "itc_unavailable": str(unavail), "count": len(self.records)}


def _yes(v: Any) -> bool:
    return str(v or "").strip().upper() in ("Y", "YES", "TRUE", "1")


def _items_sum(items: list[dict[str, Any]] | None, doc: dict[str, Any]) -> tuple[Decimal, ...]:
    if items:
        tx = sum((d(i.get("txval")) for i in items), ZERO)
        ig = sum((d(i.get("igst")) for i in items), ZERO)
        cg = sum((d(i.get("cgst")) for i in items), ZERO)
        sg = sum((d(i.get("sgst")) for i in items), ZERO)
        cs = sum((d(i.get("cess")) for i in items), ZERO)
        return tx, ig, cg, sg, cs
    return d(doc.get("txval")), d(doc.get("igst")), d(doc.get("cgst")), d(doc.get("sgst")), d(doc.get("cess"))


def _find_docdata(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        if "docdata" in obj and isinstance(obj["docdata"], dict):
            return obj["docdata"]
        if any(k in obj for k in ("b2b", "cdnr")) and isinstance(obj.get("b2b", obj.get("cdnr")), list):
            return obj
        for v in obj.values():
            found = _find_docdata(v)
            if found is not None:
                return found
    return None


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj and not isinstance(obj[key], (dict, list)):
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def parse_json(raw: bytes | str | dict[str, Any]) -> Statement:
    try:
        obj = raw if isinstance(raw, dict) else json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Gstr2bError("This is not a valid GSTR-2B JSON file.") from exc
    docdata = _find_docdata(obj)
    if docdata is None:
        raise Gstr2bError("No GSTR-2B document data found (expected data.docdata.b2b …).")
    records: list[Record] = []

    def supplier_iter(section: str):
        for sup in docdata.get(section) or []:
            yield sup, norm_gstin(sup.get("ctin")), sup.get("trdnm"), parse_date(sup.get("supfildt")), sup.get("supprd")

    for section in ("b2b", "b2ba"):
        for sup, ctin, name, fil, prd in supplier_iter(section):
            for inv in sup.get("inv") or []:
                tx, ig, cg, sg, cs = _items_sum(inv.get("items"), inv)
                records.append(Record(
                    section=section, doc_type="invoice", supplier_gstin=ctin, supplier_name=name,
                    number=str(inv.get("inum") or ""), invoice_date=parse_date(inv.get("dt")),
                    place_of_supply=str(inv.get("pos") or "").zfill(2) or None, reverse_charge=_yes(inv.get("rev")),
                    taxable_value=tx, igst=ig, cgst=cg, sgst=sg, cess=cs,
                    total=d(inv.get("val")) if inv.get("val") not in (None, "") else tx + ig + cg + sg + cs,
                    itc_available=_yes(inv.get("itcavl")) if "itcavl" in inv else True,
                    itc_unavailable_reason=inv.get("rsn") or None, supplier_filing_date=fil, supplier_period=prd,
                    irn=inv.get("irn") or None, original_number=inv.get("oinum") or None, raw=inv,
                ))
    for section in ("cdnr", "cdnra"):
        for sup, ctin, name, fil, prd in supplier_iter(section):
            for nt in sup.get("nt") or []:
                tx, ig, cg, sg, cs = _items_sum(nt.get("items"), nt)
                typ = str(nt.get("typ") or nt.get("ntty") or "C").upper()
                records.append(Record(
                    section=section, doc_type="credit_note" if typ.startswith("C") else "debit_note",
                    supplier_gstin=ctin, supplier_name=name, number=str(nt.get("ntnum") or nt.get("nt_num") or ""),
                    invoice_date=parse_date(nt.get("dt")), place_of_supply=str(nt.get("pos") or "").zfill(2) or None,
                    reverse_charge=_yes(nt.get("rev")), taxable_value=tx, igst=ig, cgst=cg, sgst=sg, cess=cs,
                    total=d(nt.get("val")) if nt.get("val") not in (None, "") else tx + ig + cg + sg + cs,
                    itc_available=_yes(nt.get("itcavl")) if "itcavl" in nt else True,
                    itc_unavailable_reason=nt.get("rsn") or None, supplier_filing_date=fil, supplier_period=prd,
                    irn=nt.get("irn") or None, original_number=nt.get("ontnum") or None, raw=nt,
                ))
    for imp in docdata.get("impg") or []:
        records.append(Record(
            section="impg", doc_type="invoice", supplier_gstin="IMPORT", supplier_name=imp.get("portcode"),
            number=str(imp.get("boenum") or ""), invoice_date=parse_date(imp.get("boedt")), place_of_supply=None,
            reverse_charge=False, taxable_value=d(imp.get("txval")), igst=d(imp.get("igst")), cgst=ZERO, sgst=ZERO,
            cess=d(imp.get("cess")), total=d(imp.get("txval")) + d(imp.get("igst")) + d(imp.get("cess")), raw=imp,
        ))
    gstin = _find_key(obj, "gstin")
    period = _find_key(obj, "rtnprd")
    gendt = parse_date(_find_key(obj, "gendt"))
    return Statement(gstin=norm_gstin(gstin) if gstin else None, period=str(period) if period else None,
                     generated_on=gendt, records=records)


# --- Excel ------------------------------------------------------------------------------------

_HEADERS = {
    "supplier_gstin": [r"gstin\s*of\s*supplier", r"supplier\s*gstin", r"^gstin", r"ctin"],
    "supplier_name": [r"trade.*name", r"legal\s*name", r"supplier\s*name", r"party"],
    "number": [r"invoice\s*(no|number)", r"note\s*(no|number)", r"document\s*(no|number)", r"bill\s*no", r"inum"],
    "invoice_date": [r"invoice\s*date", r"note\s*date", r"document\s*date", r"^date", r"bill\s*date"],
    "total": [r"invoice\s*value", r"note\s*value", r"total\s*value", r"document\s*value"],
    "place_of_supply": [r"place\s*of\s*supply", r"^pos"],
    "reverse_charge": [r"reverse\s*charge"],
    "taxable_value": [r"taxable\s*value", r"taxable"],
    "igst": [r"integrated\s*tax", r"^igst"],
    "cgst": [r"central\s*tax", r"^cgst"],
    "sgst": [r"state.*tax", r"^sgst", r"utgst"],
    "cess": [r"^cess"],
    "supplier_period": [r"gstr-?1.*period", r"filing\s*period", r"tax\s*period"],
    "supplier_filing_date": [r"filing\s*date"],
    "itc_available": [r"itc\s*availability", r"itc\s*available"],
    "itc_unavailable_reason": [r"^reason"],
    "irn": [r"^irn$", r"\birn\b(?!.*date)"],
    "doc_type": [r"note\s*type", r"document\s*type"],
}


def _map_header(cells: list[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, c in enumerate(cells):
        text = re.sub(r"\s+", " ", str(c or "")).strip().lower().replace("(₹)", "").strip()
        if not text:
            continue
        for key, pats in _HEADERS.items():
            if key in mapping:
                continue
            if any(re.search(p, text) for p in pats):
                mapping[key] = idx
                break
    return mapping


def parse_excel(data: bytes) -> Statement:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise Gstr2bError("Could not open the Excel file.") from exc
    records: list[Record] = []
    for ws in wb.worksheets:
        name = ws.title.lower().replace("-", "").replace(" ", "")
        if any(x in name for x in ("summary", "readme", "itcavailable", "itcnotavailable")) and name not in ("b2b",):
            continue
        section = "cdnr" if "cdnr" in name or "credit" in name else "b2ba" if "b2ba" in name else "b2b"
        rows = list(ws.iter_rows(values_only=True))
        header_idx, mapping = None, {}
        for i, r in enumerate(rows[:12]):
            m = _map_header(list(r))
            # Portal sheets have two header rows (group titles, then column titles): merge them.
            if i + 1 < len(rows):
                m2 = _map_header(list(rows[i + 1]))
                for k, v in m2.items():
                    m.setdefault(k, v)
            if "supplier_gstin" in m and "number" in m and ("taxable_value" in m or "total" in m):
                header_idx, mapping = i, m
                break
        if header_idx is None:
            continue
        for r in rows[header_idx + 1:]:
            def val(key: str) -> Any:
                i = mapping.get(key)
                return r[i] if i is not None and i < len(r) else None

            g = norm_gstin(str(val("supplier_gstin") or ""))
            if len(g) != 15:
                continue
            tx, ig, cg, sg, cs = d(val("taxable_value")), d(val("igst")), d(val("cgst")), d(val("sgst")), d(val("cess"))
            dtype = str(val("doc_type") or "").lower()
            doc_type = "credit_note" if "credit" in dtype or section == "cdnr" and "debit" not in dtype else (
                "debit_note" if "debit" in dtype else "invoice")
            pos_raw = str(val("place_of_supply") or "")
            from lekha.engines.states import parse_place_of_supply

            records.append(Record(
                section=section, doc_type=doc_type, supplier_gstin=g, supplier_name=val("supplier_name"),
                number=str(val("number") or "").strip(), invoice_date=parse_date(val("invoice_date")),
                place_of_supply=parse_place_of_supply(pos_raw), reverse_charge=_yes(val("reverse_charge")),
                taxable_value=tx, igst=ig, cgst=cg, sgst=sg, cess=cs,
                total=d(val("total")) if val("total") not in (None, "") else tx + ig + cg + sg + cs,
                itc_available=_yes(val("itc_available")) if mapping.get("itc_available") is not None else True,
                itc_unavailable_reason=val("itc_unavailable_reason") or None,
                supplier_filing_date=parse_date(val("supplier_filing_date")),
                supplier_period=_period_text(val("supplier_period")), irn=val("irn") or None,
            ))
    if not records:
        raise Gstr2bError("No invoice rows found. Upload the GSTR-2B Excel or JSON downloaded from the GST portal.")
    return Statement(gstin=None, period=None, generated_on=None, records=records)


def _period_text(v: Any) -> str | None:
    if v in (None, ""):
        return None
    s = str(v).strip()
    m = re.match(r"([A-Za-z]{3})[- ']?(\d{2,4})", s)
    if m:
        months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
        mon = months.index(m.group(1).lower()[:3]) + 1
        yr = int(m.group(2)) + (2000 if len(m.group(2)) == 2 else 0)
        return f"{mon:02d}{yr}"
    return s if re.fullmatch(r"\d{6}", s) else None


def parse(filename: str, data: bytes) -> Statement:
    if filename.lower().endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        return parse_excel(data)
    return parse_json(data)
