from hypothesis import given
from hypothesis import strategies as st

from lekha.engines import gstin as G

REAL = "27AAPFU0939F1ZV"  # publicly documented example GSTIN

pan_strategy = st.builds(
    lambda a, n, z: a + n + z,
    st.text("ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=5, max_size=5),
    st.text("0123456789", min_size=4, max_size=4),
    st.text("ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=1),
)
state_strategy = st.sampled_from(["07", "27", "29", "32", "33", "36", "37"])


def test_real_gstin_is_valid():
    chk = G.validate(REAL)
    assert chk.valid and chk.checksum_ok and chk.state_code == "27"
    assert chk.pan == "AAPFU0939F" and chk.entity_type == "Firm / LLP"


def test_normalises_spaces_and_case():
    assert G.validate(" 27aapfu 0939f1zv ").valid


def test_wrong_length():
    chk = G.validate("27AAPFU0939F1Z")
    assert not chk.valid and "15 characters" in chk.problems[0]


def test_bad_state_code():
    g = G.make("27", "AAPFU0939F")
    bad = "00" + g[2:14]
    bad += G.check_char(bad)
    chk = G.validate(bad)
    assert not chk.valid and not chk.state_ok and chk.checksum_ok


@given(state_strategy, pan_strategy)
def test_make_always_valid(state, pan):
    assert G.is_valid(G.make(state, pan))


@given(state_strategy, pan_strategy, st.integers(0, 13), st.sampled_from(list(G.ALPHABET)))
def test_any_single_substitution_breaks_checksum(state, pan, pos, ch):
    g = G.make(state, pan)
    if g[pos] == ch:
        return
    mutated = g[:pos] + ch + g[pos + 1 :]
    # The mod-36 Luhn variant detects every single-character substitution.
    assert G.check_char(mutated[:14]) != mutated[14]


def test_repair_prefers_known_supplier():
    good = G.make("36", "AAGCD7781Q")
    typo = good[:7] + ("8" if good[7] != "8" else "9") + good[8:]
    assert not G.is_valid(typo)
    assert G.repair_candidates(typo, known={good}) == [good]


def test_repair_confusable_without_known():
    good = REAL
    typo = good.replace("0939", "O939")  # OCR read zero as letter O
    assert good in G.repair_candidates(typo)
