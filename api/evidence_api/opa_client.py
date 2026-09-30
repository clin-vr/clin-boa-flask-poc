"""HTTP client for the OPA server that holds the evidence policies."""

from __future__ import annotations

import hashlib
from typing import Any

import requests


class OpaUnavailable(RuntimeError):
    """Error raised when OPA cannot be reached or returns an error status."""
    pass


class OpaClient:
    """Client for OPA's data and policy APIs at one base URL."""
    def __init__(self, url: str, timeout: float = 5) -> None:
        """Store the OPA base URL, without a trailing slash, and the request timeout."""
        self.url = url.rstrip("/")
        self.timeout = timeout

    def evaluate(self, policy: str, input_doc: dict[str, Any]) -> dict[str, Any] | None:
        """Return the policy's decision, or None when OPA has no such policy."""
        try:
            response = requests.post(f"{self.url}/v1/data/evidence/policies/{policy}/decision",
                                     json={"input": input_doc}, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OpaUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return response.json().get("result")

    def policy_revision(self) -> str:
        """Return a sha256 over all loaded policy modules; raise OpaUnavailable when OPA cannot be reached."""
        try:
            response = requests.get(f"{self.url}/v1/policies", timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OpaUnavailable(f"{type(exc).__name__}: {exc}") from exc
        digest = hashlib.sha256()
        for module in sorted(response.json().get("result", []), key=lambda m: m["id"]):
            digest.update(module["id"].encode() + b"\n" + module["raw"].encode() + b"\n")
        return "sha256:" + digest.hexdigest()

    def healthy(self) -> bool:
        """Return whether OPA's /health endpoint answers OK within 2 seconds."""
        try:
            return requests.get(f"{self.url}/health", timeout=2).ok
        except requests.RequestException:
            return False
