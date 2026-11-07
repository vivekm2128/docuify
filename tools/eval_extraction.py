#!/usr/bin/env python3
"""End-to-end accuracy + latency check for Docufy.

Sends every sample document through the real server (real OCR, real model call)
using the same field templates the UI ships, then scores the answers against
known values baked into the generated samples.

    # with the server running on :8765
    python tools/eval_extraction.py --port 8765

    # or in-process (no server needed)
    python tools/eval_extraction.py --in-process
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLES = ROOT / "samples"

# (sample file, template id, expected values). Values use the same types the
# template declares; numbers compare within a cent, strings case-insensitively.
CASES = {
    "invoice": (
        "sample-invoice.pdf",
        "invoice",
        {
            "vendor_name": "NORTHWIND TRADING CO.",
            "invoice_number": "INV-2026-1042",
            "invoice_date": "2026-02-14",
            "due_date": "2026-03-16",
            "total_amount": 13028.25,
            "currency": "USD",
            "purchase_order": "PO-88214",
            "is_paid": False,
        },
    ),
    "paystub": (
        "sample-paystub.pdf",
        "paystub",
        {
            "employee_name": "ABAYOMI, CHIDERA N.",
            "employer_name": "MERIDIAN HEALTH SYSTEMS",
            "pay_date": "2026-02-20",
            "pay_period_start": "2026-02-01",
            "pay_period_end": "2026-02-15",
            "gross_pay": 4904.97,  # the bold GROSS PAY line, not the YTD column
            "net_pay": 3132.54,
            "regular_hours": 72.0,
            "ytd_gross": 23837.29,  # GROSS PAY row, YTD column
        },
    ),
    "bank_statement": (
        "sample-bank-statement.pdf",
        "bank_statement",
        {
            "account_holder": "ABAYOMI, CHIDERA N.",
            "bank_name": "FIRST CASCADE BANK",
            "account_number": "4417",
            "statement_start": "2026-01-01",
            "statement_end": "2026-01-31",
            "beginning_balance": 8412.55,
            "ending_balance": 11904.12,
        },
    ),
    "lending_application": (
        "sample-lending-application.pdf",
        "lending_application",
        {
            "borrower_name": "ABAYOMI, CHIDERA N.",
            "loan_amount": 489600.00,
            "purchase_price": 612000.00,
            "down_payment": 122400.00,
            "monthly_income": 8882.00,
            "credit_score": 748,
            "interest_rate": 6.625,  # a dropped decimal reads as 32.4 or 3.4
            "property_address": "4820 NE Fremont St, Portland, OR 97213",
        },
    ),
    "lending_scan": (
        "sample-lending-application-scan.pdf",
        "lending_application",
        {
            "borrower_name": "Chidera Abayomi",
            "loan_amount": 489600.00,
            "purchase_price": 612000.00,
            "down_payment": 122400.00,
            "monthly_income": 8470.00,
            "credit_score": 748,
        },
    ),
}


def match(got, want) -> bool:
    if got is None:
        return False
    if isinstance(want, bool):
        return got is want
    if isinstance(want, (int, float)):
        try:
            return abs(float(got) - float(want)) < 0.01
        except (TypeError, ValueError):
            return False
    g = str(got).lower()
    w = str(want).lower()
    # Account numbers and the like appear with punctuation noise next to them.
    if all(ch.isdigit() for ch in w.replace(" ", "")):
        return w.replace(" ", "") in g.replace(" ", "").replace("·", "")
    return w in g or g in w


def run_in_process(templates, verbose: bool) -> list[dict]:
    from app import extractor
    from app.ocr import extract as run_ocr

    rows = []
    for name, (filename, template_id, expected) in CASES.items():
        path = SAMPLES / filename
        fields = [extractor.FieldSpec(**f) for f in templates[template_id]["fields"]]
        t0 = time.perf_counter()
        ocr = run_ocr(path)
        ocr_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        results, meta = extractor.extract_fields(fields, ocr.text)
        llm_ms = (time.perf_counter() - t1) * 1000
        rows.append(
            {
                "name": name,
                "ocr_ms": round(ocr_ms),
                "llm_ms": round(llm_ms),
                "oc_tokens": meta.get("output_tokens"),
                "got": {k: v["value"] for k, v in results.items()},
            }
        )
    return rows


def run_http(base: str, templates) -> list[dict]:
    import httpx

    rows = []
    for name, (filename, template_id, expected) in CASES.items():
        path = SAMPLES / filename
        with path.open("rb") as fh:
            up = httpx.post(f"{base}/api/documents", files={"file": (filename, fh, "application/pdf")}, timeout=120)
        up.raise_for_status()
        doc_id = up.json()["document"]["document_id"]
        body = {
            "document_id": doc_id,
            "fields": templates[template_id]["fields"],
        }
        r = httpx.post(f"{base}/api/scrape", json=body, timeout=600)
        r.raise_for_status()
        data = r.json()
        d = data["diagnostics"]
        rows.append(
            {
                "name": name,
                "ocr_ms": d["ocr_ms"],
                "llm_ms": d["llm_ms"],
                "oc_tokens": d.get("output_tokens"),
                "got": data["fields_by_name"],
                "diagnostics": d,
            }
        )
    return rows


def load_templates() -> dict:
    sys.path.insert(0, str(ROOT / "static"))
    # Templates live in the frontend as plain JSON so there is one source of
    # truth for both the UI and this harness.
    return json.loads((ROOT / "static" / "templates.json").read_text(encoding="utf-8"))["templates"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8765")
    ap.add_argument("--in-process", action="store_true")
    args = ap.parse_args()

    templates = load_templates()
    if args.in_process:
        rows = run_in_process(templates, True)
    else:
        rows = run_http(f"http://127.0.0.1:{args.port}", templates)

    # A template that lost a field would otherwise look like a model miss.
    problems = []
    for name, (_f, template_id, expected) in CASES.items():
        keys = {f["key"] for f in templates[template_id]["fields"]}
        for k in expected:
            if k not in keys:
                problems.append(f"{template_id} template is missing field '{k}' (needed by {name})")
    if problems:
        print("TEMPLATE PROBLEM:\n  " + "\n  ".join(problems))
        return 2

    total_ok = total = 0
    for row in rows:
        _file, _tid, expected = CASES[row["name"]]
        checks = {k: match(row["got"].get(k), v) for k, v in expected.items()}
        ok = sum(checks.values())
        total_ok += ok
        total += len(checks)
        print(f"\n{row['name']}  ocr={row['ocr_ms']}ms llm={row['llm_ms']}ms "
              f"out_tok={row['oc_tokens']}  {ok}/{len(checks)}")
        for k, good in checks.items():
            mark = "ok  " if good else "MISS"
            got = row["got"].get(k, "<key absent from response>")
            print(f"   [{mark}] {k}: got={got!r} want={expected[k]!r}")

    print(f"\n=== accuracy {total_ok}/{total} = {100 * total_ok / max(total,1):.1f}%")
    llms = [r["llm_ms"] for r in rows]
    ocrs = [r["ocr_ms"] for r in rows]
    print(f"=== llm median {statistics.median(llms):.0f}ms  max {max(llms):.0f}ms")
    print(f"=== ocr median {statistics.median(ocrs):.0f}ms  max {max(ocrs):.0f}ms")
    return 0 if total_ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
