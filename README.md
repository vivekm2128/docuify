# Docufy

Point it at a document, tell it what to pull out, get clean JSON back with a
confidence score for every field. Everything runs on your Mac: OCR on CPU,
the LLM through your own Ollama install. Nothing leaves the machine.

![Docufy](samples/ui-9-final.png)

## What it does

1. **Upload** a PDF, scan, photo, or screenshot (PDF, PNG, JPG, WEBP, TIFF, GIF, HEIC).
2. **Read it** with local OCR. Text boxes are reassembled into real lines and
   columns, so the model sees `TOTAL DUE   USD 597.52` instead of scrambled words.
3. **Define fields** — name, a plain-English description, and a type
   (`string`, `number`, `integer`, `boolean`, `date`, `array`, `object`).
4. **Scrape** — the app sends the OCR text plus your field definitions to your
   local model and gets back structured JSON.
5. **Inspect** — every field comes back with a value, a confidence score, and
   the exact snippet it came from. Hover a result and that snippet lights up
   on the document.

## Setup on your Mac (Apple Silicon)

One-time setup, roughly two minutes.

```bash
cd docufy

# 1. Python environment (3.10+; 3.12 is ideal)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Ollama (skip if already installed)
brew install ollama          # or download from ollama.com
#    ...or grab the app from https://ollama.com/download/mac
```

Now check which model you already have:

```bash
ollama list
```

Use whatever that shows. If you don't recognise the name, a good default for
this job is Qwen:

```bash
ollama pull qwen3:8b        # ~5 GB, great balance for an M-series Mac
```

Other good picks:

| Model | Size | Notes |
|---|---|---|
| `qwen3:4b` | ~2.5 GB | fastest, fine for invoices/receipts |
| `qwen3:8b` | ~5 GB | the sweet spot |
| `qwen3:14b` | ~9 GB | better on messy scans, needs 16 GB+ RAM |
| `llama3.2:3b` | ~2 GB | lightest usable option |

Avoid reasoning models for bulk work if speed matters; they spend time
"thinking" before answering.

## Run it

Copy the folder to your Mac, then in Terminal:

```bash
cd ~/docufy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the model server (skip if the Ollama menu-bar app is running):

```bash
ollama serve
```

Then start Docufy in a second Terminal:

```bash
cd ~/docufy
source .venv/bin/activate
python run.py --model qwen3:8b     # use a name from `ollama list`
```

Your browser opens at **http://127.0.0.1:8765**. First thing to do: click
**Test model** in the top-right corner. Green across the board means your
local model is reading documents correctly. Tap ⌘C when you're done.

Point it at the model you actually have:

```bash
python run.py --model qwen3:4b
```

Or pin it permanently in a `.env` file (see `env.example`).

### Options

```bash
python run.py --port 9000            # different port
python run.py --model qwen3:14b      # different model
python run.py --no-browser           # don't auto-open the browser
python run.py --reload               # dev mode, restarts on code changes
```

## Using the app

- **Fields tab** — hit **+ Add field**, or use a Quick add chip. Name it the way
  you want the JSON key to look (`invoice_total`, not `Invoice Total`; names are
  normalised to snake_case for you). The description is what actually makes
  extraction accurate, so be specific: *"total amount payable including tax"*
  beats *"total"*.
- **Scrape document** — or press <kbd>⌘</kbd>+<kbd>Enter</kbd>.
- **Results tab** — every field comes back **green (PASS)** or **red (FAIL)**
  against a **pass threshold you control** with the slider at the top of the
  panel. Drag it and the verdicts update instantly. A field the model couldn't
  find shows as grey **MISSING** and never passes. Click a card to copy its
  value. Hover to see where it came from on the page.
- **API tab** — the exact request and response JSON, with copy and download.
- **Test model** (top right) — sends a tiny built-in invoice with known answers
  to your real local model and shows a green/red check per field, using the same
  pass threshold. Do this first on your Mac; it's the fastest way to prove the
  whole chain works before you trust it on real documents.

Viewer controls: <kbd>⌘</kbd>/<kbd>Ctrl</kbd> + scroll to zoom, <kbd>⌘</kbd>+<kbd>0</kbd>
to fit, <kbd>⌘</kbd>+<kbd>K</kbd> to add a field. Thumbnails under the viewer
jump between pages.

## Confidences and pass/fail

Each score is the model's own estimate that the value is correct, and the
server clamps it to 0..1. A field **passes** when its confidence is at or above
the pass threshold in the Results tab (default 80%). Set it once and it
persists for the session; the Test-model dialog uses the same line.

The default 80% is a reasonable starting point:

- **95–100%** — plainly stated in the text; trust it
- **80–94%** — passes by default, still worth skimming
- **50–79%** — fails by default; check by hand
- **below 50%** — the model is guessing
- **null / MISSING** — the field isn't in the document; never counts as a pass

A high score can still be wrong. The evidence snippet is the better signal: if
none is shown, the model's quoted text didn't appear in the OCR output and the
server discarded it.

## API

Everything the UI does is a plain HTTP call, so you can script it.

```bash
# 1. Upload -> OCR runs immediately
curl -s -X POST http://127.0.0.1:8765/api/documents \
  -F "file=@invoice.pdf"
