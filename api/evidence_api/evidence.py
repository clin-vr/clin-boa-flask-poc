"""The evidence shape every collector returns and OPA receives as input.evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Owner:
    value: str
    source: str


@dataclass
class Subject:
    id: str | None = None
    uri: str | None = None
    title: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    modified_by: str | None = None
    text: str | None = None
    owners: list[Owner] = field(default_factory=list)


@dataclass
class Value:
    value: Any = None
    found: bool = False
    location: str | None = None


@dataclass
class Evidence:
    layer: str
    source: str
    found: bool = False
    error: str | None = None
    subject: Subject = field(default_factory=Subject)
    values: dict[str, Value] = field(default_factory=dict)
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
