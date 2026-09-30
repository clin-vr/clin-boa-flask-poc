"""Reads JSON files standing in for sources that aren't integrated yet, such as CI run logs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..evidence import Evidence, Owner, Subject, Value
from ..parsing import JsonParser, ParseError
from ..selectors import apply
from .base import Collector


class FixtureCollector(Collector):
    """Collector that reads JSON fixture files from a directory."""

    name = "fixture"

    def __init__(self, fixtures_dir: Path | str) -> None:
        """Store the fixtures directory as an absolute path."""
        self.root = Path(fixtures_dir).resolve()

    def collect(self, layer: str, ref: dict[str, Any], selectors: dict[str, Any], *,
                owner: str | None = None, include_raw: bool = False) -> Evidence:
        """Read the JSON file at ref["path"] under the fixtures directory and apply the selectors.

        A missing file gives found=False, a path outside the directory or an OSError gives error set,
        and invalid JSON gives found=True with every value marked unreadable."""
        path = (self.root / ref["path"]).resolve()
        if not path.is_relative_to(self.root):
            return self.unreachable(layer, ValueError(f"Fixture path {ref['path']!r} escapes the fixtures directory"))
        if not path.is_file():
            return Evidence(layer=layer, source=self.name, found=False,
                            values={name: Value(None, False, "fixture not found") for name in selectors})
        try:
            content = path.read_bytes()
        except OSError as exc:
            return self.unreachable(layer, exc)

        subject = Subject(id=path.name, uri=f"fixture:{ref['path']}", title=path.name,
                          owners=[Owner(owner, "template")] if owner else [])
        try:
            document = JsonParser().parse(content)
        except ParseError as exc:
            return Evidence(layer=layer, source=self.name, found=True, subject=subject,
                            values={name: Value(None, False, f"unreadable: {exc}") for name in selectors})

        return Evidence(layer=layer, source=self.name, found=True, subject=subject,
                        values=apply(selectors, document, {}, {}),
                        raw={"content": document.data} if include_raw else None)


def config_defaults() -> dict[str, Any]:
    """Return FIXTURES_DIR from the environment, defaulting to "fixtures"."""
    return {"FIXTURES_DIR": os.environ.get("FIXTURES_DIR", "fixtures")}


def from_config(config: dict[str, Any]) -> FixtureCollector:
    """Build a FixtureCollector for the configured FIXTURES_DIR."""
    return FixtureCollector(config["FIXTURES_DIR"])
