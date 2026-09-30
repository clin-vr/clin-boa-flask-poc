"""Unit tests for control templates and selectors over generated samples; needs no services."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evidence_api import selectors
from evidence_api.collectors.sharepoint.columns import internal_column_name
from evidence_api.parsing import ParserRegistry
from evidence_api.selectors import needs_content
from evidence_api.templates import TemplateError, TemplateNotFound, load_template, parse_template, resolve
from generate_samples import generate

TEMPLATES = Path(__file__).parent.parent.parent / "templates"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
CONTROLS = ["CTL-FRESH-001", "CTL-CLOUD-001", "CTL-CDS-001", "CTL-EXC-001"]


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    """Generate the sample files and fixtures into a temp directory and return its root."""
    root = tmp_path_factory.mktemp("sel")
    generate(root / "files", root / "fixtures", "http://sharepoint-mock:8000", now=NOW)
    return root


def parsed(root, rel):
    """Parse a generated file, given relative to the samples root."""
    path = root / rel
    return ParserRegistry().parse(path.read_bytes(), path.suffix)


def selectors_of(control, params=None, layer=0):
    """Return the resolved selectors of a control's first source in the given layer."""
    return resolve(load_template(TEMPLATES, control), params).layers[layer].sources[0].selectors


@pytest.mark.parametrize("control", CONTROLS)
def test_templates_load_with_revision(control):
    """Each control template loads with its id and a sha256 revision."""
    template = load_template(TEMPLATES, control)
    assert template.control_id == control
    assert template.revision.startswith("sha256:") and len(template.revision) == 71


def test_default_params_substitute():
    """The template's default params fill its ref placeholders."""
    ref = resolve(load_template(TEMPLATES, "CTL-CDS-001")).layers[0].sources[0].ref
    assert ref["name_pattern"] == "AIT-12345-2.4.0-approval\\.docx"


def test_request_params_override_defaults():
    """Request params override the template's defaults."""
    template = resolve(load_template(TEMPLATES, "CTL-CDS-001"), {"version": "2.6.0"})
    assert template.params == {"app_id": "AIT-12345", "version": "2.6.0"}
    assert template.layers[1].sources[0].ref["path"] == "ci/deploy-AIT-12345-2.6.0.json"
    assert selectors_of("CTL-CLOUD-001", {"ait_id": "AIT-67890"})["approved"]["xlsx_row"]["match"] == \
        {"Application ID": "AIT-67890"}


def test_missing_param_raises():
    """A placeholder with no param value raises TemplateError naming it."""
    body = json.loads((TEMPLATES / "CTL-FRESH-001.json").read_text())
    body["params"] = {}
    with pytest.raises(TemplateError, match="doc"):
        resolve(parse_template(body))


def test_invalid_layer_kind_rejected():
    """An unknown layer kind raises TemplateError."""
    body = json.loads((TEMPLATES / "CTL-FRESH-001.json").read_text())
    body["layers"][0]["kind"] = "corrective"
    with pytest.raises(TemplateError, match="corrective"):
        parse_template(body)


def test_unknown_selector_rejected():
    """An unknown selector type raises TemplateError."""
    body = json.loads((TEMPLATES / "CTL-FRESH-001.json").read_text())
    body["layers"][0]["sources"][0]["selectors"]["x"] = {"pdf_page": 1}
    with pytest.raises(TemplateError, match="pdf_page"):
        parse_template(body)


def test_missing_and_invalid_control_ids():
    """An unknown control raises TemplateNotFound and a path-like id raises TemplateError."""
    with pytest.raises(TemplateNotFound):
        load_template(TEMPLATES, "CTL-NOPE-999")
    with pytest.raises(TemplateError):
        load_template(TEMPLATES, "../README")


def test_needs_content():
    """CTL-FRESH-001's selectors need no content, while CTL-CLOUD-001's do."""
    assert needs_content(selectors_of("CTL-FRESH-001")) is False
    assert needs_content(selectors_of("CTL-CLOUD-001")) is True


def test_column_internal_name():
    """A column selector looks up the internal column name and reports both names."""
    assert internal_column_name("Owner Team") == "Owner_x0020_Team"
    values = selectors.apply({"t": {"column": "Owner Team"}}, None, {}, {"Owner_x0020_Team": "CDS"},
                             column_key=internal_column_name)
    assert (values["t"].value, values["t"].found) == ("CDS", True)
    assert values["t"].location == "column 'Owner Team' (Owner_x0020_Team)"


