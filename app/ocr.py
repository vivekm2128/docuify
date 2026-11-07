"""Local OCR: page rasterisation (PDF / HEIC / images) + text recognition.

RapidOCR runs PaddleOCR models on onnxruntime, so there is no system
dependency such as Tesseract, and it runs fast on Apple Silicon.
"""

from __future__ import annotations

import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from .config import settings

_engine = None
_lock = threading.Lock()


def _get_engine():
    """Lazily build the OCR engine (model load takes ~1s)."""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR

                _engine = RapidOCR()
    return _engine


@dataclass
class Word:
    text: str
    confidence: float
    # normalised 0..1 coordinates: [x0, y0, x1, y1]
    box: list[float]

    def as_dict(self) -> dict:
        return {"text": self.text, "confidence": round(self.confidence, 3), "box": self.box}


@dataclass
class Page:
    index: int
    width: int
    height: int
    text: str
    confidence: float
    words: list[Word] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "width": self.width,
            "height": self.height,
            "confidence": round(self.confidence, 3),
            "text": self.text,
            "words": [w.as_dict() for w in self.words],
        }


@dataclass
class OcrResult:
    text: str
    pages: list[Page]
    confidence: float
    engine: str = "rapidocr"

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 3),
            "engine": self.engine,
            "pages": [p.as_dict() for p in self.pages],
        }

    @property
    def words(self) -> list[Word]:
        return [w for p in self.pages for w in p.words]

    def worst_confidence(self) -> float:
        """The weakest word score, not the average.

        A page average hides the one destroyed field among a hundred good
        words, which is exactly the failure worth surfacing.
        """
        scores = [w.confidence for w in self.words]
        return min(scores) if scores else 0.0


# A run of letters this long with no space is almost never one real word in
# this kind of document; it is OCR merging several words into one token
# ("NE Fremont St" -> "NEFremontSt"). Digits are left out on purpose, because
# account numbers and dates are legitimately long.
_MERGED_LETTERS = re.compile(r"[A-Za-z]{15,}")
# The same idea for runs that mix letters and digits, like "4820NEFremontSt".
_MERGED_ALNUM = re.compile(r"(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{14,}")


def merged_tokens(text: str) -> list[str]:
    """Spans that look like several words glued together by the OCR.

    These are the defect confidence cannot see: the recogniser read every
    character correctly, so it reports high confidence, but the word
    boundaries and therefore the field value are wrong.

    Runs are looked for inside a whitespace token, not matched whole, because
    a glued address carries its punctuation with it
    ("4820NEFremontSt,Portland,OR97213").
    """
    found: list[str] = []
    for token in text.split():
        for pattern in (_MERGED_LETTERS, _MERGED_ALNUM):
            found.extend(pattern.findall(token))
    # Longest first, so the most damaged span is the one reported.
    return sorted(set(found), key=len, reverse=True)


def _load_raster_pages(path: Path) -> list[Image.Image]:
    """Turn any supported upload into a list of RGB PIL images."""
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        import pypdfium2 as pdfium

        pages: list[Image.Image] = []
        pdf = pdfium.PdfDocument(str(path))
        try:
            for i in range(min(len(pdf), settings.max_pages)):
                bitmap = pdf[i].render(scale=settings.pdf_render_scale)
                pages.append(bitmap.to_pil().convert("RGB"))
        finally:
            pdf.close()
        return pages

    if suffix in {".heic", ".heif"}:
        # iPhone photos are usually HEIC; pillow-heif registers a plugin.
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError as exc:  # pragma: no cover - optional dep
            raise ValueError(
                "HEIC images need the 'pillow-heif' package. "
                "Run: pip install pillow-heif (or convert the photo to JPEG)."
            ) from exc

    img = Image.open(path)
    frames: list[Image.Image] = []
    try:
        for frame in range(getattr(img, "n_frames", 1)):
            img.seek(frame)
            frames.append(img.convert("RGB"))
    finally:
        img.close()
    return frames[: settings.max_pages]


def _prepare(img: Image.Image) -> Image.Image:
    """Autorotate and upscale small scans for better recognition."""
    img = ImageOps.exif_transpose(img)
    longest = max(img.size)
    if longest < settings.ocr_max_dim:
        scale = settings.ocr_max_dim / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    elif longest > 4000:
        scale = 4000 / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img


