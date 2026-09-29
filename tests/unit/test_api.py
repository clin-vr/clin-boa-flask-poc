import json
from pathlib import Path

import pytest

from evidence_api.app import create_app
from evidence_api.collectors.base import Collector
from evidence_api.evidence import Evidence, Subject, Value
from evidence_api.opa_client import OpaUnavailable

ROOT = Path(__file__).parent.parent.parent
GOLDEN = json.loads((ROOT / "tests/golden/evidence.json").read_text())
DECISION = {"status": "COMPLIANT", "color": "green", "reason": "ok", "layers": [], "findings": []}


class FakeCollector(Collector):
    name = "sharepoint"

    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def collect(self, layer, ref, selectors, *, owner=None, include_raw=False):
        self.calls.append({"layer": layer, "ref": ref, "selectors": selectors, "include_raw": include_raw})
        if self.fail == "error":
            return self.unreachable(layer, RuntimeError("SharePoint returned 503"))
        if self.fail == "raise":
            raise KeyError("boom")
        return Evidence(layer=layer, source=self.name, found=True, subject=Subject(id="x"),
                        values={k: Value("v", True, "here") for k in selectors},
                        raw={"content": "full"} if include_raw else None)


class FakeOpa:
    def __init__(self, decision=DECISION, down=False):
        self.decision, self.down, self.inputs = decision, down, []

    def evaluate(self, policy, input_doc):
        if self.down:
            raise OpaUnavailable("ConnectionError: refused")
        self.inputs.append((policy, input_doc))
        return self.decision

    def policy_revision(self):
        return "sha256:policy"

    def healthy(self):
        return not self.down


def client(collector=None, opa=None):
    collector = collector or FakeCollector()
    app = create_app(
        {"TEMPLATES_DIR": str(ROOT / "templates"), "SHAREPOINT_ALLOWED_HOSTS": "sharepoint-mock",
         "SHAREPOINT_BASE_URL": "http://sharepoint-mock:8000"},
        collectors={"sharepoint": collector, "fixture": collector},
        opa=opa or FakeOpa(),
    )
    return app.test_client(), collector


def post(c, route, body, query=""):
    return c.post(f"/{route}{query}", json=body)


@pytest.mark.parametrize("body", [None, [], "text"])
def test_non_object_body_is_400(body):
    c, _ = client()
    response = c.post("/evaluate", data=json.dumps(body), content_type="application/json")
    assert response.status_code == 400


def test_body_without_control_or_url_is_400():
    c, _ = client()
    assert post(c, "evaluate", {"params": {}}).status_code == 400


def test_unknown_control_is_404():
    c, _ = client()
    response = post(c, "evaluate", {"control_id": "CTL-NOPE-001"})
    assert response.status_code == 404 and "CTL-NOPE-001" in response.get_json()["error"]


def test_unknown_policy_is_404():
    c, _ = client(opa=FakeOpa(decision=None))
    assert post(c, "evaluate", {"control_id": "CTL-FRESH-001"}).status_code == 404


def test_non_object_params_is_400():
    c, _ = client()
    assert post(c, "evaluate", {"control_id": "CTL-FRESH-001", "params": [1]}).status_code == 400


def test_evaluate_envelope_shape():
    c, _ = client()
    body = post(c, "evaluate", {"control_id": "CTL-CDS-001", "params": {"version": "2.5.0"}}).get_json()
    assert list(body) == ["schema_version", "request", "result", "evidence", "meta"]
    assert body["request"] == {"control_id": "CTL-CDS-001", "params": {"version": "2.5.0"}}
    assert body["result"] == DECISION
    assert [e["layer"] for e in body["evidence"]] == ["cataloged", "enforced"]
    assert set(body["evidence"][0]) == set(GOLDEN)
    assert set(body["evidence"][0]["subject"]) == set(GOLDEN["subject"])
    meta = body["meta"]
    assert set(meta) == {"evaluated_at", "collector", "template_revision", "policy_revision", "executed_as", "duration_ms"}
    assert meta["policy_revision"] == "sha256:policy" and meta["executed_as"] == "anonymous"


def test_collect_has_no_result_and_no_policy_revision():
    c, _ = client()
    body = post(c, "collect", {"control_id": "CTL-FRESH-001"}).get_json()
    assert "result" not in body
    assert body["meta"]["policy_revision"] is None


def test_opa_input_carries_layers_params_and_no_raw():
    opa = FakeOpa()
    c, _ = client(opa=opa)
    post(c, "evaluate", {"control_id": "CTL-CDS-001", "params": {"version": "2.6.0"}}, "?include_raw=true")
    policy, sent = opa.inputs[0]
    assert policy == "cds_code_approval"
    assert sent["control"]["layers"] == [{"name": "cataloged", "kind": "detective"},
                                         {"name": "enforced", "kind": "preventive"}]
    assert sent["params"] == {"app_id": "AIT-12345", "version": "2.6.0"}
    assert all(e["raw"] is None for e in sent["evidence"])


