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
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return None


def documented():
    return {m["name"]: json.loads(m.group(3)) for m in capture_examples.BLOCK.finditer(DOC.read_text())}


def test_every_json_block_parses():
    blocks = JSON_BLOCK.findall(DOC.read_text())
    assert len(blocks) >= 4
    for block in blocks:
        json.loads(block)


def test_every_example_is_captured():
    assert set(documented()) == set(capture_examples.EXAMPLES)
    assert all(body for body in documented().values())


@pytest.mark.parametrize("name", sorted(capture_examples.EXAMPLES))
def test_example_matches_live_response(name):
    route, body = capture_examples.EXAMPLES[name]
    live = requests.post(f"{API}/{route}", json=body, timeout=30).json()
    assert shape(documented()[name]) == shape(live)