# -> {"document": {"document_id": "..."}, "ocr": {...}}

# 2. Scrape fields
curl -s -X POST http://127.0.0.1:8765/api/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "PASTE-ID-HERE",
    "fields": [
      {"key": "invoice_total", "description": "Total amount payable including tax", "type": "number"},
      {"key": "vendor_name",   "description": "Company issuing the invoice",          "type": "string"},
      {"key": "is_paid",       "description": "Whether the invoice is marked paid",   "type": "boolean"},
      {"key": "line_items",    "description": "Each purchased line item",             "type": "array"}
    ]
  }'
```

Response:

```json
{
  "document_id": "…",
  "model": "qwen3:8b",
  "cached": false,
  "llm_ms": 2140,
  "total_ms": 2180,
  "average_confidence": 0.87,
  "ocr_confidence": 0.98,
  "fields": [
    {
      "key": "invoice_total",
      "value": 597.52,
      "confidence": 0.95,
      "type": "number",
      "evidence": "TOTAL DUE  USD 597.52"
    }
  ],
  "fields_by_name": { "invoice_total": 597.52 }
}
```

| Endpoint | Purpose |
|---|---|
| `POST /api/documents` | Upload + OCR. Returns `document_id`. |
| `POST /api/scrape` | Extract fields. Add `"refresh": true` to bypass the cache. |
| `GET /api/documents/{id}/ocr` | OCR text plus word boxes (used for highlighting). |
| `GET /api/documents/{id}/pages/{n}.png` | Rendered page image. |
| `GET /api/documents/{id}/file` | The original upload. |
| `GET /api/health` | Model status, installed models, allowed types. |
| `POST /api/self-check` | Run the model on a built-in document with known answers. |
| `GET /api/docs` | Interactive API docs. |

Repeating a scrape with the same document and fields returns the cached answer
(`"cached": true`) — hit the button again to re-run the model.

## Configuration

Copy `env.example` to `.env` and edit. Every setting also works as an
environment variable.

| Variable | Default | Meaning |
|---|---|---|
| `DOCUFY_LLM_MODEL` | `qwen3:8b` | Ollama model name |
| `DOCUFY_LLM_URL` | `http://127.0.0.1:11434` | Ollama address |
| `DOCUFY_LLM_TIMEOUT` | `300` | seconds before giving up |
| `DOCUFY_MAX_CHARS` | `14000` | OCR characters sent to the model |
| `DOCUFY_MAX_PAGES` | `20` | pages OCR'd per document |
| `DOCUFY_OCR_MAX_DIM` | `2200` | page upscale size before OCR |
| `DOCUFY_MAX_UPLOAD_MB` | `50` | upload size limit |
| `DOCUFY_PORT` | `8765` | server port |

## Troubleshooting

**"Ollama offline"** — Run `ollama serve`, or open the Ollama app. Confirm with
`curl http://127.0.0.1:11434/api/tags`.

**"Model 'qwen3:8b' not pulled"** — `ollama pull qwen3:8b`, or run with
`--model <something from ollama list>`.

**Scrapes are slow** — The first scrape loads the model into RAM (10–30s);
later ones are much faster because it stays resident. If it's still slow, try
`qwen3:4b`. Raising `DOCUFY_LLM_NUM_CTX` makes it slower, so don't unless you
need to send very long documents.

**Values are wrong or "not found"** —
- Make descriptions specific about format and location.
- Long documents get truncated at `DOCUFY_MAX_CHARS`; raise it (and
  `DOCUFY_LLM_NUM_CTX`) if your document is longer.
- OCR errors on faint scans are the usual culprit. Check **API** tab → nothing
  shows OCR text, so use `GET /api/documents/{id}/ocr` to see what the model saw.
- Bigger models read messy scans noticeably better.

**HEIC photos fail** — `pip install pillow-heif` (already in
`requirements.txt`).

**Port already in use** — `python run.py --port 9000`.

## Project layout

```
docufy/
  run.py                 # launcher
  app/
    config.py            # settings from env / .env
    main.py              # FastAPI routes
    ocr.py               # page rendering + RapidOCR + line reconstruction
    extractor.py         # prompt building, JSON repair, type coercion, confidence
  static/
    index.html           # the UI
    style.css
    app.js
  tools/fake_ollama.py   # stand-in model for testing without Ollama
  samples/               # sample invoice + UI screenshots
```

## Testing without a local model

`tools/fake_ollama.py` answers the Ollama API with simple regex heuristics, so
you can check the app end to end before downloading a model. It is not an LLM
and its values are crude.

```bash
python tools/fake_ollama.py &                     # terminal 1
DOCUFY_LLM_URL=http://127.0.0.1:11434 python run.py   # terminal 2
```

## Privacy

Documents are stored under `data/documents/` on your disk, and the model is
local, so nothing is uploaded anywhere. Delete that folder to clear history.