def test_include_raw_toggles_raw():
    c, collector = client()
    without = post(c, "collect", {"control_id": "CTL-FRESH-001"}).get_json()
    with_raw = post(c, "collect", {"control_id": "CTL-FRESH-001"}, "?include_raw=true").get_json()
    assert without["evidence"][0]["raw"] is None
    assert with_raw["evidence"][0]["raw"] == {"content": "full"}
    assert [call["include_raw"] for call in collector.calls] == [False, True]


def test_source_error_is_200_with_error_evidence():
    c, _ = client(collector=FakeCollector(fail="error"))
    response = post(c, "collect", {"control_id": "CTL-FRESH-001"})
    assert response.status_code == 200
    assert "SharePoint returned 503" in response.get_json()["evidence"][0]["error"]


def test_collector_exception_becomes_error_evidence():
    c, _ = client(collector=FakeCollector(fail="raise"))
    response = post(c, "collect", {"control_id": "CTL-FRESH-001"})
    assert response.status_code == 200
    assert response.get_json()["evidence"][0]["error"].startswith("KeyError")


def test_opa_down_is_200_error_result():
    c, _ = client(opa=FakeOpa(down=True))
    response = post(c, "evaluate", {"control_id": "CTL-FRESH-001"})
    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["status"] == "ERROR" and result["color"] == "red"
    assert result["reason"].startswith("OPA unavailable")


def test_revisions_are_stable():
    c, _ = client()
    first = post(c, "evaluate", {"control_id": "CTL-CDS-001"}).get_json()["meta"]
    second = post(c, "evaluate", {"control_id": "CTL-CDS-001"}).get_json()["meta"]
    assert first["template_revision"] == second["template_revision"]
    assert first["template_revision"].startswith("sha256:")


FILE_LINK = "http://sharepoint-mock:8000/sites/compliance/Shared%20Documents/Runbooks/runbook.docx"
FOLDER_LINK = "http://sharepoint-mock:8000/sites/compliance/Shared%20Documents/APS"


def test_debug_file_link():
    c, collector = client()
    response = post(c, "evaluate", {"url": FILE_LINK, "policy": "freshness"})
    assert response.status_code == 200
    assert collector.calls[0]["ref"] == {"site": "/sites/compliance",
                                         "path": "/sites/compliance/Shared Documents/Runbooks/runbook.docx"}
    assert collector.calls[0]["layer"] == "primary"


def test_debug_folder_link_needs_pattern():
    c, collector = client()
    assert post(c, "collect", {"url": FOLDER_LINK}).status_code == 400
    ok = post(c, "collect", {"url": FOLDER_LINK, "name_pattern": r"cloud-approvals-.*\.xlsx",
                             "selectors": {"a": {"xlsx_cell": {"sheet": "Info", "cell": "B2"}}}})
    assert ok.status_code == 200
    assert collector.calls[0]["ref"]["folder"] == "/sites/compliance/Shared Documents/APS"
    assert collector.calls[0]["ref"]["select"] == "newest"


@pytest.mark.parametrize("url", [
    "https://sharepoint-mock:8000/:w:/r/sites/compliance/_layouts/15/Doc.aspx?sourcedoc=%7Babc%7D",
    "http://sharepoint-mock:8000/sites/compliance/_layouts/15/Doc.aspx",
])
def test_debug_sharing_links_rejected(url):
    c, _ = client()
    response = post(c, "collect", {"url": url})
    assert response.status_code == 400 and "Sharing links" in response.get_json()["error"]


@pytest.mark.parametrize("url", ["http://evil.example/sites/compliance/a.docx", "file:///etc/passwd",
                                 "http://sharepoint-mock:8000/not-a-site/a.docx"])
def test_debug_bad_links_rejected(url):
    c, _ = client()
    assert post(c, "collect", {"url": url}).status_code == 400


def test_debug_policy_required_only_on_evaluate():
    c, _ = client()
    assert post(c, "collect", {"url": FILE_LINK}).status_code == 200
    response = post(c, "evaluate", {"url": FILE_LINK})
    assert response.status_code == 400 and "policy" in response.get_json()["error"]


def test_debug_bad_selector_is_400():
    c, _ = client()
    assert post(c, "collect", {"url": FILE_LINK, "selectors": {"x": {"pdf_page": 1}}}).status_code == 400


def test_health_lists_collectors():
    c, _ = client(opa=FakeOpa(down=True))
    body = c.get("/health").get_json()
    assert body == {"status": "ok", "opa": "unreachable", "collectors": ["fixture", "sharepoint"]}
