import json
import statistics
import time
from pathlib import Path

import pytest

from conftest import MOCK, ROOT, call, compose, wait_for

pytestmark = pytest.mark.e2e

# control, params, expected status, layer expected to fail (None when not applicable)
MATRIX = [
    ("CTL-FRESH-001", {"doc": "runbook.docx"}, "COMPLIANT", None),
    ("CTL-FRESH-001", {"doc": "stale-runbook.docx"}, "NON_COMPLIANT", "primary"),
    ("CTL-FRESH-001", {"doc": "missing.docx"}, "INDETERMINATE", "primary"),
    ("CTL-CLOUD-001", {"ait_id": "AIT-12345"}, "COMPLIANT", None),
    ("CTL-CLOUD-001", {"ait_id": "AIT-67890"}, "NON_COMPLIANT", "primary"),
    ("CTL-CLOUD-001", {"ait_id": "AIT-99999"}, "INDETERMINATE", "primary"),
    ("CTL-CDS-001", {"version": "2.4.0"}, "COMPLIANT", None),
    ("CTL-CDS-001", {"version": "2.5.0"}, "NON_COMPLIANT", "cataloged"),
    ("CTL-CDS-001", {"version": "2.6.0"}, "NON_COMPLIANT", "enforced"),
    ("CTL-CDS-001", {"version": "3.0.0"}, "INDETERMINATE", None),
    ("CTL-EXC-001", {"exception_id": "EXC-001"}, "COMPLIANT", None),
    ("CTL-EXC-001", {"exception_id": "EXC-002"}, "NON_COMPLIANT", "primary"),
    ("CTL-EXC-001", {"exception_id": "EXC-003"}, "INDETERMINATE", "primary"),
]
IDS = [f"{c}-{next(iter(p.values()))}" for c, p, _, _ in MATRIX]


@pytest.mark.parametrize("control,params,status,failing_layer", MATRIX, ids=IDS)
def test_evaluate(control, params, status, failing_layer):
    body = call("evaluate", {"control_id": control, "params": params})
    result = body["result"]
    assert result["status"] == status, result["reason"]
    assert result["color"] == ("green" if status == "COMPLIANT" else "red")
    if failing_layer:
        layers = {layer["name"]: layer["status"] for layer in result["layers"]}
        assert layers[failing_layer] == status
        others = [s for name, s in layers.items() if name != failing_layer]
        assert all(s == "COMPLIANT" for s in others)
    assert body["meta"]["policy_revision"].startswith("sha256:")
    assert body["meta"]["template_revision"].startswith("sha256:")


@pytest.mark.parametrize("control,params,status,failing_layer", MATRIX, ids=IDS)
def test_collect(control, params, status, failing_layer):
    body = call("collect", {"control_id": control, "params": params})
    assert "result" not in body
    assert body["meta"]["policy_revision"] is None
    for evidence in body["evidence"]:
        assert evidence["error"] is None
        assert set(evidence) == {"layer", "source", "found", "error", "subject", "values", "raw"}
        assert evidence["raw"] is None
    missing = params.get("doc") == "missing.docx" or params.get("version") == "3.0.0"
    assert all(e["found"] is not missing for e in body["evidence"])


def test_include_raw_returns_parsed_content():
    raw = call("collect?include_raw=true", {"control_id": "CTL-CLOUD-001"})["evidence"][0]["raw"]
    assert raw["content"]["sheets"]["AITs"]["rows"][0]["Application ID"] == "AIT-12345"
    assert raw["list_fields"]["Owner_x0020_Team"] == "Cloud Platform Eng"


def test_owners_come_from_every_source():
    body = call("collect", {"control_id": "CTL-CDS-001"})
    sources = {o["source"] for o in body["evidence"][0]["subject"]["owners"]}
    assert sources == {"template", "column:Owner Team", "sp:Author", "sp:ModifiedBy", "sp:AssociatedOwnerGroup"}


def test_debug_link_evaluates():
    url = "http://sharepoint-mock:8000/sites/compliance/Shared%20Documents/Runbooks/runbook.docx"
    assert call("evaluate", {"url": url, "policy": "freshness"})["result"]["status"] == "COMPLIANT"


def test_latency_report():
    samples = {}
    for control in ("CTL-FRESH-001", "CTL-CLOUD-001", "CTL-CDS-001", "CTL-EXC-001"):
        timings = []
        for _ in range(20):
            started = time.perf_counter()
            call("evaluate", {"control_id": control})
            timings.append((time.perf_counter() - started) * 1000)
        timings.sort()
        samples[control] = {"runs": 20, "p50_ms": round(statistics.median(timings), 1),
                            "p95_ms": round(timings[int(0.95 * len(timings)) - 1], 1),
                            "max_ms": round(timings[-1], 1)}
    report = ROOT / "reports" / "latency.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps(samples, indent=2) + "\n")
    assert all(s["p95_ms"] < 2000 for s in samples.values()), samples


def test_rule_edit_hot_reloads():
    params_file = ROOT / "opa" / "data" / "params.json"
    original = params_file.read_text()
    edited = json.loads(original)
    edited["evidence"]["params"]["freshness"]["within_days"] = 1

    def status():
        return call("evaluate", {"control_id": "CTL-FRESH-001"})["result"]["status"]

    def wait_status(expected, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if status() == expected:
                return True
            time.sleep(0.5)
        return False

    assert status() == "COMPLIANT"
    try:
        params_file.write_text(json.dumps(edited, indent=2) + "\n")
        assert wait_status("NON_COMPLIANT"), "OPA did not pick up the params change"
    finally:
        params_file.write_text(original)
    assert wait_status("COMPLIANT"), "OPA did not pick up the restored params"


def test_unreachable_source_is_error():
    compose("stop", "sharepoint-mock")
    try:
        body = call("evaluate", {"control_id": "CTL-FRESH-001", "params": {"doc": "runbook.docx"}})
        assert body["result"]["status"] == "ERROR"
        assert body["result"]["color"] == "red"
        assert body["evidence"][0]["error"]
    finally:
        compose("start", "sharepoint-mock")
        wait_for(f"{MOCK}/health")
