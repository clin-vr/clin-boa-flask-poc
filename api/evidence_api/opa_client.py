from __future__ import annotations

import hashlib
from typing import Any

import requests


class OpaUnavailable(RuntimeError):
    pass


class OpaClient:
    def __init__(self, url: str, timeout: float = 5) -> None:
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
        try:
            return requests.get(f"{self.url}/health", timeout=2).ok
        except requests.RequestException:
            return False
