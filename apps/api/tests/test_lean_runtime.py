"""Pure-NumPy replacements that keep SciPy out of the API process must match SciPy exactly."""

import random
import subprocess
import sys

import imagehash
import numpy as np
import pytest
from PIL import Image, ImageDraw
from scipy.optimize import linear_sum_assignment as scipy_lsa

from lekha.engines.assignment import linear_sum_assignment
from lekha.ocr.pages import phash


def _cost(a, rows, cols):
    return float(np.asarray(a)[rows, cols].sum())


@pytest.mark.parametrize("seed", range(6))
def test_assignment_matches_scipy_on_random_matrices(seed):
    rng = np.random.default_rng(seed)
    for _ in range(400):
        n, m = rng.integers(0, 9, size=2)
        kind = rng.integers(0, 3)
        if kind == 0:
            a = rng.random((n, m))
        elif kind == 1:  # many ties, like 0/1 match scores
            a = rng.integers(0, 3, size=(n, m)).astype(float)
        else:  # negative scores, as matching passes -score
            a = -rng.random((n, m)) * rng.integers(0, 2, size=(n, m))
        r, c = linear_sum_assignment(a)
        sr, sc = scipy_lsa(a)
        assert len(r) == len(sr) == min(n, m)
        assert list(r) == sorted(r) and len(set(c)) == len(c)
        assert _cost(a, r, c) == pytest.approx(_cost(a, sr, sc), abs=1e-9)


def test_assignment_known_answer_and_validation():
    a = [[4, 1, 3], [2, 0, 5], [3, 2, 2]]
    r, c = linear_sum_assignment(a)
    assert list(zip(r.tolist(), c.tolist(), strict=True)) == [(0, 1), (1, 0), (2, 2)]
    with pytest.raises(ValueError):
        linear_sum_assignment([[np.inf]])


def test_phash_is_bit_identical_to_imagehash():
    rnd = random.Random(1)
    for k in range(60):
        w, h = rnd.randint(40, 900), rnd.randint(40, 900)
        img = Image.new("RGB", (w, h), (rnd.randint(0, 255),) * 3)
        d = ImageDraw.Draw(img)
        for _ in range(rnd.randint(0, 40)):
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            d.rectangle([x, y, x + rnd.randint(1, 200), y + rnd.randint(1, 60)],
                        fill=tuple(rnd.randint(0, 255) for _ in range(3)))
        if k % 3 == 0:
            img = Image.fromarray(np.random.default_rng(k).integers(0, 256, (h, w, 3), dtype=np.uint8))
        assert str(phash(img)) == str(imagehash.phash(img, hash_size=8))


def test_api_process_does_not_load_heavy_optional_libraries():
    code = ("import sys, lekha.main, lekha.jobs.handlers, lekha.services.reconcile; "
            "print(','.join(m for m in ('scipy', 'anthropic') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == ""


def test_password_hashes_are_light_and_old_ones_upgrade():
    from argon2 import PasswordHasher

    from lekha.core.security import hash_password, needs_rehash, verify_password

    h = hash_password("Correct-Horse-9")
    assert "m=19456,t=2,p=1" in h and verify_password("Correct-Horse-9", h) and not needs_rehash(h)
    old = PasswordHasher().hash("Correct-Horse-9")  # library defaults (64 MiB), as stored before
    assert verify_password("Correct-Horse-9", old) and needs_rehash(old)
    assert not verify_password("wrong", h)
