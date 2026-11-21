"""Realistic Indian business data for synthetic invoices: suppliers, items with HSN/SAC and rates by date."""

from __future__ import annotations

import random
from datetime import date

from lekha.engines import gstin as G
from lekha.engines.rules import GST2_DATE

CITIES = {
    "36": [("Hyderabad", "500072"), ("Secunderabad", "500003"), ("Warangal", "506002")],
    "37": [("Vijayawada", "520010"), ("Visakhapatnam", "530016"), ("Guntur", "522002")],
    "29": [("Bengaluru", "560058"), ("Mysuru", "570001")],
    "27": [("Mumbai", "400069"), ("Pune", "411019"), ("Nagpur", "440010")],
    "33": [("Chennai", "600032"), ("Coimbatore", "641018")],
    "32": [("Kochi", "682016"), ("Thrissur", "680001")],
    "07": [("New Delhi", "110020")],
    "24": [("Ahmedabad", "380015"), ("Surat", "395003")],
}
STREETS = ["MG Road", "Industrial Estate", "Kukatpally", "Begumpet", "Phase II, IDA", "Station Road", "Main Bazaar",
           "Ring Road", "Gandhi Nagar", "Sector 5", "Old City", "Market Yard", "Ameerpet", "Jubilee Hills"]
NAME_A = ["Sri Balaji", "Lakshmi", "Venkateswara", "Sai Krishna", "Deccan", "Godavari", "Krishna", "Annapurna",
          "Bharat", "National", "Shree Ganesh", "Vijaya", "Sri Rama", "Navyug", "Om Sai", "Mahalakshmi", "Kaveri",
          "Sapthagiri", "Royal", "Classic", "Prime", "Universal", "Metro", "Sunrise"]
NAME_B = ["Traders", "Enterprises", "Industries", "Agencies", "Distributors", "Hardware", "Steel Fittings",
          "Polymers", "Packaging", "Electricals", "Chemicals", "Logistics", "Paper Mart", "Timber Depot",
          "Tech Solutions", "Glass Works", "Paints", "Auto Parts"]
SUFFIX = ["", " Pvt Ltd", " LLP", " & Co", " Private Limited", ""]
PAN_TYPE = {"Pvt Ltd": "C", "Private Limited": "C", "LLP": "F", "& Co": "F"}

# (description, hsn, unit, price range, rate before GST 2.0, rate after)
ITEMS = [
    ("Teak wood planks, sawn", "4407", "CFT", (2500, 4200), 18, 18),
    ("MS steel angle 40x40", "7216", "KG", (55, 75), 18, 18),
    ("PVC pipe 4 inch", "3917", "NOS", (300, 650), 18, 18),
    ("Copper wire 2.5 sq mm", "8544", "MTR", (28, 45), 18, 18),
    ("Cement OPC 53 grade", "2523", "BAG", (340, 420), 28, 18),
    ("Corrugated boxes 5-ply", "4819", "NOS", (18, 60), 18, 18),
    ("Laser printer toner", "8443", "NOS", (1800, 4200), 18, 18),
    ("Office chairs", "9401", "NOS", (3500, 9000), 18, 18),
    ("Bath soap (case of 48)", "3401", "BOX", (900, 1500), 18, 5),
    ("Shampoo bottles 340ml (case)", "3305", "BOX", (2100, 3200), 18, 5),
    ("Toothpaste 150g (case of 36)", "3306", "BOX", (1900, 2600), 18, 5),
    ("Split AC 1.5 ton", "8415", "NOS", (28000, 42000), 28, 18),
    ("LED TV 43 inch", "8528", "NOS", (21000, 32000), 28, 18),
    ("Laptop i5 16GB", "8471", "NOS", (52000, 78000), 18, 18),
    ("Industrial gloves (pair)", "4015", "PAIR", (40, 120), 12, 5),
    ("Paint emulsion 20L", "3209", "NOS", (3800, 6200), 28, 18),
    ("Ball bearings 6205", "8482", "NOS", (90, 240), 18, 18),
    ("Diesel generator service", "998719", "JOB", (4500, 15000), 18, 18),
    ("Software subscription (annual)", "998314", "NOS", (12000, 60000), 18, 18),
    ("Goods transport (GTA)", "996511", "TRIP", (6000, 22000), 5, 5),
    ("Chartered accountancy fees", "998221", "JOB", (15000, 60000), 18, 18),
    ("Security services", "998525", "MONTH", (18000, 45000), 18, 18),
    ("Staff lunch catering", "996331", "PLATE", (120, 280), 5, 5),
    ("Mineral water 20L cans", "2201", "NOS", (40, 70), 18, 18),
]


def make_party(rng: random.Random, state: str | None = None) -> dict:
    state = state or rng.choice(list(CITIES))
    suffix = rng.choice(SUFFIX)
    name = f"{rng.choice(NAME_A)} {rng.choice(NAME_B)}{suffix}"
    pan_type = next((v for k, v in PAN_TYPE.items() if k in suffix), rng.choice("PFC"))
    pan = (
        "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(3))
        + pan_type
        + name[0].upper()
        + "".join(rng.choice("0123456789") for _ in range(4))
        + rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
    )
    city, pin = rng.choice(CITIES[state])
    return {
        "name": name,
        "gstin": G.make(state, pan, rng.choice("1112")),
        "state": state,
        "address": f"{rng.randint(1, 99)}-{rng.randint(1, 400)}, {rng.choice(STREETS)}, {city} {pin}",
        "phone": f"+91 9{rng.randint(100000000, 999999999)}",
        "email": f"accounts@{name.split()[0].lower()}{rng.randint(1, 99)}.in",
    }


def rate_for(item: tuple, on: date) -> int:
    return item[5] if on >= GST2_DATE else item[4]


def invoice_number(rng: random.Random, style: int, seq: int, on: date) -> str:
    fy = f"{on.year % 100 if on.month >= 4 else on.year % 100 - 1:02d}-{(on.year % 100 + (1 if on.month >= 4 else 0)):02d}"
    styles = [
        f"INV/{fy}/{seq:04d}",
        f"SB-{seq}",
        f"{on.year}/{seq:03d}",
        f"T{seq:05d}",
        f"GST/{seq}/{fy}",
        f"{seq:04d}",
    ]
    return styles[style % len(styles)]
