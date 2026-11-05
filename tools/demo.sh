#!/usr/bin/env bash
# Read every invoice PDF in a folder, score it against an answer key (if any) and open the results page.
# usage: tools/demo.sh                              (the 30 test invoices + their answer key)
#        tools/demo.sh <invoice folder> [answer_key.json]
set -euo pipefail
cd "$(dirname "$0")/.."
if [ $# -eq 0 ]; then input=data/invoices; key=data/answer_key.json; else input="$1"; key="${2:-}"; fi
if [ -z "${ANTHROPIC_API_KEY:-}" ] && command -v security >/dev/null; then
  ANTHROPIC_API_KEY="$(security find-generic-password -a portfolio-demos -s ANTHROPIC_API_KEY -w)"
  export ANTHROPIC_API_KEY
fi
uv run invoice-pdf-to-sheet run --input "$input" --xlsx out/invoices.xlsx
if [ -n "$key" ]; then
  uv run invoice-pdf-to-sheet eval --xlsx out/invoices.xlsx --key "$key"
  uv run python tools/view_results.py out/invoices.xlsx --key "$key" --out out/results.html
else
  uv run python tools/view_results.py out/invoices.xlsx --out out/results.html
fi
open out/results.html 2>/dev/null || xdg-open out/results.html
