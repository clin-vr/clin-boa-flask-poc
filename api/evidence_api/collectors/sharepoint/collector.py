"""SharePoint Server on-prem, REST v1 (`/_api/web/...`).

Endpoint shapes used:
  GetFileByServerRelativeUrl('<path>')?$expand=Author,ModifiedBy -> file properties and people
  GetFileByServerRelativeUrl('<path>')/$value                     -> file content
  GetFileByServerRelativeUrl('<path>')/ListItemAllFields          -> library columns
  GetFolderByServerRelativeUrl('<path>')/Files                    -> folder listing
  AssociatedOwnerGroup/Users                                      -> site owners group members

UNVERIFIED against the bank's farm — no external access. The version
(2013/2016/2019/SE) also needs confirming; the REST surface differs.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...evidence import Evidence, Owner, Subject, Value
from ...parsing import ParseError, ParserRegistry
from ...selectors import apply, needs_content
from ..base import Collector, SourceUnreachable
from .auth import AnonymousAuth, AuthStrategy
from .columns import internal_column_name

logger = logging.getLogger(__name__)

OWNER_COLUMN = "Owner Team"


def file_uri(base_url: str, server_relative_path: str) -> str:
    """Return the absolute URL of a server-relative path, percent-encoded except for slashes."""
    return base_url.rstrip("/") + quote(server_relative_path, safe="/")


def _quote(path: str) -> str:
    """Percent-encode a server-relative path for an OData string literal."""
    # Single quotes inside a server-relative URL must be doubled for OData.
    return quote(path.replace("'", "''"), safe="/")


def _unwrap(body: dict[str, Any]) -> Any:
    """Accept both odata=verbose ({"d": ...}) and nometadata shapes."""
    body = body.get("d", body)
    if "results" in body:
        return body["results"]
    if "value" in body and isinstance(body["value"], list):
        return body["value"]
    return body


def _person(value: Any) -> str | None:
    """Return a person field's Title, or None when it is missing or deferred."""
    return value.get("Title") if isinstance(value, dict) and "__deferred" not in value else None


