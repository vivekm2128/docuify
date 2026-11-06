"""Docufy API: upload a document, OCR it locally, scrape user-defined fields."""

from __future__ import annotations

import json
import mimetypes
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import extractor
from .config import settings
from .ocr import extract as run_ocr

app = FastAPI(title="Docufy", version="1.0.0", docs_url="/api/docs")

DOCS_DIR = settings.data_dir / "documents"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

# OCR results are reused across scrape calls for the same document.
_ocr_cache: dict[str, dict] = {}


# ---------------------------------------------------------------- models


class FieldSpecIn(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    type: Literal["string", "number", "integer", "boolean", "date", "array", "object"] = "string"
    required: bool = False


class ScrapeRequest(BaseModel):
    document_id: str
    fields: list[FieldSpecIn] = Field(min_length=1, max_length=40)
    model: str | None = None
    refresh: bool = False


# ---------------------------------------------------------------- helpers


def _validated_id(document_id: str) -> str:
    """Reject path traversal and unknown ids."""
    if not document_id or not all(c in "0123456789abcdef-" for c in document_id.lower()):
        raise HTTPException(400, "Invalid document id")
    if not (DOCS_DIR / document_id).is_dir():
        raise HTTPException(404, "Document not found. Upload it again.")
    return document_id


def _doc_meta(document_id: str) -> dict:
    path = DOCS_DIR / document_id / "meta.json"
    if not path.exists():
        raise HTTPException(404, "Document not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _get_ocr(document_id: str) -> dict:
    cached = _ocr_cache.get(document_id)
    if cached is not None:
        return cached
    path = DOCS_DIR / document_id / "ocr.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        _ocr_cache[document_id] = data
        return data
    raise HTTPException(409, "This document has not finished OCR yet.")


# ---------------------------------------------------------------- routes


@app.get("/api/health")
def health() -> dict:
    models = extractor.list_models()
    return {
        "ok": True,
        "llm": {
            "url": settings.llm_base_url,
            "configured_model": settings.llm_model,
            "reachable": bool(models),
            "installed_models": models,
            "model_installed": any(
                m == settings.llm_model or m.split(":")[0] == settings.llm_model.split(":")[0]
                for m in models
            ),
        },
        "types": extractor.SUPPORTED_TYPES,
        "max_upload_mb": settings.max_upload_mb,
    }


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in settings.allowed_extensions:
        raise HTTPException(
            400,
            f"Unsupported file type '{suffix or 'unknown'}'. "
            f"Allowed: {', '.join(settings.allowed_extensions)}",
        )

    document_id = str(uuid.uuid4())
    folder = DOCS_DIR / document_id
    folder.mkdir(parents=True)
    original = folder / f"original{suffix}"

    max_bytes = settings.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with original.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        await file.close()

    meta = {
        "document_id": document_id,
        "filename": file.filename or f"document{suffix}",
        "bytes": written,
        "uploaded_at": int(time.time()),
        "original": original.name,
    }

    # OCR now, so the first Scrape click only pays for the LLM.
    started = time.perf_counter()
    try:
        result = run_ocr(original)
    except Exception as exc:  # noqa: BLE001 - report the real reason to the UI
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, f"Could not read this document: {exc}") from exc

    ocr_data = result.as_dict()
    ocr_data["duration_ms"] = int((time.perf_counter() - started) * 1000)
    (folder / "ocr.json").write_text(json.dumps(ocr_data), encoding="utf-8")
    _ocr_cache[document_id] = ocr_data

    meta.update(
        {
            "pages": len(ocr_data["pages"]),
            "ocr_confidence": ocr_data["confidence"],
            "ocr_ms": ocr_data["duration_ms"],
            "text_length": len(ocr_data["text"]),
        }
    )
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    return {
        "document": meta,
        "ocr": {
            "confidence": ocr_data["confidence"],
            "duration_ms": ocr_data["duration_ms"],
            "pages": [
                {
                    "index": p["index"],
                    "width": p["width"],
                    "height": p["height"],
                    "confidence": p["confidence"],
                    "word_count": len(p["words"]),
                }
                for p in ocr_data["pages"]
            ],
        },
    }


@app.get("/api/documents/{document_id}/file")
def document_file(document_id: str):
    _validated_id(document_id)
    meta = _doc_meta(document_id)
    path = DOCS_DIR / document_id / meta["original"]
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=meta["filename"])


