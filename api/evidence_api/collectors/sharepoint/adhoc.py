"""Ad-hoc runs from a SharePoint link, without a control template."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import unquote, urlparse

from ...templates import TemplateError, parse_template

SITE = re.compile(r"^(/sites/[^/]+)(/.*)?$")


class DebugError(TemplateError):
    pass


def parse_link(url: str, allowed_hosts: set[str]) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise DebugError(f"url must be an http(s) SharePoint link, got {url!r}")
    if parsed.hostname not in allowed_hosts:
        raise DebugError(f"Host {parsed.hostname!r} is not in SHAREPOINT_ALLOWED_HOSTS")
    path = unquote(parsed.path).rstrip("/")
    if path.startswith("/:") or path.lower().endswith("/doc.aspx"):
        raise DebugError("Sharing links aren't supported. Use the file or folder's direct path, "
                         "such as https://host/sites/<site>/Shared Documents/<file>.")
    match = SITE.match(path)
    if not match or not match.group(2):
        raise DebugError(f"Link must point inside a site, like /sites/<site>/..., got {path!r}")
    return match.group(1), path


def build_template(body: dict[str, Any], allowed_hosts: set[str], require_policy: bool):
    policy = body.get("policy")
    if require_policy and not policy:
        raise DebugError("policy is required for /evaluate")
    site, path = parse_link(str(body["url"]), allowed_hosts)
    if "." in path.rsplit("/", 1)[-1]:
        ref = {"site": site, "path": path}
    else:
        pattern = body.get("name_pattern") or (body.get("params") or {}).get("name_pattern")
        if not pattern:
            raise DebugError("A folder link needs name_pattern (in the body or params)")
        ref = {"site": site, "folder": path, "name_pattern": pattern, "select": body.get("select", "newest")}
    spec = {
        "control_id": "DEBUG",
        "description": "Ad-hoc run from a link",
        "policy": policy or "",
        "layers": [{"name": "primary", "kind": "detective",
                    "sources": [{"collector": "sharepoint", "ref": ref, "selectors": body.get("selectors") or {}}]}],
    }
    revision = "sha256:" + hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    return parse_template(spec, revision=revision)
