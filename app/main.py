"""Docufy API: upload a document, OCR it locally, scrape user-defined fields."""

from __future__ import annotations

import json
import mimetypes
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

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

# Nothing is cached: OCR runs at scrape time, and every scrape is a real model
# call. The OCR engine itself is a lazy singleton in app.ocr, and the lock below
# keeps one scrape from throwing two full OCR passes at the CPU at once.
_ocr_lock = threading.Lock()


# ---------------------------------------------------------------- models


class FieldSpecIn(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    type: Literal["string", "number", "integer", "boolean", "date", "array", "object"] = "string"
    required: bool = False
    # Slot names for structured list fields, e.g. ["date", "amount"].
    fields: list[str] | None = None


class ScrapeRequest(BaseModel):
    document_id: str
    fields: list[FieldSpecIn] = Field(min_length=1, max_length=40)
    model: str | None = None


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


def _ocr_path(document_id: str) -> Path:
    return DOCS_DIR / document_id / "ocr.json"


def _read_ocr(document_id: str) -> dict | None:
    path = _ocr_path(document_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _run_ocr(document_id: str) -> tuple[dict, int]:
    """OCR the stored upload right now. Returns (ocr payload, milliseconds).

    Called from the scrape path only, so an upload responds instantly and a
    scrape always reads the document fresh. The result is written to disk for
    the viewer's word boxes, but it is never reused to answer a scrape.
    """
    meta = _doc_meta(document_id)
    source = DOCS_DIR / document_id / meta["original"]
    started = time.perf_counter()
    with _ocr_lock:
        result = run_ocr(source)
    data = result.as_dict()
    ms = int((time.perf_counter() - started) * 1000)
    data["duration_ms"] = ms
    try:
        _ocr_path(document_id).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return data, ms


def _document_status(document_id: str) -> dict[str, Any]:
    """What the viewer needs to know before the first scrape."""
    meta = _doc_meta(document_id)
    ocr = _read_ocr(document_id)
    return {
        "document_id": document_id,
        "filename": meta.get("filename"),
        "bytes": meta.get("bytes"),
        "pages": (len(ocr["pages"]) if ocr else meta.get("pages")) or 1,
        "ocr_done": ocr is not None,
        "ocr_confidence": ocr.get("confidence") if ocr else None,
        "text_length": len(ocr.get("text", "")) if ocr else None,
    }


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
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    # No OCR here: it runs at scrape time, so the upload is instant and no
    # result is ever served from an earlier pass. The viewer renders pages on
    # demand and warms them in the background, so the document is visible
    # immediately.
    return {
        "document": {
            **meta,
            "pages": 0,
            "ocr_done": False,
            "ocr_confidence": None,
            "text_length": None,
        }
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


@app.get("/api/documents/{document_id}/status")
def document_status(document_id: str):
    """Lightweight state for the viewer: page count, whether OCR has run."""
    _validated_id(document_id)
    return _document_status(document_id)


@app.get("/api/documents/{document_id}/ocr")
def document_ocr(document_id: str):
    _validated_id(document_id)
    data = _read_ocr(document_id)
    if data is None:
        raise HTTPException(409, "This document has not been read yet. Run a scrape first.")
    return data


@app.post("/api/scrape")
def scrape(req: ScrapeRequest):
    _validated_id(req.document_id)

    fields = [
        extractor.FieldSpec(
            key=f.key.strip(),
            description=f.description,
            type=f.type,
            required=f.required,
            fields=f.fields,
        )
        for f in req.fields
        if f.key.strip()
    ]
    if not fields:
        raise HTTPException(400, "Add at least one field with a name.")

    keys = [f.key for f in fields]
    if len(set(k.lower() for k in keys)) != len(keys):
        raise HTTPException(400, "Field names must be unique.")

    started = time.perf_counter()

    # OCR fresh on every scrape. This is the whole point: no document text is
    # ever carried over from an earlier run.
    try:
        ocr, ocr_ms = _run_ocr(req.document_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - report the real reason to the UI
        raise HTTPException(422, f"Could not read this document: {exc}") from exc

    if not ocr.get("text", "").strip():
        raise HTTPException(
            422, "No readable text was found in this document, so there is nothing to scrape."
        )

    try:
        word_scores = [
            (w["text"], w["confidence"])
            for page in ocr.get("pages", [])
            for w in page.get("words", [])
        ]
        results, meta = extractor.extract_fields(
            fields,
            ocr["text"],
            model=req.model,
            document_id=req.document_id,
            word_scores=word_scores,
        )
    except extractor.LLMError as exc:
        raise HTTPException(502, str(exc)) from exc

    field_list = [
        {
            "key": k,
            "value": v["value"],
            "confidence": v["confidence"],
            "type": v["type"],
            "evidence": v["evidence"],
            "ocr_confidence": v.get("ocr_confidence"),
            "ocr_warning": v.get("ocr_warning"),
        }
        for k, v in results.items()
    ]

    avg = (
        sum(f["confidence"] for f in field_list) / len(field_list) if field_list else 0.0
    )

    raw = meta.get("raw") or ""
    diagnostics = {
        "ocr_ms": ocr_ms,
        "ocr_confidence": ocr.get("confidence", 0),
        "ocr_pages": len(ocr.get("pages", [])),
        "ocr_chars": len(ocr.get("text", "")),
        "llm_ms": meta.get("duration_ms", 0),
        "total_ms": int((time.perf_counter() - started) * 1000),
        "attempts": meta.get("attempts"),
        "repaired": meta.get("repaired"),
        "think": meta.get("think"),
        "num_ctx": meta.get("num_ctx"),
        "prompt_chars": meta.get("prompt_chars"),
        "response_chars": meta.get("response_chars"),
        "prompt_tokens": meta.get("prompt_tokens"),
        "output_tokens": meta.get("output_tokens"),
        "tokens_per_second": meta.get("tokens_per_second"),
        "load_ms": meta.get("load_ms"),
        "prompt_eval_ms": meta.get("prompt_eval_ms"),
        "generate_ms": meta.get("generate_ms"),
        "wall_ms": meta.get("wall_ms"),
        "first_token_ms": meta.get("first_token_ms"),
        "runs": meta.get("runs"),
        "verified": meta.get("verified"),
        "total_fields": meta.get("total_fields"),
    }
    diagnostics = {k: v for k, v in diagnostics.items() if v is not None}

    response = {
        "document_id": req.document_id,
        "model": meta.get("model"),
        "average_confidence": round(avg, 3),
        "ocr_confidence": ocr.get("confidence", 0),
        "fields": field_list,
        "fields_by_name": {f["key"]: f["value"] for f in field_list},
        "diagnostics": diagnostics,
        # The model's own reply, kept short: enough to see a bad answer, not a
        # second copy of the whole document.
        "raw_response": raw[:2000] + ("…" if len(raw) > 2000 else ""),
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


@app.post("/api/feature-requests")
def create_feature_request(payload: dict):
    """Record a feature request so an automation can pick it up.

    Stored as a plain JSON file rather than in a database: the file is the
    hand-off to whoever (or whatever) implements the request, so it has to be
    readable outside this process.
    """
    title = (payload.get("title") or "").strip()
    detail = (payload.get("detail") or "").strip()
    kind = (payload.get("kind") or "feature").strip()
    if not title:
        raise HTTPException(422, "A title is required.")
    if len(title) > 200:
        raise HTTPException(422, "Keep the title under 200 characters.")

    folder = settings.data_dir / "feature-requests"
    folder.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex[:12]
    record = {
        "id": request_id,
        "title": title,
        "detail": detail,
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # What the app was doing when this was filed, so whoever investigates
        # does not have to guess the environment.
        "app": {
            "model": settings.llm_model,
            "static_version": settings.app_version,
        },
        "status": "new",
    }
    (folder / f"{request_id}.json").write_text(json.dumps(record, indent=2))
    return {"request": record}


@app.get("/api/feature-requests")
def list_feature_requests():
    folder = settings.data_dir / "feature-requests"
    records = []
    if folder.exists():
        for path in sorted(folder.glob("*.json")):
            try:
                records.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"requests": records[:50]}


# Static UI last so /api/* wins. html=True serves index.html at /.
if settings.static_dir.exists():
    app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="static")
