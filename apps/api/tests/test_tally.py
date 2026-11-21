from datetime import date
from decimal import Decimal as D

from lxml import etree

from lekha.engines.tally import Voucher, balanced, build_xml


def _sum(xml: bytes) -> list[float]:
    root = etree.fromstring(xml)
    return [sum(float(a.text) for a in v.findall("./ALLLEDGERENTRIES.LIST/AMOUNT")) for v in root.findall(".//VOUCHER")]


def test_vouchers_balance_for_both_round_off_directions():
    up = Voucher("A/1", date(2025, 10, 1), "Raja Glass", "36AAAAA0000A1Z5", "36", D("100000.40"), D(0), D("9000.04"),
                 D("9000.04"), D(0), D("-0.48"), D("118000.00"))
    down = Voucher("A/2", date(2025, 10, 2), "Raja Glass", "36AAAAA0000A1Z5", "36", D("100000.30"), D(0), D("9000.02"),
                   D("9000.02"), D(0), D("0.66"), D("118001.00"))
    assert balanced(up) and balanced(down)
    sums = _sum(build_xml([up, down]))
    assert all(abs(s) < 0.005 for s in sums), sums


def test_ledger_master_created_once():
    v = Voucher("B/1", date(2025, 10, 1), "Kumar Transport", "29AABCU9603R1ZJ", "36", D(1000), D(180), D(0), D(0), D(0),
                D(0), D(1180))
    root = etree.fromstring(build_xml([v, v]))
    assert len(root.findall(".//LEDGER")) == 1
    assert root.find(".//LEDGER/LEDSTATENAME").text == "Karnataka"
