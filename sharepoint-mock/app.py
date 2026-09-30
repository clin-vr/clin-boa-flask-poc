"""SharePoint Server REST v1 stand-in serving the generated samples."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Flask, Response, jsonify, request, send_file

from generate_samples import load_manifest, parse_offset

FILE_CALL = re.compile(r"^GetFileByServerRelativeUrl\('(?P<path>(?:[^']|'')*)'\)(?P<suffix>/.*)?$")
FOLDER_CALL = re.compile(r"^GetFolderByServerRelativeUrl\('(?P<path>(?:[^']|'')*)'\)/Files$")

NOT_FOUND = {"code": "-2130575338, Microsoft.SharePoint.SPException", "message": "File Not Found."}
BAD_REQUEST = {"code": "-1, Microsoft.SharePoint.Client.InvalidClientQueryException",
               "message": "The expression is not valid."}


def _iso(moment: datetime) -> str:
    """Format a datetime as a SharePoint-style UTC timestamp."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def create_app(samples_dir: Path | str | None = None, manifest: dict | None = None,
               base_url: str | None = None, now_fn: Callable[[], datetime] | None = None) -> Flask:
    """Build the mock Flask app serving the manifest's files from the samples directory.

    Arguments left as None fall back to SAMPLES_DIR, the bundled manifest, SHAREPOINT_BASE_URL and UTC now.
    """
    app = Flask(__name__)
    samples = Path(samples_dir or os.environ.get("SAMPLES_DIR", "/data/files"))
    manifest = manifest or load_manifest()
    base = (base_url or os.environ.get("SHAREPOINT_BASE_URL", "http://sharepoint-mock:8000")).rstrip("/")
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    files = {entry["path"]: entry for entry in manifest["files"]}
    users = manifest["users"]

    def verbose() -> bool:
        """Return True when the request's Accept header asks for odata=verbose."""
        return "odata=verbose" in request.headers.get("Accept", "")

    def single(body: dict, kind: str) -> Response:
        """Return one entity as JSON, wrapped in `d` with type metadata for verbose requests."""
        if verbose():
            return jsonify(d={"__metadata": {"type": kind}, **body})
        return jsonify(body)

    def collection(rows: list[dict], kind: str) -> Response:
        """Return a list of entities as `value`, or as `d.results` with type metadata for verbose requests."""
        if verbose():
            return jsonify(d={"results": [{"__metadata": {"type": kind}, **row} for row in rows]})
        return jsonify(value=rows)

    def error(status: int, err: dict) -> tuple[Response, int]:
        """Return a SharePoint-shaped OData error body with the given status."""
        if verbose():
            return jsonify(error={"code": err["code"], "message": {"lang": "en-US", "value": err["message"]}}), status
        return jsonify({"odata.error": {"code": err["code"],
                                        "message": {"lang": "en-US", "value": err["message"]}}}), status

    def user(key: str) -> dict:
        """Return the Id, Title, Email and LoginName of a manifest user."""
        return {k: users[key][k] for k in ("Id", "Title", "Email", "LoginName")}

    def expanded() -> set[str]:
        """Return the property names listed in the request's $expand parameter."""
        return {part.strip() for part in request.args.get("$expand", "").split(",") if part.strip()}

    def file_props(entry: dict) -> dict:
        """Return the SP.File properties for a manifest entry.

        Author and ModifiedBy are included when expanded, and deferred links on verbose requests.
        """
        now = now_fn()
        path = entry["path"]
        props: dict[str, Any] = {
            "Name": path.rsplit("/", 1)[-1],
            "ServerRelativeUrl": path,
            "Length": str((samples / path.lstrip("/")).stat().st_size),
            "TimeCreated": _iso(parse_offset(entry["created"], now)),
            "TimeLastModified": _iso(parse_offset(entry["modified"], now)),
            "UIVersionLabel": entry["version"],
            "Exists": True,
        }
        wanted = expanded()
        for field, key in (("Author", "author"), ("ModifiedBy", "modified_by")):
            if field in wanted:
                props[field] = user(entry[key])
            elif verbose():
                props[field] = {"__deferred": {"uri": f"{base}{path}/{field}"}}
        return props

    def list_item(entry: dict) -> dict:
        """Return the ListItemAllFields properties for a manifest entry."""
        return {
            "Id": list(files).index(entry["path"]) + 1,
            "Title": None,
            "FileLeafRef": entry["path"].rsplit("/", 1)[-1],
            "AuthorId": users[entry["author"]]["Id"],
            "EditorId": users[entry["modified_by"]]["Id"],
            **entry["list_fields"],
        }

    @app.get("/health")
    def health():
        """Return status ok and the number of files served."""
        return jsonify(status="ok", files=len(files))

    @app.get("/<path:site>/_api/web/AssociatedOwnerGroup/Users")
    def owner_group(site: str):
        """Return the members of the site's owner group, or 404 when the site has none."""
        group = manifest["sites"].get(f"/{site}", {}).get("owner_group")
        if group is None:
            return error(404, NOT_FOUND)
        return collection([user(m) for m in group["members"]], "SP.User")

    @app.get("/<path:site>/_api/web/<path:call>")
    def web_call(site: str, call: str):
        """Serve file properties, ListItemAllFields, $value and folder listings under a site.

        Returns 404 for unknown files or folders outside the site, and 400 for unrecognized calls.
        """
        site_prefix = f"/{site}"
        if match := FILE_CALL.match(call):
            path = match["path"].replace("''", "'")
            entry = files.get(path)
            if entry is None or not path.startswith(site_prefix + "/"):
                return error(404, NOT_FOUND)
            suffix = match["suffix"] or ""
            if suffix == "":
                return single(file_props(entry), "SP.File")
            if suffix == "/ListItemAllFields":
                return single(list_item(entry), "SP.Data.DocumentsItem")
            if suffix == "/$value":
                return send_file(samples / path.lstrip("/"), mimetype="application/octet-stream")
            return error(400, BAD_REQUEST)
        if match := FOLDER_CALL.match(call):
            folder = match["path"].replace("''", "'").rstrip("/")
            if not folder.startswith(site_prefix + "/"):
                return error(404, NOT_FOUND)
            children = [e for p, e in files.items() if p.rsplit("/", 1)[0] == folder]
            known_folder = any(p.startswith(folder + "/") for p in files)
            if not known_folder:
                return error(404, NOT_FOUND)
            return collection([file_props(e) for e in children], "SP.File")
        return error(400, BAD_REQUEST)

    return app
