"""Turn an uploaded file into page images + positioned text tokens.

Digital PDFs keep their exact text layer (no OCR errors). Scans and phone photos go through
EXIF rotation, size normalisation, deskew and RapidOCR. Every token gets a normalised
bounding box (0..1) so the review screen can highlight exactly where a value came from.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageOps

MAX_SIDE = 2000
AI_MAX_SIDE = 1600
MAX_PAGES = 6


@dataclass
class Token:
    id: int
    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 normalised
    conf: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "box": [round(v, 4) for v in self.box], "conf": round(self.conf, 3)}


@dataclass
class Page:
    image: Image.Image
    tokens: list[Token] = field(default_factory=list)
    source: str = "ocr"  # ocr | pdf_text

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


def _register_heif() -> None:
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:
        pass


_register_heif()


def sniff_mime(data: bytes, declared: str | None = None) -> str:
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
        return "image/heic"
    return declared or "application/octet-stream"


ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic"}


def load_pages(data: bytes, mime: str) -> list[Page]:
    if mime == "application/pdf":
        return _load_pdf(data)
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = _limit(img, MAX_SIDE)
    img = deskew(img)
    return [Page(image=img, source="ocr")]


def _load_pdf(data: bytes) -> list[Page]:
    import pymupdf

    pages: list[Page] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for pno in range(min(len(doc), MAX_PAGES)):
            p = doc[pno]
            zoom = min(2.5, MAX_SIDE / max(p.rect.width, p.rect.height))
            pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            words = p.get_text("words")  # x0, y0, x1, y1, word, block, line, wno
            if len(words) >= 8:
                w, h = p.rect.width, p.rect.height
                tokens = _merge_words_into_lines(words, w, h)
                pages.append(Page(image=img, tokens=tokens, source="pdf_text"))
            else:
                pages.append(Page(image=img, source="ocr"))
    return pages


def _merge_words_into_lines(words: list, w: float, h: float) -> list[Token]:
    """Group PDF words that share a line and sit close together into one token (like OCR segments)."""
    out: list[Token] = []
    by_line: dict[tuple[int, int], list] = {}
    for x0, y0, x1, y1, word, block, line, _wno in words:
        by_line.setdefault((block, line), []).append((x0, y0, x1, y1, word))
    for items in by_line.values():
        items.sort(key=lambda t: t[0])
        seg = [items[0]]
        for it in items[1:]:
            gap = it[0] - seg[-1][2]
            height = max(seg[-1][3] - seg[-1][1], 1)
            if gap > height * 1.2:
                out.append(_seg_token(len(out), seg, w, h))
                seg = [it]
            else:
                seg.append(it)
        out.append(_seg_token(len(out), seg, w, h))
    out.sort(key=lambda t: (round(t.box[1], 3), t.box[0]))
    for i, t in enumerate(out):
        t.id = i
    return out


def _seg_token(i: int, seg: list, w: float, h: float) -> Token:
    x0 = min(s[0] for s in seg) / w
    y0 = min(s[1] for s in seg) / h
    x1 = max(s[2] for s in seg) / w
    y1 = max(s[3] for s in seg) / h
    return Token(i, " ".join(s[4] for s in seg), (x0, y0, x1, y1), 1.0)


def _limit(img: Image.Image, side: int) -> Image.Image:
    if max(img.size) <= side:
        return img
    scale = side / max(img.size)
    return img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)


def deskew(img: Image.Image) -> Image.Image:
    """Straighten photos taken at a slight angle (up to ±15°)."""
    import cv2

    gray = np.array(img.convert("L"))
    small_scale = 1000 / max(gray.shape)
    if small_scale < 1:
        gray = cv2.resize(gray, None, fx=small_scale, fy=small_scale)
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    lines = cv2.HoughLinesP(thr, 1, math.pi / 360, threshold=120, minLineLength=gray.shape[1] // 4, maxLineGap=12)
    if lines is None:
        return img
    angles = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if abs(a) < 15:
            angles.append(a)
    if len(angles) < 3:
        return img
    angle = float(np.median(angles))
    if abs(angle) < 0.4:
        return img
    return img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))


def det_max_side() -> int:
    """Longest side the text *detector* sees (0 = RapidOCR's own choice, up to 2000 px).

    Detection activations dominate OCR memory (about 390 MB at 2000 px, 205 MB at 1280, 135 MB at
    1024). Recognition still reads crops from the full-resolution page, so capping detection costs
    little accuracy (see docs/BENCHMARK.md) while letting OCR run on a 512 MB host.
    Read from the environment so the OCR worker process needs no settings import.
    """
    import os

    try:
        return max(0, int(os.environ.get("LEKHA_OCR_DET_MAX_SIDE", "1280")))
    except ValueError:
        return 1280


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    limit = det_max_side()
    if limit:
        from rapidocr_onnxruntime.ch_ppocr_det.utils import DetPreProcess

        det = engine.text_det
        # RapidOCR's "max" mode ignores the configured side and picks 960/1500/2000 itself; pin it.
        det.get_preprocess = lambda max_wh: DetPreProcess(min(limit, (max_wh + 31) // 32 * 32), "max",
                                                          det.mean, det.std)
    return engine


def ocr_page(page: Page) -> None:
    if page.source == "pdf_text" or page.tokens:
        return
    arr = np.array(page.image)
    result, _ = _ocr_engine()(arr)
    w, h = page.image.size
    tokens: list[Token] = []
    for box, text, conf in result or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        tokens.append(Token(0, str(text), (min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h), float(conf)))
    tokens.sort(key=lambda t: (round(t.box[1], 2), t.box[0]))
    for i, t in enumerate(tokens):
        t.id = i
    page.tokens = tokens


def process(data: bytes, mime: str) -> list[Page]:
    pages = load_pages(data, mime)
    for p in pages:
        ocr_page(p)
    # Make token ids unique across pages: page index * 10000 + local id.
    for pi, p in enumerate(pages):
        for t in p.tokens:
            t.id = pi * 10000 + t.id
    return pages


def png_bytes(img: Image.Image, max_side: int | None = None, fmt: str = "PNG") -> bytes:
    if max_side:
        img = _limit(img, max_side)
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.save(buf, format="JPEG", quality=85, optimize=True)
    else:
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def full_text(pages: list[Page]) -> str:
    return "\n".join(t.text for p in pages for t in p.tokens)


_DCT32 = 2.0 * np.cos(np.pi * np.outer(np.arange(32), 2 * np.arange(32) + 1) / 64)


def phash(image: Image.Image):
    """imagehash.phash (hash_size=8), bit for bit, with the 2-D DCT as two matrix products (no SciPy import)."""
    import imagehash

    pixels = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=float)
    dct = np.round(_DCT32 @ pixels @ _DCT32.T, 6)  # drop float dust: a blank page has exact zeros
    low = dct[:8, :8]
    return imagehash.ImageHash(low > np.median(low))


def page_hashes(page: Page) -> tuple[str, str]:
    """(perceptual hash of the page, layout hash that ignores the actual text)."""
    import cv2
    import imagehash

    ph = str(phash(page.image))
    gray = np.array(page.image.convert("L").resize((256, int(256 * page.image.height / page.image.width))))
    blur = cv2.GaussianBlur(gray, (31, 31), 0)
    bw = (blur < 200).astype(np.uint8) * 255
    lh = str(imagehash.average_hash(Image.fromarray(bw), hash_size=8))
    return ph, lh
