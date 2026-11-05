# invoice-pdf-to-sheet

Supplier invoice PDFs → one row per invoice in a spreadsheet with a **Ready** tab and a **Needs review** tab. Every row carries its evidence; every review row says exactly why.

Built for the bookkeeper who types supplier invoices into a sheet by hand: they check the review tab instead of retyping everything.

All data in this repo is generated mock data (fictional coffee roastery and 6 invented suppliers).

## What it does

1. Reads every PDF in a folder. Text PDFs go to Claude as text; scanned (image-only) PDFs go as page images.
2. Claude (`claude-sonnet-5-5`) fills a strict Pydantic schema: supplier, VAT id, number, dates, totals, lines, plus a verbatim quote per key field.
3. Code — not the model — checks every invoice:
   - lines add up to net, net + VAT = gross (±0.01)
   - dates are real ISO dates, not in the future, due ≥ invoice date
   - supplier VAT id present, valid format, and not the buyer's own
   - evidence quotes exist on the page and contain the extracted invoice number and gross amount
   - duplicates within the batch (same supplier + invoice number)
   - file limits: ≤ 10 MB, ≤ 5 pages, readable PDF
4. Writes `.xlsx` (always) and, with `--google`, a Google Sheet + Drive folder of the PDFs, each row linking to its PDF.

Failures never stop the batch: a file that can't be read or extracted (after 1 retry) becomes a review row with the reason.

## Run

```bash
uv sync
export ANTHROPIC_API_KEY=...            # never committed
uv run invoice-pdf-to-sheet run --input data/invoices --xlsx out/invoices.xlsx
uv run invoice-pdf-to-sheet eval --xlsx out/invoices.xlsx --key data/answer_key.json
# optional Google output (scope: drive.file only — the app sees only files it created)
uv run invoice-pdf-to-sheet run --input data/invoices --xlsx out/invoices.xlsx --google
```

| Env var | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Claude API |
| `INVOICE_BUYER_VAT_ID` | `LT999000099` | your own VAT id (a supplier row carrying it is flagged) |
| `GOOGLE_OAUTH_CLIENT_FILE` | `~/.config/portfolio-demos/client_secret.json` | OAuth desktop client |
| `GOOGLE_TOKEN_FILE` | `~/.config/portfolio-demos/token-invoice-pdf-to-sheet.json` | cached token (0600) |

## Mock data and measurement

`mock/make_invoices.py` regenerates `data/invoices/` (30 PDFs, 6 layouts incl. German labels and comma decimals) and `data/answer_key.json`. Planted cases: one scanned invoice, one re-sent duplicate, one invoice whose lines don't add up, one with no supplier VAT id, one with a prompt-injection note in the footer.

`eval` scores 10 header fields × 30 invoices (field-level exact match) and routing (right tab). Results: pending the first real run.

## Safety

- Invoice text reaches the model only inside one `<user_input>` block with `<`/`>` escaped, never in the system prompt; model output is schema-validated.
- Logs carry file names, counts, statuses and reason codes only — never invoice text, values or provider error messages.
- Spreadsheet cells are written as text (no formula injection from invoice content).
- Nothing is sent or shared; the Google output lands in a new private folder.

## Known limits

- Dates are not matched against their evidence quote (too many printed formats); invoice number and gross total are.
- A model refusal shows up as `extraction failed: ValidationError` (the SDK validates the structured output before the stop reason can be read).
- Duplicates are detected within one batch only — there is no ledger across runs.
- VAT ids are format-checked only (no VIES lookup).
- Prompt injection inside page images (scans) is not detected; the output is still schema-validated and checked like any other.
- The Google path is tested against fakes; the first real call happens on the first `--google` run.

## Development

`make check` (ruff, mypy strict, security scans, tests) · `make test` · `make fix`. Spec and acceptance criteria: `specs/invoice-pdf-to-sheet.md`.
