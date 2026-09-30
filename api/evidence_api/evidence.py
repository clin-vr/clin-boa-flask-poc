"""The evidence shape every collector returns and OPA receives as input.evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Owner:
    """An owner of the subject and where that owner was read from."""
    value: str
    source: str


@dataclass
class Subject:
    """The document or item the evidence is about, as the collector found it."""
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
    """One selector's result: the value, whether it was found, and where it was looked for."""
    value: Any = None
    found: bool = False
    location: str | None = None


@dataclass
class Evidence:
    """What one source yielded for a layer: the subject, selector values, any error, and optional raw data."""
    layer: str
    source: str
    found: bool = False
    error: str | None = None
    subject: Subject = field(default_factory=Subject)
    values: dict[str, Value] = field(default_factory=dict)
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the evidence as a plain dict, nested dataclasses included."""
        return asdict(self)
