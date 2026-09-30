"""Everything specific to SharePoint: the REST v1 collector, its auth, column names and ad-hoc link runs."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from .collector import SharePointCollector, file_uri

__all__ = ["SharePointCollector", "allowed_hosts", "config_defaults", "file_uri", "from_config"]


def config_defaults() -> dict[str, Any]:
    """Return SHAREPOINT_BASE_URL and SHAREPOINT_ALLOWED_HOSTS, defaulting hosts to the URL's hostname."""
    base_url = os.environ.get("SHAREPOINT_BASE_URL", "http://localhost:8000")
    return {
        "SHAREPOINT_BASE_URL": base_url,
        "SHAREPOINT_ALLOWED_HOSTS": os.environ.get("SHAREPOINT_ALLOWED_HOSTS", urlparse(base_url).hostname or ""),
    }


def from_config(config: dict[str, Any]) -> SharePointCollector:
    """Build a SharePointCollector for the configured base URL and COLLECTOR_TIMEOUT."""
    return SharePointCollector(config["SHAREPOINT_BASE_URL"], timeout=config["COLLECTOR_TIMEOUT"])


def allowed_hosts(config: dict[str, Any]) -> set[str]:
    """Return the comma-separated SHAREPOINT_ALLOWED_HOSTS as a set, ignoring blank entries."""
    return {h.strip() for h in config["SHAREPOINT_ALLOWED_HOSTS"].split(",") if h.strip()}
