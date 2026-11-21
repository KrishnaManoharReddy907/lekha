"""Tally Prime XML export (M12): purchase vouchers + supplier ledger masters.

Tally's import envelope expects credits as positive amounts and debits as negative amounts with
ISDEEMEDPOSITIVE=Yes. A purchase voucher therefore credits the supplier (party) for the invoice
total and debits the purchase ledger (taxable value), the input-tax ledgers and round-off.
Ledger names are configurable per client because every Tally company names them differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from lxml import etree

from lekha.engines.states import state_name


@dataclass
class Ledgers:
    purchase: str = "Purchase Account"
    igst: str = "Input IGST"
    cgst: str = "Input CGST"
    sgst: str = "Input SGST"
    cess: str = "Input Cess"
    round_off: str = "Round Off"
    creditors_group: str = "Sundry Creditors"


@dataclass
class Voucher:
    number: str
    date: date
    supplier_name: str
    supplier_gstin: str | None
    place_of_supply: str | None
    taxable: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    cess: Decimal
    round_off: Decimal
    total: Decimal
    narration: str = ""
    lines: list[dict] = field(default_factory=list)


def _amt(v: Decimal) -> str:
    return f"{v:.2f}"


def _entry(parent: etree._Element, ledger: str, amount: Decimal, debit: bool) -> None:
    e = etree.SubElement(parent, "ALLLEDGERENTRIES.LIST")
    etree.SubElement(e, "LEDGERNAME").text = ledger
    etree.SubElement(e, "ISDEEMEDPOSITIVE").text = "Yes" if debit else "No"
    etree.SubElement(e, "AMOUNT").text = _amt(-amount if debit else amount)


def build_xml(vouchers: list[Voucher], led: Ledgers | None = None, company: str = "##SVCURRENTCOMPANY") -> bytes:
    led = led or Ledgers()
    env = etree.Element("ENVELOPE")
    hdr = etree.SubElement(env, "HEADER")
    etree.SubElement(hdr, "TALLYREQUEST").text = "Import Data"
    body = etree.SubElement(env, "BODY")
    imp = etree.SubElement(body, "IMPORTDATA")
    desc = etree.SubElement(imp, "REQUESTDESC")
    etree.SubElement(desc, "REPORTNAME").text = "All Masters"
    sv = etree.SubElement(desc, "STATICVARIABLES")
    etree.SubElement(sv, "SVCURRENTCOMPANY").text = company
    data = etree.SubElement(imp, "REQUESTDATA")

    # Supplier ledger masters (created if missing, ignored if they exist).
    seen: set[str] = set()
    for v in vouchers:
        if v.supplier_name in seen:
            continue
        seen.add(v.supplier_name)
        msg = etree.SubElement(data, "TALLYMESSAGE")
        lg = etree.SubElement(msg, "LEDGER", NAME=v.supplier_name, ACTION="Create")
        etree.SubElement(lg, "NAME").text = v.supplier_name
        etree.SubElement(lg, "PARENT").text = led.creditors_group
        etree.SubElement(lg, "ISBILLWISEON").text = "Yes"
        if v.supplier_gstin:
            etree.SubElement(lg, "GSTREGISTRATIONTYPE").text = "Regular"
            etree.SubElement(lg, "PARTYGSTIN").text = v.supplier_gstin
            st = state_name(v.supplier_gstin[:2])
            if st:
                etree.SubElement(lg, "LEDSTATENAME").text = st

    for v in vouchers:
        msg = etree.SubElement(data, "TALLYMESSAGE")
        vch = etree.SubElement(msg, "VOUCHER", VCHTYPE="Purchase", ACTION="Create")
        d = v.date.strftime("%Y%m%d")
        etree.SubElement(vch, "DATE").text = d
        etree.SubElement(vch, "VOUCHERTYPENAME").text = "Purchase"
        etree.SubElement(vch, "REFERENCE").text = v.number
        etree.SubElement(vch, "REFERENCEDATE").text = d
        etree.SubElement(vch, "PARTYLEDGERNAME").text = v.supplier_name
        etree.SubElement(vch, "PARTYNAME").text = v.supplier_name
        if v.supplier_gstin:
            etree.SubElement(vch, "PARTYGSTIN").text = v.supplier_gstin
        pos = state_name(v.place_of_supply) if v.place_of_supply else None
        if pos:
            etree.SubElement(vch, "PLACEOFSUPPLY").text = pos
        etree.SubElement(vch, "NARRATION").text = v.narration or f"Purchase invoice {v.number} (imported from Lekha)"
        _entry(vch, v.supplier_name, v.total, debit=False)
        _entry(vch, led.purchase, v.taxable, debit=True)
        for amount, name in ((v.igst, led.igst), (v.cgst, led.cgst), (v.sgst, led.sgst), (v.cess, led.cess)):
            if amount:
                _entry(vch, name, amount, debit=True)
        if v.round_off:
            # Positive round-off is an extra debit; negative round-off is a credit of its absolute value.
            _entry(vch, led.round_off, abs(v.round_off), debit=v.round_off > 0)
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def balanced(v: Voucher) -> bool:
    """Debits must equal the credit: taxable + taxes + round-off == total."""
    return abs(v.taxable + v.igst + v.cgst + v.sgst + v.cess + v.round_off - v.total) <= Decimal("0.01")
