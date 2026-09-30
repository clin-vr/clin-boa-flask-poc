"""Selectors: named requests for one value from a document or its metadata."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .evidence import Value
from .parsing import ParsedDocument

METADATA_SELECTORS = {"metadata", "column"}
CONTENT_SELECTORS = {"xlsx_cell", "xlsx_row", "docx_section", "text_regex", "json_path"}
SELECTOR_TYPES = METADATA_SELECTORS | CONTENT_SELECTORS


class SelectorError(ValueError):
    """Error for a selector spec that is malformed or of an unknown type."""
    pass


def selector_type(spec: Any) -> str:
    """Return the spec's selector type; raise SelectorError unless it is an object with one known type key."""
    if not isinstance(spec, dict) or len(spec) != 1:
        raise SelectorError(f"A selector must be an object with exactly one type key, got {spec!r}")
    kind = next(iter(spec))
    if kind not in SELECTOR_TYPES:
        raise SelectorError(f"Unknown selector type {kind!r}; expected one of {sorted(SELECTOR_TYPES)}")
    return kind


def validate_selectors(selectors: dict[str, Any]) -> None:
    """Raise SelectorError unless selectors is an object whose every spec has a known type."""
    if not isinstance(selectors, dict):
        raise SelectorError("selectors must be an object")
    for spec in selectors.values():
        selector_type(spec)


def needs_content(selectors: dict[str, Any]) -> bool:
    """Return whether any selector reads the document content rather than metadata or columns."""
    return any(selector_type(spec) in CONTENT_SELECTORS for spec in selectors.values())


def apply(selectors: dict[str, Any], document: ParsedDocument | None, metadata: dict[str, Any],
          fields: dict[str, Any], *, metadata_location: str = "metadata",
          column_key: Callable[[str], str] | None = None) -> dict[str, Value]:
    """Return a Value for each named selector, read from the document, its metadata, or its column fields."""
    return {name: _apply_one(spec, document, metadata, fields, metadata_location, column_key)
            for name, spec in selectors.items()}


def _apply_one(spec: dict[str, Any], document: ParsedDocument | None, metadata: dict[str, Any],
               fields: dict[str, Any], metadata_location: str, column_key: Callable[[str], str] | None) -> Value:
    """Return the Value for one selector; content selectors give found=False when no document was read."""
    kind = selector_type(spec)
    arg = spec[kind]
    if kind == "metadata":
        value = metadata.get(arg)
        return Value(value, value is not None, metadata_location)
    if kind == "column":
        key = column_key(arg) if column_key else arg
        value = fields.get(key)
        return Value(value, value is not None, f"column '{arg}' ({key})" if key != arg else f"column '{arg}'")
    if document is None:
        return Value(None, False, "document not read")
    return {
        "xlsx_cell": _xlsx_cell,
        "xlsx_row": _xlsx_row,
        "docx_section": _docx_section,
        "text_regex": _text_regex,
        "json_path": _json_path,
    }[kind](arg, document)


def _xlsx_cell(arg: dict[str, Any], document: ParsedDocument) -> Value:
    """Return the value of one cell on a named sheet."""
    sheet, cell = arg["sheet"], arg["cell"].upper()
    location = f"sheet '{sheet}', cell {cell}"
    value = document.sheets.get(sheet, {}).get("cells", {}).get(cell)
    return Value(value, value is not None, location)


def _xlsx_row(arg: dict[str, Any], document: ParsedDocument) -> Value:
    """Return a column's value from the first row on a sheet whose fields equal all the match values."""
    sheet, match, column = arg["sheet"], arg["match"], arg["column"]
    for index, row in enumerate(document.sheets.get(sheet, {}).get("rows", []), start=2):
        if all(str(row.get(k, "")) == str(v) for k, v in match.items()):
            value = row.get(column)
            return Value(value, value is not None, f"sheet '{sheet}', row {index}, column '{column}'")
    return Value(None, False, f"sheet '{sheet}', no row matching {match}")


def _docx_section(arg: dict[str, Any], document: ParsedDocument) -> Value:
    """Return the text of the first section whose heading starts with the given heading, ignoring case."""
    wanted = arg["heading"].strip().lower()
    for section in document.sections:
        if section.heading.strip().lower().startswith(wanted):
            return Value(section.text, True, f"section '{section.heading}'")
    return Value(None, False, f"no section headed '{arg['heading']}'")


def _text_regex(arg: dict[str, Any], document: ParsedDocument) -> Value:
    """Return the first regex match in the document text (group 1 if the pattern has groups), stripped."""
    match = re.search(arg["pattern"], document.text, re.MULTILINE)
    if not match:
        return Value(None, False, f"no match for /{arg['pattern']}/")
    line = document.text.count("\n", 0, match.start()) + 1
    value = match.group(1) if match.groups() else match.group(0)
    return Value(value.strip(), True, f"document text, line {line}")


def _json_path(arg: str, document: ParsedDocument) -> Value:
    """Return the value at a dotted path into the document's JSON data; digit parts index into lists."""
    current: Any = document.data
    for part in arg.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return Value(None, False, f"json path {arg}")
    return Value(current, True, f"json path {arg}")
