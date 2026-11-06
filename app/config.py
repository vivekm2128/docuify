"""Runtime configuration, read from environment variables or a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader so we don't need an extra dependency."""
    root = Path(__file__).resolve().parent.parent
    env_file = root / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Real environment variables win over the .env file.
        os.environ.setdefault(key, value)


_load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # --- Server ---
    host: str = os.environ.get("DOCUFY_HOST", "127.0.0.1")
    port: int = _int("DOCUFY_PORT", 8765)

    # --- LLM (Ollama-compatible API on your Mac) ---
    llm_base_url: str = os.environ.get("DOCUFY_LLM_URL", "http://127.0.0.1:11434")
    llm_model: str = os.environ.get("DOCUFY_LLM_MODEL", "qwen3:8b")
    llm_temperature: float = _float("DOCUFY_LLM_TEMPERATURE", 0.0)
    llm_num_ctx: int = _int("DOCUFY_LLM_NUM_CTX", 8192)
    llm_timeout: float = _float("DOCUFY_LLM_TIMEOUT", 300.0)
    # Keep the model resident in RAM between scrapes (much faster repeat runs).
    llm_keep_alive: str = os.environ.get("DOCUFY_LLM_KEEP_ALIVE", "30m")
    # Give the model a second chance when the first answer isn't valid JSON.
    llm_repair_attempts: int = _int("DOCUFY_LLM_REPAIR_ATTEMPTS", 1)

    # --- OCR ---
    # upscale page images before OCR: helps a lot on phone photos
    ocr_max_dim: int = _int("DOCUFY_OCR_MAX_DIM", 2200)
    ocr_min_confidence: float = _float("DOCUFY_OCR_MIN_CONFIDENCE", 0.35)
    pdf_render_scale: float = _float("DOCUFY_PDF_RENDER_SCALE", 2.0)
    max_pages: int = _int("DOCUFY_MAX_PAGES", 20)

    # --- Storage / limits ---
    data_dir: Path = Path(os.environ.get("DOCUFY_DATA_DIR", str(BASE_DIR / "data")))
    static_dir: Path = BASE_DIR / "static"
    max_upload_mb: int = _int("DOCUFY_MAX_UPLOAD_MB", 50)
    # Only the first N characters of OCR text are sent to the model.
    max_chars: int = _int("DOCUFY_MAX_CHARS", 14000)
    allowed_extensions: tuple[str, ...] = field(
        default=(
            ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
            ".gif", ".heic", ".heif",
        )
    )


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
