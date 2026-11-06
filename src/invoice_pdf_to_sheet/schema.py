from pathlib import Path
from typing import Annotated, Literal

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from pydantic import AfterValidator, BaseModel

COLUMNS = [
    "file",
    "supplier_name",
    "supplier_vat_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "net_total",
    "vat_total",
    "gross_total",
    "line_count",
    "method",
    "confidence",
    "evidence",
    "reasons",
    "pdf_link",
]


# "; " joins reasons and evidence quotes in one cell. Inside a value (raw VAT id, date, file name, quote) it is written
# with a no-break space, which looks the same but can't be split on, so printed text can't forge an extra entry.
SEPARATOR = "; "
SAFE = ";\u00a0"


def clean(text: str) -> str:
    """Every value read from a PDF or a file name passes here: drop characters a spreadsheet can't hold first (so
    removing them can't glue a separator back together), then neutralise the separator."""
    return str(ILLEGAL_CHARACTERS_RE.sub("", text)).replace(SEPARATOR, SAFE)


Text = Annotated[str, AfterValidator(clean)]


class LineItem(BaseModel):
    description: Text
    quantity: float
    unit_price: float
    line_total: float


class FieldQuote(BaseModel):
    field: Text
    page: int
    quote: Text


class InvoiceFields(BaseModel):
    supplier_name: Text
    supplier_vat_id: Text | None
    invoice_number: Text
    invoice_date: Text
    due_date: Text | None
    currency: Text
    net_total: float
    vat_total: float
    gross_total: float
    lines: list[LineItem]
    evidence: list[FieldQuote]


class ResultRow(BaseModel):
    file: Text
    supplier_name: str | None = None
    supplier_vat_id: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    net_total: float | None = None
    vat_total: float | None = None
    gross_total: float | None = None
    line_count: int | None = None
    method: Literal["text", "vision", "none"] = "none"
    confidence: Literal["verified", "vision", "unverified", "none"] = "none"
    evidence: str = ""
    reasons: list[str] = []
    pdf_path: Path

    def cells(self) -> list[str | float | int | None]:
        """Row values in COLUMNS order, without pdf_link (each writer renders its own link)."""
        values = self.model_dump()
        values["reasons"] = SEPARATOR.join(clean(r) for r in self.reasons)
        return [values[column] for column in COLUMNS[:-1]]