class SharePointCollector(Collector):
    """Collector for documents on SharePoint Server on-prem over REST v1."""

    name = "sharepoint"

    def __init__(self, base_url: str, auth: AuthStrategy | None = None, *, timeout: float = 5,
                 session: Any = None, registry: ParserRegistry | None = None) -> None:
        """Set up the HTTP session with auth and the JSON nometadata Accept header.

        Without a given session, build one with a small connection pool and one retry on GETs."""
        self.base_url = base_url.rstrip("/")
        self.auth = auth or AnonymousAuth()
        self.timeout = timeout
        self.registry = registry or ParserRegistry()
        if session is None:
            session = requests.Session()
            retry = Retry(total=1, backoff_factor=0.2, status_forcelist=(429, 502, 503, 504),
                          allowed_methods=frozenset({"GET"}), raise_on_status=False)
            # Single connection pool: NTLM binds its handshake to the connection, so
            # reuse is what keeps a read at one round trip instead of three.
            adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
        session.headers.update({"Accept": "application/json;odata=nometadata"})
        self.session = self.auth.apply(session)

    def principal(self) -> str:
        """Return the identity the auth strategy reads as."""
        return self.auth.principal()

    def _get(self, url: str) -> Any | None:
        """GET a URL and return the response, or None on 404; raise SourceUnreachable on other 4xx/5xx."""
        response = self.session.get(url, timeout=self.timeout)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SourceUnreachable(f"SharePoint returned {response.status_code} for {url}")
        return response

    def _json(self, url: str) -> Any | None:
        """GET a URL and return its unwrapped JSON body, or None on 404."""
        response = self._get(url)
        return None if response is None else _unwrap(response.json())

    def _file_url(self, site: str, path: str, suffix: str = "") -> str:
        """Build the REST URL for a file in a site, with an optional sub-resource or query suffix."""
        return f"{self.base_url}{site}/_api/web/GetFileByServerRelativeUrl('{_quote(path)}'){suffix}"

    def _locate(self, ref: dict[str, Any]) -> str | None:
        """Return the server-relative path of the referenced file, or None when no folder file matches.

        A ref with "path" is returned as is; otherwise the folder's files are matched against name_pattern
        and the newest by TimeLastModified, or the first by name when select is "first", is chosen."""
        if "path" in ref:
            return ref["path"]
        site, folder = ref["site"], ref["folder"]
        rows = self._json(f"{self.base_url}{site}/_api/web/GetFolderByServerRelativeUrl('{_quote(folder)}')/Files")
        pattern = re.compile(ref.get("name_pattern", ".*"))
        matches = [row for row in rows or [] if pattern.fullmatch(row["Name"])]
        if not matches:
            return None
        if ref.get("select", "newest") == "first":
            chosen = min(matches, key=lambda row: row["Name"])
        else:
            chosen = max(matches, key=lambda row: row["TimeLastModified"])
        return chosen["ServerRelativeUrl"]

    def collect(self, layer: str, ref: dict[str, Any], selectors: dict[str, Any], *,
                owner: str | None = None, include_raw: bool = False) -> Evidence:
        """Collect the referenced document, returning Evidence with error set on request failures."""
        try:
            return self._collect(layer, ref, selectors, owner, include_raw)
        except (requests.RequestException, SourceUnreachable) as exc:
            return self.unreachable(layer, exc)

    def _collect(self, layer: str, ref: dict[str, Any], selectors: dict[str, Any],
                 owner: str | None, include_raw: bool) -> Evidence:
        """Read the document's properties, library columns, site owners and, if needed, content into Evidence.

        Return found=False when no document is found; a parse failure marks unfound values unreadable.
        Raise requests.RequestException or SourceUnreachable when SharePoint can't be read."""
        site = ref["site"]
        path = self._locate(ref)
        props = self._json(self._file_url(site, path, "?$expand=Author,ModifiedBy")) if path else None
        if props is None:
            return Evidence(layer=layer, source=self.name, found=False,
                            values={name: Value(None, False, "document not found") for name in selectors})

        list_fields = self._json(self._file_url(site, path, "/ListItemAllFields")) or {}
        group = self._json(f"{self.base_url}{site}/_api/web/AssociatedOwnerGroup/Users") or []

        document, unreadable = None, None
        if needs_content(selectors) or include_raw:
            content = self._get(self._file_url(site, path, "/$value"))
            try:
                document = self.registry.parse(content.content, path.rsplit(".", 1)[-1])
            except ParseError as exc:
                unreadable = f"unreadable: {exc}"

        author, modified_by = _person(props.get("Author")), _person(props.get("ModifiedBy"))
        owners = [Owner(owner, "template")] if owner else []
        team = list_fields.get(internal_column_name(OWNER_COLUMN))
        if team:
            owners.append(Owner(team, f"column:{OWNER_COLUMN}"))
        if author:
            owners.append(Owner(author, "sp:Author"))
        if modified_by:
            owners.append(Owner(modified_by, "sp:ModifiedBy"))
        owners.extend(Owner(member["Title"], "sp:AssociatedOwnerGroup") for member in group)

        subject = Subject(
            id=props.get("Name"),
            uri=file_uri(self.base_url, path),
            title=props.get("Name"),
            created_at=props.get("TimeCreated"),
            modified_at=props.get("TimeLastModified"),
            modified_by=modified_by,
            text=document.text if include_raw and document else None,
            owners=owners,
        )
        metadata = {
            "id": subject.id, "title": subject.title, "created_at": subject.created_at,
            "modified_at": subject.modified_at, "modified_by": modified_by, "author": author,
            "version_label": props.get("UIVersionLabel"), "size_bytes": props.get("Length"),
        }
        values = apply(selectors, document, metadata, list_fields,
                       metadata_location="sharepoint metadata", column_key=internal_column_name)
        if unreadable:
            values = {name: value if value.found else Value(None, False, unreadable) for name, value in values.items()}

        raw = None
        if include_raw:
            raw = {"metadata": props, "list_fields": list_fields,
                   "content": document.to_dict() if document else None}
        return Evidence(layer=layer, source=self.name, found=True, subject=subject, values=values, raw=raw)
