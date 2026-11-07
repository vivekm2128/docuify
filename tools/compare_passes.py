#!/usr/bin/env python3
"""Compare the two-pass settings honestly, on all five samples.

Runs the real extraction path twice: once as the fast path alone, once with a
second model verifying the fields the fast pass could not ground. Reports
accuracy, latency and how often the second pass actually fired.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, extractor  # noqa: E402
from app.ocr import extract as run_ocr  # noqa: E402
from tools import eval_extraction as harness  # noqa: E402


def fields_for(template_id: str):
    templates = harness.load_templates()
    return [extractor.FieldSpec(**f) for f in templates[template_id]["fields"]]


def run(label: str, fallback: str) -> None:
    config.settings.llm_fallback_model = fallback
    correct = total = 0
    times: list[float] = []
    fired = 0
    print(f"\n=== {label} (fallback={fallback or 'off'}) ===")

    for name, (filename, template, expected) in harness.CASES.items():
        path = harness.SAMPLES / filename
        fields = fields_for(template)
        ocr = run_ocr(path)
        results, meta = extractor.extract_fields(fields, ocr.text)
        got = {k: v.get("value") for k, v in results.items()}
        meta = meta or {}
        runs = meta.get("runs") or [{}]
        if len(runs) > 1:
            fired += 1
        times.append(sum(float(r.get("wall_ms") or 0) for r in runs) / 1000)

        hits = sum(1 for k, want in expected.items() if k in got and harness.match(got[k], want))
        correct += hits
        total += len(expected)
        misses = [k for k, want in expected.items() if not (k in got and harness.match(got[k], want))]
        print(f"  {name:<22} {hits:>2}/{len(expected)}  {times[-1]:>6.1f}s  "
              f"runs={len(runs)}  misses={misses}")

    print(f"  -> {correct}/{total} = {correct / total:.1%}"
          f"  median {statistics.median(times):.1f}s  max {max(times):.1f}s"
          f"  escalate-rate {fired}/5")


if __name__ == "__main__":
    run("fast path only", "")
    run("fast path + verifier", sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:7b")
