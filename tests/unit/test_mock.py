"""Unit tests for the SharePoint REST v1 mock through its Flask test client; needs no services."""

import hashlib
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

from app import create_app
from generate_samples import generate

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
VERBOSE = {"Accept": "application/json;odata=verbose"}
NOMETA = {"Accept": "application/json;odata=nometadata"}
APPROVAL = "/sites/cds/Approvals/AIT-12345-2.4.0-approval.docx"
RUNBOOK = "/sites/compliance/Shared Documents/Runbooks/runbook.docx"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """Generate the samples and return a mock test client with its files directory."""
    root = tmp_path_factory.mktemp("mock")
    generate(root / "files", root / "fixtures", "http://mock", now=NOW)
    client = create_app(root / "files", base_url="http://mock", now_fn=lambda: NOW).test_client()
    return client, root / "files"


def file_url(site, path, suffix="", query=""):
    """Return the mock REST URL for a file, escaping quotes and adding an optional suffix and query."""
    literal = quote(path.replace("'", "''"), safe="/")
    return f"{site}/_api/web/GetFileByServerRelativeUrl('{literal}'){suffix}{query}"


def test_file_properties_nometadata(env):
    """Nometadata file properties carry name, dates, version and length but no people."""
    client, _ = env
    body = client.get(file_url("/sites/compliance", RUNBOOK), headers=NOMETA).get_json()
    assert body["Name"] == "runbook.docx"
    assert body["ServerRelativeUrl"] == RUNBOOK
    assert body["TimeLastModified"] == "2026-09-26T12:00:00Z"
    assert body["TimeCreated"] == "2026-03-13T12:00:00Z"
    assert body["UIVersionLabel"] == "4.0"
    assert body["Length"].isdigit()
    assert "Author" not in body and "ModifiedBy" not in body


def test_file_properties_verbose_defers_people(env):
    """Verbose file properties carry SP.File metadata and deferred people."""
    client, _ = env
    body = client.get(file_url("/sites/compliance", RUNBOOK), headers=VERBOSE).get_json()["d"]
    assert body["__metadata"]["type"] == "SP.File"
    assert "__deferred" in body["ModifiedBy"]


@pytest.mark.parametrize("headers,unwrap", [(NOMETA, lambda b: b), (VERBOSE, lambda b: b["d"])])
def test_expand_returns_people(env, headers, unwrap):
    """$expand=Author,ModifiedBy returns both people in either OData shape."""
    client, _ = env
    body = unwrap(client.get(file_url("/sites/compliance", RUNBOOK, query="?$expand=Author,ModifiedBy"),
                             headers=headers).get_json())
    assert body["Author"]["Title"] == "J. Analyst"
    assert body["ModifiedBy"]["Title"] == "K. Reviewer"


def test_list_item_uses_internal_column_names(env):
    """ListItemAllFields uses internal column names like Owner_x0020_Team."""
    client, _ = env
    body = client.get(file_url("/sites/compliance", RUNBOOK, "/ListItemAllFields"), headers=NOMETA).get_json()
    assert body["Owner_x0020_Team"] == "Platform Operations"
    assert "Owner Team" not in body
    assert body["EditorId"] == 12


def test_value_bytes_match_seed(env):
    """$value returns the exact bytes of the generated file."""
    client, files = env
    response = client.get(file_url("/sites/cds", APPROVAL, "/$value"))
    assert response.status_code == 200
    expected = hashlib.sha256((files / APPROVAL.lstrip("/")).read_bytes()).hexdigest()
    assert hashlib.sha256(response.data).hexdigest() == expected


@pytest.mark.parametrize("headers,rows", [
    (NOMETA, lambda b: b["value"]),
    (VERBOSE, lambda b: b["d"]["results"]),
])
def test_folder_listing(env, headers, rows):
    """A folder listing returns its files in either OData shape."""
    client, _ = env
    folder = quote("/sites/compliance/Shared Documents/APS", safe="/")
    body = client.get(f"/sites/compliance/_api/web/GetFolderByServerRelativeUrl('{folder}')/Files",
                      headers=headers).get_json()
    assert sorted(r["Name"] for r in rows(body)) == ["cloud-approvals-2026-06.xlsx", "cloud-approvals-2026-09.xlsx"]


def test_owner_group_members(env):
    """AssociatedOwnerGroup/Users returns the site's owner group members."""
    client, _ = env
    body = client.get("/sites/cds/_api/web/AssociatedOwnerGroup/Users", headers=NOMETA).get_json()
    assert [u["Title"] for u in body["value"]] == ["A. Approver", "B. Approver"]


@pytest.mark.parametrize("url", [
    file_url("/sites/compliance", "/sites/compliance/Shared Documents/Runbooks/missing.docx"),
    file_url("/sites/compliance", APPROVAL),
    "/sites/compliance/_api/web/GetFolderByServerRelativeUrl('/sites/compliance/Nope')/Files",
    "/sites/nope/_api/web/AssociatedOwnerGroup/Users",
])
def test_not_found_uses_sharepoint_error_shape(env, url):
    """Missing files, wrong-site files, folders and sites give a SharePoint 404."""
    client, _ = env
    response = client.get(url, headers=NOMETA)
    assert response.status_code == 404
    assert response.get_json()["odata.error"]["message"]["value"] == "File Not Found."


def test_verbose_error_shape(env):
    """A verbose 404 uses the verbose error shape."""
    client, _ = env
    response = client.get(file_url("/sites/compliance", "/sites/compliance/x.docx"), headers=VERBOSE)
    assert response.status_code == 404
    assert response.get_json()["error"]["message"]["value"] == "File Not Found."


def test_escaped_quote_in_path_is_decoded(env):
    """A doubled single quote in a path is decoded and gives 404, not 400."""
    client, _ = env
    response = client.get(file_url("/sites/compliance", "/sites/compliance/Shared Documents/O'Brien.docx"))
    assert response.status_code == 404


def test_unknown_call_is_bad_request(env):
    """An unsupported REST call gives 400."""
    client, _ = env
    assert client.get("/sites/compliance/_api/web/lists").status_code == 400
