#!/usr/bin/env bash
# Start Docufy, and Ollama too if it isn't already running.
#
#   ./start.sh                 # http://127.0.0.1:8765
#   ./start.sh --port 8800 --model qwen3.5:4b-mlx
#
# Why the environment variable below:
# on Apple M5 Macs with macOS 26.2+, ggml's Metal kernels fail to compile
# ("static_assert failed ... Input types must match cooperative tensor types"),
# so no model loads at all. Setting GGML_METAL_TENSOR_DISABLE=1 turns off the
# Metal tensor path and llama.cpp starts normally on the GPU. It is a no-op on
# other chips, so it is safe to leave set.

set -euo pipefail
cd "$(dirname "$0")"

export GGML_METAL_TENSOR_DISABLE="${GGML_METAL_TENSOR_DISABLE:-1}"

if [[ ! -d .venv ]]; then
  echo "Creating the Python environment…"
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

if ! curl -s --max-time 2 "${DOCUFY_LLM_URL:-http://127.0.0.1:11434}/api/version" >/dev/null 2>&1; then
  echo "Starting Ollama (with the Metal tensor workaround)…"
  nohup env GGML_METAL_TENSOR_DISABLE="$GGML_METAL_TENSOR_DISABLE" ollama serve \
    > /tmp/docufy-ollama.log 2>&1 < /dev/null &
  for _ in $(seq 1 40); do
    if curl -s --max-time 1 "${DOCUFY_LLM_URL:-http://127.0.0.1:11434}/api/version" >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done
fi

exec ./.venv/bin/python run.py "$@"
