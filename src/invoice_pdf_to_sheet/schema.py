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
        # a raw value inside a reason (VAT id, date, file name) may not carry the separator, or it could forge a reason
        values["reasons"] = "; ".join(r.replace(";", ",") for r in self.reasons)
        return [values[column] for column in COLUMNS[:-1]]
