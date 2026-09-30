"""Base class for collectors, which turn one template source into one Evidence entry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..evidence import Evidence


class SourceUnreachable(RuntimeError):
    """The source could not be read at all, as opposed to the evidence being absent."""


class Collector(ABC):
    """A source that turns one template source reference into one Evidence entry."""

    name: str = "base"

    @abstractmethod
    def collect(self, layer: str, ref: dict[str, Any], selectors: dict[str, Any], *,
                owner: str | None = None, include_raw: bool = False) -> Evidence:
        """Return Evidence with found=False when the evidence is absent, and error set when
        the source is unreachable. Never raise for either case."""

    def principal(self) -> str:
        """Return the identity reads execute as, "anonymous" unless a subclass overrides it."""
        return "anonymous"

    def unreachable(self, layer: str, exc: Exception) -> Evidence:
        """Return Evidence for the layer with error set to the exception's type and message."""
        return Evidence(layer=layer, source=self.name, error=f"{type(exc).__name__}: {exc}")
