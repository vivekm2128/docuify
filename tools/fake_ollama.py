"""Fake Ollama server for local testing on a machine without a real model.

Answers /api/tags and /api/generate so the whole app can be exercised
end to end. It "extracts" fields with simple heuristics instead of an LLM.

    python tools/fake_ollama.py --port 11434
    DOCUFY_LLM_URL=http://127.0.0.1:11434 python run.py
"""

from __future__ import annotations

import argparse
import json
import re

from fastapi import FastAPI, Request

app = FastAPI()

MODEL = "qwen3:8b"


def guess(key: str, doc: str) -> dict:
    """Very small heuristics so the fake model returns plausible values."""
    low_doc = doc.lower()
    key_l = key.lower()

    def num(pattern: str):
        m = re.search(pattern, doc, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(",", "")
            return float(raw) if "." in raw else int(raw), m.group(0)
        return None, ""

    if "total" in key_l or "amount" in key_l:
        v, ev = num(r"(?:total|amount|sum|due)[^\d\n]{0,20}([\d,]+(?:\.\d+)?)")
        return {"value": v, "confidence": 0.95 if v is not None else 0.0, "evidence": ev}
    if "number" in key_l or "invoice_no" in key_l or "ref" in key_l:
        m = re.search(r"(?:invoice|no\.?|number|ref)[^\w\n]{0,5}([A-Z0-9][A-Z0-9\-/]{2,})", doc, re.IGNORECASE)
        return {"value": m.group(1) if m else None, "confidence": 0.9 if m else 0.0, "evidence": m.group(0) if m else ""}
    if "date" in key_l:
        m = re.search(r"\d{4}-\d{2}-\d{2}", doc)
        return {"value": m.group(0) if m else None, "confidence": 0.88 if m else 0.0, "evidence": m.group(0) if m else ""}
    if "vendor" in key_l or "supplier" in key_l or "company" in key_l:
        m = re.search(r"(?:vendor|supplier|from|company)\s*:?\s*([A-Z][A-Za-z0-9&.,\- ]{2,40})", doc)
        return {"value": m.group(1).strip() if m else None, "confidence": 0.86 if m else 0.0, "evidence": m.group(0) if m else ""}
    if "paid" in key_l:
        m = re.search(r"\b(paid|unpaid|paid in full)\b", low_doc)
        if m:
            return {"value": not m.group(1).startswith("un"), "confidence": 0.8, "evidence": m.group(1)}
        return {"value": None, "confidence": 0.0, "evidence": ""}
    if "currency" in key_l:
        m = re.search(r"\b(USD|EUR|GBP|CAD|AUD|JPY)\b", doc)
        return {"value": m.group(1) if m else None, "confidence": 0.92 if m else 0.0, "evidence": m.group(0) if m else ""}
    if "line" in key_l or "item" in key_l:
        items = re.findall(r"^\s*[-•]\s*(.+)$", doc, re.MULTILINE)
        return {"value": items[:10], "confidence": 0.7 if items else 0.0, "evidence": items[0] if items else ""}
    # Generic fallback: echo a short line mentioning the key, else null.
    for line in doc.splitlines():
        if key_l.replace("_", " ") in line.lower() or key_l in line.lower():
            return {"value": line.strip()[:120], "confidence": 0.45, "evidence": line.strip()}
    return {"value": None, "confidence": 0.1, "evidence": ""}


@app.get("/api/tags")
def tags():
    return {"models": [{"name": MODEL, "modified_at": "2026-01-01T00:00:00Z", "size": 4_000_000_000}]}


@app.post("/api/generate")
async def generate(req: Request):
    body = await req.json()
    prompt = body.get("prompt", "")

    doc_match = re.search(r"<<<DOCUMENT\n(.*)\nDOCUMENT>>>", prompt, re.S)
    doc = doc_match.group(1) if doc_match else ""

    key_block = prompt.split("Fields:")[1].split("Rules:")[0] if "Fields:" in prompt else ""
    keys = re.findall(r'field "([^"]+)"', key_block)

    fields = {k: guess(k, doc) for k in keys}
    payload = json.dumps({"fields": fields})
    return {
        "model": body.get("model", MODEL),
        "created_at": "2026-01-01T00:00:00Z",
        "response": payload,
        "done": True,
        "eval_count": 120,
        "eval_duration": 900_000_000,
    }


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11434)
    a = ap.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
