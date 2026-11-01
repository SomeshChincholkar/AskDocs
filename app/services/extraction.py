"""In-memory text extraction for supported document formats."""

import asyncio
import io
import logging
from pathlib import Path

import fitz
import pandas as pd
from docx import Document
from google import genai
from google.genai import types

from app.config import Settings

logger = logging.getLogger(__name__)
MAX_PAGES = 300
MAX_ROWS = 20_000
ROWS_PER_PAGE = 20


class ExtractionError(Exception):
    """Raised when a document exceeds safe extraction limits."""


def _excel_pages(data: bytes, suffix: str) -> list[tuple[int, str]]:
    if suffix == ".csv":
        frame = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, nrows=MAX_ROWS + 1)
        sheets = [("CSV", frame)]
    else:
        sheets = list(
            pd.read_excel(
                io.BytesIO(data),
                sheet_name=None,
                dtype=str,
                keep_default_na=False,
                nrows=MAX_ROWS + 1,
            ).items()
        )

    page_number = 0
    total_rows = 0
    pages: list[tuple[int, str]] = []
    for sheet_name, frame in sheets:
        if len(frame.index) > MAX_ROWS:
            raise ExtractionError(f"Spreadsheet exceeds the {MAX_ROWS:,}-row limit.")
        total_rows += len(frame.index)
        if total_rows > MAX_ROWS:
            raise ExtractionError(f"Spreadsheet exceeds the {MAX_ROWS:,}-row limit.")
        columns = [str(column) for column in frame.columns]
        for offset in range(0, len(frame.index), ROWS_PER_PAGE):
            page_number += 1
            if page_number > MAX_PAGES:
                raise ExtractionError(f"Spreadsheet exceeds the {MAX_PAGES}-page limit.")
            lines = []
            for values in frame.iloc[offset : offset + ROWS_PER_PAGE].itertuples(
                index=False, name=None
            ):
                fields = [
                    f"{column}: {value}"
                    for column, value in zip(columns, values, strict=True)
                    if str(value).strip()
                ]
                if fields:
                    lines.append(f"Sheet: {sheet_name} | " + " | ".join(fields))
            if lines:
                pages.append((page_number, "\n".join(lines)))
    return pages


def _gemini_ocr(data: bytes, mime_type: str, settings: Settings) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required for image OCR.")
    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.llm_model,
        contents=[
            "Extract all readable text from this image. Return only the extracted text.",
            types.Part.from_bytes(data=data, mime_type=mime_type),
        ],
    )
    return response.text or ""


async def extract_document(
    data: bytes,
    filename: str,
    mime_type: str,
    settings: Settings,
) -> list[tuple[int, str]]:
    """Extract page-labeled text without persisting the uploaded bytes."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        pages: list[tuple[int, str]] = []
        with fitz.open(stream=data, filetype="pdf") as document:
            if len(document) > MAX_PAGES:
                raise ExtractionError(f"PDF exceeds the {MAX_PAGES}-page limit.")
            for index, page in enumerate(document, start=1):
                text = page.get_text().strip()
                if not text:
                    image = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    text = await asyncio.to_thread(
                        _gemini_ocr, image.tobytes("png"), "image/png", settings
                    )
                pages.append((index, text.strip()))
        return pages
    if suffix == ".docx":
        document = Document(io.BytesIO(data))
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
        tables = [
            " | ".join(cell.text.strip() for row in table.rows for cell in row.cells)
            for table in document.tables
        ]
        text = "\n".join(value for value in paragraphs + tables if value)
        return [(1, text)] if text else []
    if suffix in {".xlsx", ".xls", ".csv"}:
        return await asyncio.to_thread(_excel_pages, data, suffix)
    if suffix == ".txt":
        return [(1, data.decode("utf-8-sig", errors="replace"))]
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        text = await asyncio.to_thread(_gemini_ocr, data, mime_type, settings)
        return [(1, text)] if text.strip() else []
    logger.warning("Unsupported extraction extension: %s", suffix)
    return []
