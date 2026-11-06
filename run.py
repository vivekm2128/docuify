#!/usr/bin/env python3
"""Start Docufy.

    python run.py                 # http://127.0.0.1:8765
    python run.py --port 9000
    python run.py --model qwen3:4b
    python run.py --reload        # auto-restart while editing backend code

Everything runs on this machine: OCR via onnxruntime, the LLM via Ollama.
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Docufy server locally.")
    parser.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="port (default 8765)")
    parser.add_argument("--model", default=None, help="Ollama model name, e.g. qwen3:8b")
    parser.add_argument("--llm-url", default=None, help="Ollama base URL (default http://127.0.0.1:11434)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--reload", action="store_true", help="reload on code changes (development)")
    args = parser.parse_args()

    # apply overrides before the settings module reads the environment
    if args.host:
        os.environ["DOCUFY_HOST"] = args.host
    if args.port:
        os.environ["DOCUFY_PORT"] = str(args.port)
    if args.model:
        os.environ["DOCUFY_LLM_MODEL"] = args.model
    if args.llm_url:
        os.environ["DOCUFY_LLM_URL"] = args.llm_url.rstrip("/")

    try:
        import uvicorn
    except ImportError:
        print("Missing dependencies. Run:\n\n  pip install -r requirements.txt\n", file=sys.stderr)
        return 1

    from app.config import settings

    url = f"http://{settings.host}:{settings.port}"
    root = Path(__file__).resolve().parent

    if not args.no_browser:
        # Give uvicorn a moment to bind before the browser opens.
        import threading
        import time

        def open_later() -> None:
            time.sleep(1.4)
            webbrowser.open(url)

        threading.Thread(target=open_later, daemon=True).start()

    print(f"\n  Docufy running at {url}")
    print(f"  model: {settings.llm_model} via {settings.llm_base_url}")
    print("  press Ctrl+C to stop\n")

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        reload_dirs=[str(root / "app")] if args.reload else None,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
