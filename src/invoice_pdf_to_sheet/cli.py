import argparse
import importlib
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from loguru import logger

from invoice_pdf_to_sheet import extract
from invoice_pdf_to_sheet.checks import mark_duplicates
from invoice_pdf_to_sheet.evaluate import evaluate
from invoice_pdf_to_sheet.xlsx import write_xlsx


def run(input_dir: Path, xlsx: Path, google: bool, workers: int) -> int:
    files = sorted(input_dir.glob("*.pdf")) if input_dir.is_dir() else []
    if not files:
        print(f"no PDF files in {input_dir}", file=sys.stderr)
        return 2
    logger.info("run start: {} files", len(files))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda path: extract.extract_file(path), files))
    mark_duplicates(rows)
    ready = [row for row in rows if not row.reasons]
    review = [row for row in rows if row.reasons]
    for row in rows:
        logger.info(
            "{}: {} ({} reasons)",
            row.file,
            "review" if row.reasons else "ready",
            len(row.reasons),
        )
    write_xlsx(ready, review, xlsx)
    logger.info("run end: {} ready, {} review", len(ready), len(review))
    print(f"{len(rows)} invoices: {len(ready)} ready, {len(review)} needs review")
    print(f"xlsx: {xlsx}")
    if not google:
        return 0
    from google.auth.exceptions import GoogleAuthError
    from googleapiclient.errors import HttpError

    google_sheet = importlib.import_module("invoice_pdf_to_sheet.google_sheet")
    try:
        url = google_sheet.publish(ready, review)
    except (
        google_sheet.GoogleSetupError,
        GoogleAuthError,
        HttpError,
        OSError,
        ValueError,
        KeyError,
    ) as error:
        print(f"Google Sheet upload failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"Google Sheet: {url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="invoice-pdf-to-sheet")
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser(
        "run", help="extract a folder of invoice PDFs to a spreadsheet"
    )
    run_cmd.add_argument("--input", type=Path, required=True)
    run_cmd.add_argument("--xlsx", type=Path, required=True)
    run_cmd.add_argument("--google", action="store_true")
    run_cmd.add_argument("--workers", type=int, default=4)
    eval_cmd = commands.add_parser("eval", help="score an xlsx against the answer key")
    eval_cmd.add_argument("--xlsx", type=Path, required=True)
    eval_cmd.add_argument("--key", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "eval":
        print("\n".join(evaluate(args.xlsx, args.key)))
        return 0
    return run(args.input, args.xlsx, args.google, args.workers)
