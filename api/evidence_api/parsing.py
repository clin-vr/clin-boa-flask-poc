"""Parsing: bytes -> ParsedDocument. No SharePoint knowledge lives here.

Format notes for the design discussion:

  PDF       pdfplumber if present, pypdf as fallback. A PDF with no extractable
            text is almost certainly scanned, which needs OCR — flagged rather
            than silently returning empty text, because empty text would
            otherwise read as "the control statement isn't there."

  DOCX      python-docx. Paragraphs and table cells, since sign-off forms put
            most of their content in tables.

  DOC       Legacy binary Word. python-docx cannot read it. Needs LibreOffice
            headless or antiword, neither of which is likely to be installable
            on a bank server. Raises a clear error rather than pretending.

  XML       lxml if present, stdlib otherwise. Covers InfoPath form data, which
            is likely on a farm of this vintage and is by far the easiest format
            to evaluate against — the fields are already named.

  XLSX      openpyxl, read-only mode. First row is treated as the header so rows
            come back keyed by column name rather than by index — controls say
            "the Completed Date column", not "column F".

  CSV       Standard library `csv` only. Deliberately not pandas: every library
            has to clear bank-side validation, and stdlib clears trivially.
            Handles BOM and sniffs the delimiter, since exports vary.

  JSON      Standard library. Fixture sources such as CI gate run logs.
"""

from __future__ import annotations

import io
import json
import logging
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Section:
    heading: str
    level: int
    text: str


@dataclass
class ParsedDocument:
    """Format-neutral view of a document, produced by the parsing layer.

    Not every format fills every attribute:
      text/pages  all formats
      fields      XML/InfoPath field names, DOCX label/value table pairs, XLSX A1 cells of the first sheet
      rows        Excel (first sheet) and CSV, one dict per row keyed by header
      sheets      Excel, every sheet as {"rows": [...], "cells": {"A1": ...}}
      sections    DOCX headings with their level and the body text beneath them
      data        JSON, the decoded document
    """

    text: str
    pages: list[str] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    sheets: dict[str, dict[str, Any]] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    sheet_names: list[str] = field(default_factory=list)
    data: Any = None
    parser: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ParseError(RuntimeError):
    pass


class UnsupportedFormat(ParseError):
    pass


class ScannedDocumentError(ParseError):
    """PDF with no text layer. Distinct so the caller can mark the evaluation
    INDETERMINATE rather than NON_COMPLIANT."""


class DocumentParser(ABC):
    extensions: tuple[str, ...] = ()
    name = "base"

    @abstractmethod
    def parse(self, content: bytes) -> ParsedDocument: ...


class PdfParser(DocumentParser):
    extensions = ("pdf",)
    name = "pdf"

    def parse(self, content: bytes) -> ParsedDocument:
        pages = self._extract(content)
        text = "\n".join(pages)
        if not text.strip():
            raise ScannedDocumentError(
                "PDF contains no extractable text — likely scanned. OCR would be "
                "required, which is a separate decision."
            )
        return ParsedDocument(text=text, pages=pages, parser=self.name)

    @staticmethod
    def _extract(content: bytes) -> list[str]:
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(content)) as pdf:
                return [(page.extract_text() or "") for page in pdf.pages]
        except ImportError:
            pass
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            return [(page.extract_text() or "") for page in reader.pages]
        except ImportError as exc:
            raise ParseError("No PDF library available (pdfplumber or pypdf)") from exc


class DocxParser(DocumentParser):
    extensions = ("docx",)
    name = "docx"

    def parse(self, content: bytes) -> ParsedDocument:
        try:
            import docx
        except ImportError as exc:
            raise ParseError("python-docx is not installed") from exc

        document = docx.Document(io.BytesIO(content))
        parts: list[str] = []
        blocks: list[tuple[int | None, str]] = []

        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            parts.append(text)
            blocks.append((self._heading_level(paragraph), text))

        sections: list[Section] = []
        for index, (level, heading) in enumerate(blocks):
            if level is None:
                continue
            body: list[str] = []
            for next_level, text in blocks[index + 1:]:
                if next_level is not None and next_level <= level:
                    break
                body.append(text)
            sections.append(Section(heading=heading, level=level, text="\n".join(body)))

        fields: dict[str, str] = {}
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                parts.extend(c for c in cells if c)
                # Two-column tables in sign-off forms are label/value pairs.
                if len(cells) == 2 and cells[0]:
                    fields[cells[0]] = cells[1]

        body = "\n".join(parts)
        return ParsedDocument(
            text=body, pages=[body], fields=fields, sections=sections, parser=self.name
        )

    @staticmethod
    def _heading_level(paragraph: Any) -> int | None:
        """Style name is the only reliable signal available without rendering.
        Documents that fake headings with bold body text won't be detected —
        worth checking against a real control document before relying on it."""
        style = (getattr(paragraph.style, "name", "") or "").lower()
        if style in ("title", "subtitle"):
            return 0
        if style.startswith("heading"):
            suffix = style.removeprefix("heading").strip()
            return int(suffix) if suffix.isdigit() else 1
        return None


