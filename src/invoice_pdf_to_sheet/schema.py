from pathlib import Path
from typing import Literal

from pydantic import BaseModel

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


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    line_total: float


class FieldQuote(BaseModel):
    field: str
    page: int
    quote: str


class InvoiceFields(BaseModel):
    supplier_name: str
    supplier_vat_id: str | None
    invoice_number: str
    invoice_date: str
    due_date: str | None
    currency: str
    net_total: float
    vat_total: float
    gross_total: float
    lines: list[LineItem]
    evidence: list[FieldQuote]


class ResultRow(BaseModel):
    file: str
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
        values["reasons"] = SEPARATOR.join(
            r.replace(SEPARATOR, SAFE) for r in self.reasons
        )
        return [values[column] for column in COLUMNS[:-1]]
