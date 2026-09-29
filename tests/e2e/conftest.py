import os
import subprocess
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).parent.parent.parent
API = os.environ.get("API_URL", "http://localhost:8080")
MOCK = os.environ.get("MOCK_URL", "http://localhost:8000")


def wait_for(url, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(url, timeout=2).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise TimeoutError(f"{url} not healthy after {timeout}s")


def compose(*args):
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)


@pytest.fixture(scope="session", autouse=True)
def stack():
    try:
        health = requests.get(f"{API}/health", timeout=3).json()
    except requests.RequestException as exc:
        pytest.fail(f"Stack is not running at {API}; run `make up` first ({exc})")
    assert health["opa"] == "ok", health


def call(route, body):
    response = requests.post(f"{API}/{route}", json=body, timeout=30)
    assert response.status_code == 200, response.text
    return response.json()
