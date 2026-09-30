"""Unit tests for the SharePoint and fixture collectors using a fake HTTP session; needs no services."""

import json

import pytest
import requests

from evidence_api.collectors.fixture import FixtureCollector
from evidence_api.collectors.sharepoint import SharePointCollector

BASE = "http://sp"
SITE = "/sites/cds"
FOLDER_URL = f"{BASE}{SITE}/_api/web/GetFolderByServerRelativeUrl('/sites/cds/Approvals')/Files"
GROUP_URL = f"{BASE}{SITE}/_api/web/AssociatedOwnerGroup/Users"


def file_url(path, suffix=""):
    """Return the mock REST URL for a file's server-relative path plus an optional suffix."""
    from urllib.parse import quote
    return f"{BASE}{SITE}/_api/web/GetFileByServerRelativeUrl('{quote(path, safe='/')}'){suffix}"


class FakeResponse:
    """Minimal stand-in for a requests response with a status, JSON body and content."""
    def __init__(self, status=200, body=None, content=b""):
        """Store the status code, JSON body and raw content."""
        self.status_code, self._body, self.content = status, body, content

    def json(self):
        """Return the stored JSON body."""
        return self._body


class FakeSession:
    """Session stand-in that serves canned responses by URL and records each request."""
    def __init__(self, routes):
        """Store the URL routes and start with empty headers and call log."""
        self.routes, self.headers, self.calls = routes, {}, []

    def get(self, url, timeout=None):
        """Record the URL and return its response, raise its exception, or return a 404."""
        self.calls.append(url)
        result = self.routes.get(url, FakeResponse(404, {"odata.error": {}}))
        if isinstance(result, Exception):
            raise result
        return result


def props(name, modified, verbose=False):
    """Build SharePoint file properties for a file in the Approvals folder, optionally verbose-wrapped."""
    body = {"Name": name, "ServerRelativeUrl": f"/sites/cds/Approvals/{name}", "TimeCreated": "2026-09-01T00:00:00Z",
            "TimeLastModified": modified, "UIVersionLabel": "1.0", "Length": "10",
            "Author": {"Title": "A. Approver"}, "ModifiedBy": {"Title": "B. Approver"}}
    return {"d": body} if verbose else body


def routes_for(name="a.json", verbose=False, listing=None):
    """Build the fake routes for one file: folder listing, properties, list item, content and group."""
    path = f"/sites/cds/Approvals/{name}"
    listing = listing if listing is not None else [props(name, "2026-09-20T00:00:00Z")]
    return {
        FOLDER_URL: FakeResponse(body={"d": {"results": listing}} if verbose else {"value": listing}),
        file_url(path, "?$expand=Author,ModifiedBy"): FakeResponse(body=props(name, "2026-09-20T00:00:00Z", verbose)),
        file_url(path, "/ListItemAllFields"): FakeResponse(body={"Owner_x0020_Team": "CDS"}),
        file_url(path, "/$value"): FakeResponse(content=json.dumps({"gate": {"result": "allow"}}).encode()),
        GROUP_URL: FakeResponse(body={"value": [{"Title": "A. Approver"}, {"Title": "C. Member"}]}),
    }


REF = {"site": SITE, "folder": "/sites/cds/Approvals", "name_pattern": r".*\.json", "select": "newest"}
SELECTORS = {"result": {"json_path": "gate.result"}, "modified": {"metadata": "modified_at"}}


def collect(routes, ref=REF, selectors=SELECTORS, **kwargs):
    """Run a SharePointCollector over the fake routes and return its primary-layer evidence."""
    return SharePointCollector(BASE, session=FakeSession(routes)).collect("primary", ref, selectors, **kwargs)


@pytest.mark.parametrize("verbose", [False, True])
def test_both_odata_shapes(verbose):
    """Nometadata and verbose OData responses give the same evidence."""
    evidence = collect(routes_for(verbose=verbose), owner="CDS")
    assert evidence.found and evidence.error is None
    assert evidence.values["result"].value == "allow"
    assert evidence.values["modified"].value == "2026-09-20T00:00:00Z"
    assert evidence.subject.modified_by == "B. Approver"


def test_owners_are_tagged_by_source():
    """Owners are listed in order, each tagged with where it came from."""
    evidence = collect(routes_for(), owner="CDS")
    assert [(o.value, o.source) for o in evidence.subject.owners] == [
        ("CDS", "template"), ("CDS", "column:Owner Team"), ("A. Approver", "sp:Author"),
        ("B. Approver", "sp:ModifiedBy"), ("A. Approver", "sp:AssociatedOwnerGroup"),
        ("C. Member", "sp:AssociatedOwnerGroup")]