@app.get("/api/documents/{document_id}/pages/{page_index}.png")
def document_page(document_id: str, page_index: int):
    """Render one page to PNG for the viewer (PDFs can't be iframed reliably)."""
    _validated_id(document_id)
    page_dir = DOCS_DIR / document_id / "pages"
    page_dir.mkdir(exist_ok=True)
    target = page_dir / f"{page_index}.png"
    if target.exists():
        return FileResponse(target, media_type="image/png")

    from PIL import Image

    from .ocr import _load_raster_pages, _prepare

    meta = _doc_meta(document_id)
    pages = _load_raster_pages(DOCS_DIR / document_id / meta["original"])
    if page_index < 0 or page_index >= len(pages):
        raise HTTPException(404, "Page not found")
    img = _prepare(pages[page_index])
    # Viewer-sized render: sharp when zoomed, still light over localhost.
    if img.width > 2400:
        img = img.resize((2400, int(img.height * 2400 / img.width)), Image.LANCZOS)
    img.save(target, format="PNG", optimize=True)
    return FileResponse(target, media_type="image/png")


@app.get("/api/documents/{document_id}/ocr")
def document_ocr(document_id: str):
    _validated_id(document_id)
    return _get_ocr(document_id)


@app.post("/api/scrape")
def scrape(req: ScrapeRequest):
    _validated_id(req.document_id)
    ocr = _get_ocr(req.document_id)

    fields = [
        extractor.FieldSpec(
            key=f.key.strip(), description=f.description, type=f.type, required=f.required
        )
        for f in req.fields
        if f.key.strip()
    ]
    if not fields:
        raise HTTPException(400, "Add at least one field with a name.")

    keys = [f.key for f in fields]
    if len(set(k.lower() for k in keys)) != len(keys):
        raise HTTPException(400, "Field names must be unique.")

    if not ocr.get("text", "").strip():
        raise HTTPException(
            422, "No readable text was found in this document, so there is nothing to scrape."
        )

    started = time.perf_counter()
    # "Scrape again" must re-query the model even if the inputs are unchanged.
    # Read the flag into a local so the cached result is still reported.
    refresh = req.refresh
    if refresh:
        extractor.invalidate_cache()
    try:
        results, meta = extractor.extract_fields(
            fields, ocr["text"], model=req.model, document_id=req.document_id
        )
    except extractor.LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    if refresh:
        meta = dict(meta, cached=False)

    field_list = [
        {
            "key": k,
            "value": v["value"],
            "confidence": v["confidence"],
            "type": v["type"],
            "evidence": v["evidence"],
        }
        for k, v in results.items()
    ]

    avg = (
        sum(f["confidence"] for f in field_list) / len(field_list) if field_list else 0.0
    )

    response = {
        "document_id": req.document_id,
        "model": meta.get("model"),
        "cached": meta.get("cached", False),
        "llm_ms": meta.get("duration_ms", 0),
        "total_ms": int((time.perf_counter() - started) * 1000),
        "average_confidence": round(avg, 3),
        "ocr_confidence": ocr.get("confidence", 0),
        "fields": field_list,
        "fields_by_name": {f["key"]: f["value"] for f in field_list},
    }
    return JSONResponse(response)


@app.get("/api/models")
def models():
    return {"default": settings.llm_model, "installed": extractor.list_models()}


@app.post("/api/self-check")
def self_check(model: str | None = None):
    """Run the configured model against a built-in document with known answers."""
    try:
        return extractor.self_check(model)
    except extractor.LLMError as exc:
        raise HTTPException(502, str(exc)) from exc


# Static UI last so /api/* wins. html=True serves index.html at /.
if settings.static_dir.exists():
    app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="static")
