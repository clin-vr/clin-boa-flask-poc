from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1"
COLLECTOR = "evidence-api@0.1.0"
REQUEST_KEYS = ("control_id", "url", "policy", "selectors", "params")


def opa_input(template, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "control": {
            "control_id": template.control_id,
            "policy": template.policy,
            "layers": [{"name": layer.name, "kind": layer.kind} for layer in template.layers],
        },
        "params": template.params,
        "evidence": [{**item, "raw": None} for item in evidence],
    }


def error_result(reason: str) -> dict[str, Any]:
    return {"status": "ERROR", "color": "red", "reason": reason, "layers": [], "findings": []}


def build(body: dict[str, Any], template, evidence: list[dict[str, Any]], *, started: float, finished: float,
          executed_as: str, result: dict[str, Any] | None = None, policy_revision: str | None = None,
          evaluate: bool = False) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "request": {key: body[key] for key in REQUEST_KEYS if key in body},
    }
    if evaluate:
        envelope["result"] = result
    envelope["evidence"] = evidence
    envelope["meta"] = {
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "collector": COLLECTOR,
        "template_revision": template.revision,
        "policy_revision": policy_revision,
        "executed_as": executed_as,
        "duration_ms": round((finished - started) * 1000),
    }
    return envelope
