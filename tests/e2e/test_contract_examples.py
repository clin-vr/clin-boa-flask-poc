"""E2E tests that the docs/contract.md JSON examples parse and match live responses; needs the full stack."""

import importlib.util
import json
import re

import pytest
import requests

from conftest import API, ROOT

pytestmark = pytest.mark.e2e

DOC = ROOT / "docs" / "contract.md"
JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.S)

spec = importlib.util.spec_from_file_location("capture_examples", ROOT / "scripts" / "capture_examples.py")
capture_examples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture_examples)


def shape(value):
    """Return the structure of a JSON value with every leaf replaced by None and lists cut to one item."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return None


def documented():
    """Return the example bodies in docs/contract.md, keyed by example name."""
    return {m["name"]: json.loads(m.group(3)) for m in capture_examples.BLOCK.finditer(DOC.read_text())}


def test_every_json_block_parses():
    """Every json code block in the contract doc is valid JSON."""
    blocks = JSON_BLOCK.findall(DOC.read_text())
    assert len(blocks) >= 4
    for block in blocks:
        json.loads(block)


def test_every_example_is_captured():
    """The doc holds a non-empty example for exactly the names in capture_examples."""
    assert set(documented()) == set(capture_examples.EXAMPLES)
    assert all(body for body in documented().values())


@pytest.mark.parametrize("name", sorted(capture_examples.EXAMPLES))
def test_example_matches_live_response(name):
    """A documented example has the same shape as the live API response."""
    route, body = capture_examples.EXAMPLES[name]
    live = requests.post(f"{API}/{route}", json=body, timeout=30).json()
    assert shape(documented()[name]) == shape(live)
