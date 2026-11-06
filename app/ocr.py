"""Local OCR: page rasterisation (PDF / HEIC / images) + text recognition.

RapidOCR runs PaddleOCR models on onnxruntime, so there is no system
dependency such as Tesseract, and it runs fast on Apple Silicon.
"""

from __future__ import annotations

import io
import threading
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


def extract(path: Path) -> OcrResult:
    """Run OCR over every page of the uploaded document."""
    engine = _get_engine()
    pages: list[Page] = []

    for index, raw in enumerate(_load_raster_pages(path)):
        img = _prepare(raw)
        width, height = img.size
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        result, _ = engine(buf.getvalue())
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
        page_text = _group_lines(flat, gap_threshold=max(char_width * 2.5, 0.008))
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
