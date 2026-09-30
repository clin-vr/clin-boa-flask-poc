"""Refresh the example blocks in docs/contract.md from a running stack."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

API = os.environ.get("API_URL", "http://localhost:8080")
DOC = Path(__file__).parent.parent / "docs" / "contract.md"
BLOCK = re.compile(r"(<!-- example:(?P<name>[a-z0-9-]+) -->\n```json\n)(.*?)(\n```\n<!-- /example -->)", re.S)

EXAMPLES = {
    "evaluate-cds": ("evaluate", {"control_id": "CTL-CDS-001", "params": {"version": "2.6.0"}}),
    "collect-cloud": ("collect", {"control_id": "CTL-CLOUD-001"}),
    "unknown-control": ("evaluate", {"control_id": "CTL-NOPE-001"}),
}


def capture(route: str, body: dict) -> dict:
    """Return the JSON response from POSTing the body to the given API route."""
    return requests.post(f"{API}/{route}", json=body, timeout=30).json()


def main() -> None:
    """Replace every example block in docs/contract.md with a freshly captured response."""
    text = DOC.read_text(encoding="utf-8")

    def replace(match: re.Match) -> str:
        """Return the matched example block with its JSON replaced by a live response."""
        route, body = EXAMPLES[match["name"]]
        return match.group(1) + json.dumps(capture(route, body), indent=2) + match.group(4)

    DOC.write_text(BLOCK.sub(replace, text), encoding="utf-8")
    print(f"Refreshed {len(BLOCK.findall(text))} examples in {DOC}")


if __name__ == "__main__":
    main()
