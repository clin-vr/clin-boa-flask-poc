import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evidence_api.evidence import Evidence, Owner, Subject, Value
from evidence_api.parsing import ParseError, ParserRegistry, UnsupportedFormat
from generate_samples import generate

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
GOLDEN = Path(__file__).parent.parent / "golden" / "evidence.json"


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    root = tmp_path_factory.mktemp("parse")
    generate(root / "files", root / "fixtures", "http://sharepoint-mock:8000", now=NOW)
    return root


def parse(path: Path):
    return ParserRegistry().parse(path.read_bytes(), path.suffix)


def test_xlsx_reads_every_sheet(samples):
    doc = parse(samples / "files/sites/compliance/Shared Documents/APS/cloud-approvals-2026-09.xlsx")
    assert doc.sheet_names == ["Info", "AITs"]
    assert doc.sheets["AITs"]["rows"][0] == {
        "Application ID": "AIT-12345", "Application Name": "Payments Gateway", "Approved for Cloud": "Yes"}
    assert doc.sheets["Info"]["cells"]["B2"] == "2026-09-24"
    assert doc.sheets["AITs"]["cells"]["C3"] == "No"
    assert doc.rows == doc.sheets["Info"]["rows"]


def test_docx_sections_have_levels_and_nesting(samples):
    doc = parse(samples / "files/sites/cds/Approvals/AIT-12345-2.4.0-approval.docx")
    by_heading = {s.heading: s for s in doc.sections}
    assert by_heading["Approval Status"].level == 2
    assert by_heading["Approval Status"].text == "Approved for production deployment"
    assert by_heading["Sign-off"].text == "Approved by: A. Approver\nSign-off date: 2026-09-22"
    top = by_heading["Code Approval: AIT-12345 2.4.0"]
    assert top.level == 1
    assert "Approval Status" in top.text and "Approved by: A. Approver" in top.text
    assert "Sign-off date: 2026-09-22" in doc.text


def test_docx_missing_section_is_absent(samples):
    doc = parse(samples / "files/sites/compliance/Shared Documents/Exceptions/EXC-003.docx")
    assert "Compensating Controls" not in [s.heading for s in doc.sections]


def test_json_fixture(samples):
    doc = parse(samples / "fixtures/ci/deploy-AIT-12345-2.4.0.json")
    assert doc.data["gate"]["result"] == "allow"
    assert doc.parser == "json"


def test_bad_json_and_unknown_extension():
    with pytest.raises(ParseError):
        ParserRegistry().parse(b"{nope", "json")
    with pytest.raises(UnsupportedFormat):
        ParserRegistry().parse(b"", "pptx")


def test_parsed_document_serialises(samples):
    doc = parse(samples / "files/sites/compliance/Shared Documents/Runbooks/runbook.docx")
    body = json.loads(json.dumps(doc.to_dict()))
    assert body["sections"][0] == {"heading": "Payments Gateway Runbook", "level": 1,
                                   "text": "Restart procedure for the payments gateway service.\nEscalation\n"
                                           "Page the on-call engineer for Platform Operations."}


def test_evidence_matches_golden_contract():
    evidence = Evidence(
        layer="primary", source="sharepoint", found=True,
        subject=Subject(
            id="cloud-approvals-2026-09.xlsx",
            uri="http://sharepoint-mock:8000/sites/compliance/Shared%20Documents/APS/cloud-approvals-2026-09.xlsx",
            title="cloud-approvals-2026-09.xlsx",
            created_at="2026-08-30T12:00:00Z", modified_at="2026-09-24T12:00:00Z", modified_by="K. Reviewer",
            owners=[Owner("Cloud Platform Eng", "template"), Owner("Cloud Platform Eng", "column:Owner Team"),
                    Owner("J. Analyst", "sp:Author"), Owner("K. Reviewer", "sp:ModifiedBy"),
                    Owner("C. Owner", "sp:AssociatedOwnerGroup")],
        ),
        values={
            "approved": Value("Yes", True, "sheet 'AITs', row 2, column 'Approved for Cloud'"),
            "as_of": Value("2026-09-24", True, "sheet 'Info', cell B2"),
        },
    )
    assert evidence.to_dict() == json.loads(GOLDEN.read_text())


def test_empty_evidence_keeps_nulls():
    body = Evidence(layer="enforced", source="fixture").to_dict()
    assert body["found"] is False and body["error"] is None and body["raw"] is None
    assert all(body["subject"][k] is None for k in ("id", "uri", "title", "created_at", "modified_at", "modified_by", "text"))
    assert body["subject"]["owners"] == [] and body["values"] == {}
