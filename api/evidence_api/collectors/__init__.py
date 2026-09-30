"""The sources the service collects from. Each module provides config_defaults() and from_config()."""

from __future__ import annotations

from typing import Any

from . import fixture, sharepoint
from .base import Collector

SOURCES = {"sharepoint": sharepoint, "fixture": fixture}


def collector_config_defaults() -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for source in SOURCES.values():
        defaults.update(source.config_defaults())
    return defaults


def build_collectors(config: dict[str, Any]) -> dict[str, Collector]:
    return {name: source.from_config(config) for name, source in SOURCES.items()}
