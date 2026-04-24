"""Thin wrapper around pdfplumber.

Returns both the linear text (for line-based parsing) and the per-page
tables (for tabular sources like the Riverside County EH PDF).
"""
import pdfplumber
from dataclasses import dataclass


@dataclass
class PdfPage:
    page_number: int
    text: str
    tables: list[list[list[str]]]


def extract_pdf(path: str) -> list[PdfPage]:
    pages: list[PdfPage] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            pages.append(PdfPage(page_number=i, text=text, tables=tables))
    return pages


def all_text(pages: list[PdfPage]) -> str:
    return "\n".join(p.text for p in pages)
