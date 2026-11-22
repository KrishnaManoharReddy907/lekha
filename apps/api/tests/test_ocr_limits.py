"""The OCR detector cap keeps memory inside a 512 MB host without losing the page's text."""

import pytest

from lekha.ocr import pages


@pytest.mark.parametrize(("env", "expected"), [(None, 1280), ("1024", 1024), ("0", 0), ("junk", 1280), ("-5", 0)])
def test_det_max_side_from_env(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("LEKHA_OCR_DET_MAX_SIDE", raising=False)
    else:
        monkeypatch.setenv("LEKHA_OCR_DET_MAX_SIDE", env)
    assert pages.det_max_side() == expected


def test_engine_pins_detector_size(monkeypatch):
    monkeypatch.setenv("LEKHA_OCR_DET_MAX_SIDE", "960")
    pages._ocr_engine.cache_clear()
    try:
        det = pages._ocr_engine().text_det
        assert det.get_preprocess(1800).limit_side_len == 960  # large page: capped
        assert det.get_preprocess(700).limit_side_len == 704  # small page: never upscaled past itself
    finally:
        pages._ocr_engine.cache_clear()
