#!/usr/bin/env bash
# Read every invoice PDF in a folder, score the result against the answer key and open the results page.
# usage: tools/demo.sh [invoice_folder] [answer_key.json]   (defaults: data/invoices data/answer_key.json)
set -euo pipefail
cd "$(dirname "$0")/.."
input="${1:-data/invoices}"; key="${2:-data/answer_key.json}"
if [ -z "${ANTHROPIC_API_KEY:-}" ] && command -v security >/dev/null; then
  ANTHROPIC_API_KEY="$(security find-generic-password -a portfolio-demos -s ANTHROPIC_API_KEY -w)"
  export ANTHROPIC_API_KEY
fi
uv run invoice-pdf-to-sheet run --input "$input" --xlsx out/invoices.xlsx
uv run invoice-pdf-to-sheet eval --xlsx out/invoices.xlsx --key "$key"
uv run python tools/view_results.py out/invoices.xlsx --key "$key" --out out/results.html
open out/results.html 2>/dev/null || xdg-open out/results.html