def test_metadata_selector():
    """A metadata selector reads a known field and misses an unknown one."""
    values = selectors.apply({"m": {"metadata": "modified_at"}, "n": {"metadata": "nope"}},
                             None, {"modified_at": "2026-09-26T12:00:00Z"}, {})
    assert values["m"].found and values["m"].value == "2026-09-26T12:00:00Z"
    assert values["n"].found is False


@pytest.mark.parametrize("ait,expected", [("AIT-12345", "Yes"), ("AIT-67890", "No"), ("AIT-99999", None)])
def test_xlsx_row(samples, ait, expected):
    """The xlsx_row selector finds each AIT's approval row, or not found when absent."""
    doc = parsed(samples, "files/sites/compliance/Shared Documents/APS/cloud-approvals-2026-09.xlsx")
    value = selectors.apply(selectors_of("CTL-CLOUD-001", {"ait_id": ait}), doc, {}, {})["approved"]
    assert value.value == expected and value.found is (expected is not None)
    if expected:
        assert value.location.startswith("sheet 'AITs', row ")


def test_xlsx_cell(samples):
    """The xlsx_cell selector reads a named cell and misses a cell with no value."""
    doc = parsed(samples, "files/sites/compliance/Shared Documents/APS/cloud-approvals-2026-09.xlsx")
    value = selectors.apply(selectors_of("CTL-CLOUD-001"), doc, {}, {})["as_of"]
    assert (value.value, value.location) == ("2026-09-24", "sheet 'Info', cell B2")
    miss = selectors.apply({"x": {"xlsx_cell": {"sheet": "Info", "cell": "Z99"}}}, doc, {}, {})["x"]
    assert miss.found is False


def test_cds_selectors(samples):
    """The CDS selectors read status, approver and sign-off date with their locations."""
    doc = parsed(samples, "files/sites/cds/Approvals/AIT-12345-2.4.0-approval.docx")
    values = selectors.apply(selectors_of("CTL-CDS-001"), doc, {}, {})
    assert values["status"].value == "Approved for production deployment"
    assert values["status"].location == "section 'Approval Status'"
    assert values["approver"].value == "A. Approver"
    assert values["signed_on"].value == "2026-09-22"
    assert values["signed_on"].location.startswith("document text, line ")


def test_docx_heading_prefix_is_case_insensitive(samples):
    """A docx_section heading matches by case-insensitive prefix."""
    doc = parsed(samples, "files/sites/cds/Approvals/AIT-12345-2.4.0-approval.docx")
    value = selectors.apply({"s": {"docx_section": {"heading": "approval"}}}, doc, {}, {})["s"]
    assert value.found and value.location == "section 'Approval Status'"


@pytest.mark.parametrize("exc,expiry,controls_found", [
    ("EXC-001", "2026-11-28", True), ("EXC-002", "2026-09-19", True), ("EXC-003", "2026-11-28", False)])
def test_exception_selectors(samples, exc, expiry, controls_found):
    """The exception selectors read the expiry and find controls only where present."""
    doc = parsed(samples, f"files/sites/compliance/Shared Documents/Exceptions/{exc}.docx")
    values = selectors.apply(selectors_of("CTL-EXC-001", {"exception_id": exc}), doc, {}, {})
    assert values["expiry"].value == expiry
    assert values["controls"].found is controls_found


@pytest.mark.parametrize("version,approval", [("2.4.0", "2.4.0"), ("2.6.0", "2.4.0")])
def test_json_path(samples, version, approval):
    """The json_path selector reads gate fields and misses a path that does not exist."""
    doc = parsed(samples, f"fixtures/ci/deploy-AIT-12345-{version}.json")
    values = selectors.apply(selectors_of("CTL-CDS-001", {"version": version}, layer=1), doc, {}, {})
    assert values["gate_result"].value == "allow"
    assert values["approval_uri"].value.endswith(f"AIT-12345-{approval}-approval.docx")
    assert values["gate_policy"].location == "json path gate.policy_ref"
    miss = selectors.apply({"x": {"json_path": "gate.nope.deeper"}}, doc, {}, {})["x"]
    assert miss.found is False


def test_content_selector_without_document():
    """A content selector with no document gives not found, document not read."""
    value = selectors.apply({"x": {"text_regex": {"pattern": "a"}}}, None, {}, {})["x"]
    assert value.found is False and value.location == "document not read"