def _join_text(
    items: list[tuple[float, float, float, float, str, float]], char_width: float
) -> str:
    """Group detection boxes into lines and rejoin them with real spacing.

    Two things matter for the model downstream:
      * words kept apart by a small but visible gap must come back with a space
        ("Invoice No: INV-2026-1042", not "InvoiceNo:INV-2026-1042");
      * a wide gap means separate columns and gets a bigger separator, so the
        model can still tell the left column from the right one.
    """
    if not items:
        return ""

    space_threshold = char_width * 0.35
    column_threshold = max(char_width * 2.5, 0.008)

    lines: list[list[tuple[float, float, float, float, str, float]]] = []
    for item in sorted(items, key=lambda it: (it[1], it[0])):
        centre = (item[1] + item[3]) / 2
        height = max(item[3] - item[1], 1e-4)
        if lines:
            last = lines[-1]
            last_centre = sum((it[1] + it[3]) / 2 for it in last) / len(last)
            last_height = sum(max(it[3] - it[1], 1e-4) for it in last) / len(last)
            if abs(centre - last_centre) < 0.6 * max(height, last_height):
                last.append(item)
                continue
        lines.append([item])

    out: list[str] = []
    for line in lines:
        line.sort(key=lambda it: it[0])
        parts: list[str] = []
        prev_x1: float | None = None
        prev_text = ""
        for x0, _, x1, _, text, _ in line:
            if prev_x1 is not None:
                gap = x0 - prev_x1
                if gap >= column_threshold:
                    parts.append(" | ")
                elif gap >= space_threshold:
                    # Punctuation hugs the token it belongs to.
                    if prev_text.endswith((".", ",", ":", ";", "/", "-", "(", "'")):
                        parts.append(" ")
                    elif text[:1] in (".", ",", ";", ":", "/", ")", "%", "'"):
                        parts.append("")
                    else:
                        parts.append(" ")
                else:
                    parts.append(" ")
            parts.append(text)
            prev_x1 = x1
            prev_text = text
        out.append("".join(parts).strip())
    return "\n".join(out)

def _group_lines(
    items: list[tuple[float, float, float, float, str, float]],
    gap_threshold: float,
) -> str:
    """Re-flatten detected boxes into reading order, one line per row.

    Boxes come back in no particular order; we sort top-to-bottom and glue
    boxes whose vertical centres are close into the same line. Coordinates
    are normalised to 0..1, so ``gap_threshold`` is a fraction of page width.
    """
    if not items:
        return ""

    items.sort(key=lambda it: (it[1], it[0]))
    lines: list[list[tuple[float, float, float, float, str, float]]] = []

    def mid_y(it):
        return (it[1] + it[3]) / 2

    def height_of(it):
        # Normalised coords: never floor at 1.0 (that would be a whole page).
        return max(it[3] - it[1], 1e-4)

    for item in items:
        centre = mid_y(item)
        height = height_of(item)
        if lines:
            last = lines[-1]
            last_centre = sum(mid_y(it) for it in last) / len(last)
            last_height = sum(height_of(it) for it in last) / len(last)
            if abs(centre - last_centre) < 0.6 * max(height, last_height):
                last.append(item)
                continue
        lines.append([item])

    out: list[str] = []
    for line in lines:
        line.sort(key=lambda it: it[0])
        parts: list[str] = []
        prev_x1: float | None = None
        for x0, _, x1, _, text, _ in line:
            if prev_x1 is not None and x0 - prev_x1 > gap_threshold:
                parts.append("  ")  # keep column gaps visible to the model
            parts.append(text)
            prev_x1 = x1
        out.append("".join(parts).strip())
    return "\n".join(out)


def _page_payload(raw: Image.Image) -> tuple[bytes, int, int]:
    """Prepare one page and encode it once for the engine."""
    img = _prepare(raw)
    width, height = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), width, height


def extract(path: Path) -> OcrResult:
    """Run OCR over every page of the uploaded document."""
    engine = _get_engine()
    pages: list[Page] = []

    raws = _load_raster_pages(path)
    # Render/encode the next page while the current one is being recognised.
    # The engine call itself is serial, so this only hides the ~0.3s of image
    # work per page without contending for CPU during recognition.
    with ThreadPoolExecutor(max_workers=2) as pool:
        prepared = [pool.submit(_page_payload, raw) for raw in raws]

        for index, fut in enumerate(prepared):
            payload, width, height = fut.result()
            result, _ = engine(payload)

            words: list[Word] = []
            flat: list[tuple[float, float, float, float, str, float]] = []
            for item in result or []:
                box, text, score = item[0], item[1], float(item[2])
                text = text.strip()
                if not text or score < settings.ocr_min_confidence:
                    continue
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                norm = [
                    round(min(xs) / width, 5),
                    round(min(ys) / height, 5),
                    round(max(xs) / width, 5),
                    round(max(ys) / height, 5),
                ]
                words.append(Word(text=text, confidence=score, box=norm))
                flat.append((norm[0], norm[1], norm[2], norm[3], text, score))

            # One character-width estimate -> a sensible column-gap threshold.
            per_char = [
                (it[2] - it[0]) / max(len(it[4]), 1) for it in flat if it[4].strip()
            ]
            char_width = (sum(per_char) / len(per_char)) if per_char else 0.01
            page_text = _join_text(flat, char_width)
            confs = [w.confidence for w in words]
            pages.append(
                Page(
                    index=index,
                    width=width,
                    height=height,
                    text=page_text,
                    confidence=sum(confs) / len(confs) if confs else 0.0,
                    words=words,
                )
            )
    all_confs = [p.confidence for p in pages if p.words]
    full_text = "\n\n".join(f"--- Page {p.index + 1} ---\n{p.text}" for p in pages).strip()
    if not full_text:
        full_text = ""

    return OcrResult(
        text=full_text,
        pages=pages,
        confidence=sum(all_confs) / len(all_confs) if all_confs else 0.0,
    )