def test_newest_match_is_chosen():
    """select=newest picks the most recently modified file matching the pattern."""
    listing = [props("old.json", "2026-01-01T00:00:00Z"), props("new.json", "2026-09-01T00:00:00Z"),
               props("skip.txt", "2026-12-01T00:00:00Z")]
    routes = routes_for("new.json", listing=listing)
    evidence = collect(routes)
    assert evidence.subject.id == "new.json"


def test_first_match_is_alphabetical():
    """select=first picks the alphabetically first matching file."""
    listing = [props("b.json", "2026-09-01T00:00:00Z"), props("a.json", "2026-01-01T00:00:00Z")]
    evidence = collect(routes_for("a.json", listing=listing), ref={**REF, "select": "first"})
    assert evidence.subject.id == "a.json"


def test_no_match_is_not_found():
    """No matching file gives found=False with every value marked document not found."""
    evidence = collect(routes_for(listing=[]))
    assert evidence.found is False and evidence.error is None
    assert all(not v.found and v.location == "document not found" for v in evidence.values.values())


def test_exact_path_404_is_not_found():
    """A 404 on an exact path gives found=False with no error."""
    evidence = collect({}, ref={"site": SITE, "path": "/sites/cds/Approvals/missing.docx"})
    assert evidence.found is False and evidence.error is None


def test_server_error_is_unreachable():
    """A 500 from SharePoint gives a SourceUnreachable error."""
    routes = routes_for()
    routes[FOLDER_URL] = FakeResponse(500, {})
    evidence = collect(routes)
    assert evidence.error.startswith("SourceUnreachable: SharePoint returned 500")


@pytest.mark.parametrize("exc", [requests.Timeout("slow"), requests.ConnectionError("dns")])
def test_transport_errors_are_unreachable(exc):
    """Timeouts and connection errors give an error named after the exception."""
    routes = routes_for()
    routes[FOLDER_URL] = exc
    evidence = collect(routes)
    assert evidence.found is False and evidence.error.startswith(type(exc).__name__)


def test_metadata_only_selectors_skip_download():
    """Metadata-only selectors never download the file content."""
    session = FakeSession(routes_for())
    SharePointCollector(BASE, session=session).collect("primary", REF, {"m": {"metadata": "modified_at"}})
    assert not any(url.endswith("/$value") for url in session.calls)


def test_unreadable_content_marks_values():
    """Unparseable content marks content values unreadable but keeps metadata values."""
    routes = routes_for()
    routes[file_url("/sites/cds/Approvals/a.json", "/$value")] = FakeResponse(content=b"{broken")
    evidence = collect(routes)
    assert evidence.found and evidence.error is None
    assert evidence.values["result"].location.startswith("unreadable:")
    assert evidence.values["modified"].found


def test_include_raw():
    """include_raw returns the list fields, parsed content and document text."""
    evidence = collect(routes_for(), include_raw=True)
    assert evidence.raw["list_fields"] == {"Owner_x0020_Team": "CDS"}
    assert evidence.raw["content"]["data"] == {"gate": {"result": "allow"}}
    assert evidence.subject.text is not None


def test_deferred_people_are_ignored():
    """A deferred ModifiedBy gives modified_by=None."""
    routes = routes_for()
    body = props("a.json", "2026-09-20T00:00:00Z")
    body["ModifiedBy"] = {"__deferred": {"uri": "x"}}
    routes[file_url("/sites/cds/Approvals/a.json", "?$expand=Author,ModifiedBy")] = FakeResponse(body=body)
    assert collect(routes).subject.modified_by is None


def test_fixture_collector(tmp_path):
    """The fixture collector reads a file, reports a missing one, and rejects path escapes."""
    (tmp_path / "ci").mkdir()
    (tmp_path / "ci/x.json").write_text(json.dumps({"gate": {"result": "allow"}}))
    collector = FixtureCollector(tmp_path)
    found = collector.collect("enforced", {"path": "ci/x.json"}, {"r": {"json_path": "gate.result"}}, owner="CDS")
    assert found.found and found.values["r"].value == "allow" and found.subject.uri == "fixture:ci/x.json"
    missing = collector.collect("enforced", {"path": "ci/nope.json"}, {"r": {"json_path": "gate.result"}})
    assert missing.found is False and missing.error is None
    escaped = collector.collect("enforced", {"path": "../etc/passwd"}, {})
    assert escaped.error and "escapes" in escaped.error