class LegacyDocParser(DocumentParser):
    extensions = ("doc",)
    name = "doc"

    def parse(self, content: bytes) -> ParsedDocument:
        raise UnsupportedFormat(
            "Legacy .doc is not readable by python-docx. Conversion needs "
            "LibreOffice headless or antiword on the host — confirm whether "
            "either is permitted before relying on .doc support."
        )


class XmlParser(DocumentParser):
    extensions = ("xml", "xsn")
    name = "xml"

    def parse(self, content: bytes) -> ParsedDocument:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ParseError(f"Malformed XML: {exc}") from exc

        fields: dict[str, str] = {}
        texts: list[str] = []

        def walk(element: ET.Element, path: str = "") -> None:
            tag = element.tag.split("}")[-1]  # drop namespace
            here = f"{path}/{tag}" if path else tag
            value = (element.text or "").strip()
            if value:
                fields[tag] = value
                fields[here] = value
                texts.append(f"{tag}: {value}")
            for key, val in element.attrib.items():
                fields[f"{here}@{key.split('}')[-1]}"] = val
            for child in element:
                walk(child, here)

        walk(root)
        body = "\n".join(texts)
        return ParsedDocument(text=body, pages=[body], fields=fields, parser=self.name)


class CsvParser(DocumentParser):
    """Standard library only — nothing to get approved."""

    extensions = ("csv", "tsv", "txt")
    name = "csv"

    def parse(self, content: bytes) -> ParsedDocument:
        import csv

        text = content.decode("utf-8-sig", errors="replace")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = [
            {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in reader
        ]
        body = "\n".join(
            " | ".join(f"{k}: {v}" for k, v in row.items() if v) for row in rows
        )
        return ParsedDocument(text=body, pages=[body], rows=rows, parser=self.name)


class ExcelParser(DocumentParser):
    """openpyxl in read-only mode. Row 1 is the header.

    A control asks for "the row where Application ID is X, column Completed
    Date" — never for a raw cell index — so rows come back keyed by header.
    Literal cell addressing is still available through `fields` as A1-style keys.
    """

    extensions = ("xlsx", "xlsm")
    name = "xlsx"

    def __init__(self, sheet: str | None = None, max_cells: int = 200_000) -> None:
        self.sheet = sheet
        self.max_cells = max_cells

    def parse(self, content: bytes) -> ParsedDocument:
        try:
            import openpyxl
        except ImportError as exc:
            raise ParseError("openpyxl is not installed") from exc

        workbook = openpyxl.load_workbook(
            io.BytesIO(content), read_only=True, data_only=True
        )
        try:
            sheet_names = list(workbook.sheetnames)
            primary = self.sheet or sheet_names[0]
            sheets = {name: self._read_sheet(workbook[name]) for name in sheet_names}

            body = "\n".join(
                " | ".join(f"{k}: {v}" for k, v in row.items() if v)
                for sheet in sheets.values()
                for row in sheet["rows"]
            )
            return ParsedDocument(
                text=body,
                pages=[body],
                rows=sheets[primary]["rows"],
                fields=sheets[primary]["cells"],
                sheets=sheets,
                sheet_names=sheet_names,
                parser=self.name,
            )
        finally:
            workbook.close()

    def _read_sheet(self, worksheet: Any) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        cells: dict[str, Any] = {}
        header: list[str] = []
        cell_count = 0

        for row_index, row in enumerate(worksheet.iter_rows(values_only=False), start=1):
            values = [c.value for c in row]
            cell_count += len(values)
            if cell_count > self.max_cells:
                logger.warning("Sheet %s truncated at %s cells", worksheet.title, self.max_cells)
                break
            for cell in row:
                if cell.value is not None and getattr(cell, "coordinate", None):
                    cells[cell.coordinate] = _cell_text(cell.value)
            if row_index == 1:
                header = [str(v).strip() if v is not None else f"col{i}" for i, v in enumerate(values, 1)]
                continue
            if all(v is None for v in values):
                continue
            rows.append(
                {
                    header[i] if i < len(header) else f"col{i + 1}": _cell_text(v)
                    for i, v in enumerate(values)
                }
            )
        return {"rows": rows, "cells": cells}


def _cell_text(value: Any) -> str:
    from datetime import date, datetime as _dt

    if value is None:
        return ""
    if isinstance(value, (_dt, date)):
        return value.isoformat()
    return str(value).strip()


class JsonParser(DocumentParser):
    extensions = ("json",)
    name = "json"

    def parse(self, content: bytes) -> ParsedDocument:
        try:
            data = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ParseError(f"Malformed JSON: {exc}") from exc
        body = json.dumps(data, indent=2)
        return ParsedDocument(text=body, pages=[body], data=data, parser=self.name)


class ParserRegistry:
    def __init__(self, parsers: list[DocumentParser] | None = None) -> None:
        self._parsers = parsers or [
            PdfParser(),
            DocxParser(),
            LegacyDocParser(),
            XmlParser(),
            ExcelParser(),
            CsvParser(),
            JsonParser(),
        ]

    def for_extension(self, extension: str) -> DocumentParser:
        ext = extension.lower().lstrip(".")
        for parser in self._parsers:
            if ext in parser.extensions:
                return parser
        raise UnsupportedFormat(f"No parser registered for {ext!r}")

    def parse(self, content: bytes, extension: str) -> ParsedDocument:
        return self.for_extension(extension).parse(content)
