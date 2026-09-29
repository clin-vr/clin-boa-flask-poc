import os
from pathlib import Path

import pytest

from evidence_api.collectors.fixture import FixtureCollector
from evidence_api.collectors.sharepoint import SharePointCollector
from evidence_api.templates import load_template, resolve

pytestmark = pytest.mark.component

ROOT = Path(__file__).parent.parent.parent
MOCK = os.environ.get("MOCK_URL", "http://localhost:8000")


def run(control, params=None):
    template = resolve(load_template(ROOT / "templates", control), params)
    collectors = {"sharepoint": SharePointCollector(MOCK), "fixture": FixtureCollector(ROOT / "fixtures")}
    return {layer.name: collectors[s.collector].collect(layer.name, s.ref, s.selectors, owner=template.owner)
            for layer in template.layers for s in layer.sources}


def values(evidence):
    return {k: v.value for k, v in evidence.values.items()}


@pytest.mark.parametrize("doc,found", [("runbook.docx", True), ("stale-runbook.docx", True), ("missing.docx", False)])
def test_freshness(doc, found):
    evidence = run("CTL-FRESH-001", {"doc": doc})["primary"]
    assert evidence.found is found and evidence.error is None
    if found:
        assert evidence.values["owner_team"].value == "Platform Operations"
        assert ("C. Owner", "sp:AssociatedOwnerGroup") in [(o.value, o.source) for o in evidence.subject.owners]


@pytest.mark.parametrize("ait,approved", [("AIT-12345", "Yes"), ("AIT-67890", "No"), ("AIT-99999", None)])
def test_cloud(ait, approved):
    evidence = run("CTL-CLOUD-001", {"ait_id": ait})["primary"]
    assert evidence.subject.id == "cloud-approvals-2026-09.xlsx"
    assert evidence.values["approved"].value == approved


@pytest.mark.parametrize("version,approver,gate_doc", [
    ("2.4.0", "A. Approver", "2.4.0"), ("2.5.0", "Z. Outsider", "2.5.0"), ("2.6.0", "B. Approver", "2.4.0")])
def test_cds(version, approver, gate_doc):
    layers = run("CTL-CDS-001", {"version": version})
    cataloged, enforced = layers["cataloged"], layers["enforced"]
    assert values(cataloged)["approver"] == approver
    assert values(cataloged)["status"] == "Approved for production deployment"
    group = [o.value for o in cataloged.subject.owners if o.source == "sp:AssociatedOwnerGroup"]
    assert group == ["A. Approver", "B. Approver"]
    assert values(enforced)["gate_result"] == "allow"
    assert values(enforced)["approval_uri"].endswith(f"AIT-12345-{gate_doc}-approval.docx")


def test_cds_missing_version_not_found_in_both_layers():
    layers = run("CTL-CDS-001", {"version": "3.0.0"})
    assert all(e.found is False and e.error is None for e in layers.values())


@pytest.mark.parametrize("exc,controls", [("EXC-001", True), ("EXC-002", True), ("EXC-003", False)])
def test_exceptions(exc, controls):
    evidence = run("CTL-EXC-001", {"exception_id": exc})["primary"]
    assert evidence.values["expiry"].found
    assert evidence.values["controls"].found is controls
