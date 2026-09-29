"""Control templates: which evidence to collect, from where, and which policy judges it."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .selectors import validate_selectors

CONTROL_ID = re.compile(r"^[A-Za-z0-9_-]+$")
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
LAYER_KINDS = {"detective", "preventive"}


class TemplateError(ValueError):
    pass


class TemplateNotFound(LookupError):
    pass


@dataclass
class Source:
    collector: str
    ref: dict[str, Any]
    selectors: dict[str, Any] = field(default_factory=dict)


@dataclass
class Layer:
    name: str
    kind: str
    sources: list[Source]


@dataclass
class Template:
    control_id: str
    policy: str
    layers: list[Layer]
    description: str = ""
    owner: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    revision: str | None = None


def parse_template(body: dict[str, Any], revision: str | None = None) -> Template:
    try:
        layers = [
            Layer(
                name=layer["name"],
                kind=layer["kind"],
                sources=[Source(s["collector"], s["ref"], s.get("selectors", {})) for s in layer["sources"]],
            )
            for layer in body["layers"]
        ]
        template = Template(
            control_id=body["control_id"],
            policy=body["policy"],
            layers=layers,
            description=body.get("description", ""),
            owner=body.get("owner"),
            params=body.get("params", {}),
            revision=revision,
        )
    except (KeyError, TypeError) as exc:
        raise TemplateError(f"Template is missing a required field: {exc}") from exc
    validate(template)
    return template


def validate(template: Template) -> None:
    if not template.layers:
        raise TemplateError("Template has no layers")
    names = [layer.name for layer in template.layers]
    if len(set(names)) != len(names):
        raise TemplateError(f"Duplicate layer names: {names}")
    for layer in template.layers:
        if layer.kind not in LAYER_KINDS:
            raise TemplateError(f"Layer {layer.name!r} has kind {layer.kind!r}; expected one of {sorted(LAYER_KINDS)}")
        if not layer.sources:
            raise TemplateError(f"Layer {layer.name!r} has no sources")
        for source in layer.sources:
            if not isinstance(source.ref, dict):
                raise TemplateError(f"Layer {layer.name!r} has a source without a ref object")
            validate_selectors(source.selectors)


def load_template(templates_dir: Path | str, control_id: str) -> Template:
    if not CONTROL_ID.match(control_id):
        raise TemplateError(f"Invalid control_id {control_id!r}")
    path = Path(templates_dir) / f"{control_id}.json"
    if not path.is_file():
        raise TemplateNotFound(f"No template for control {control_id!r}")
    raw = path.read_bytes()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TemplateError(f"Template {path.name} is not valid JSON: {exc}") from exc
    return parse_template(body, revision="sha256:" + hashlib.sha256(raw).hexdigest())


def substitute(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, params) for v in value]
    if not isinstance(value, str):
        return value

    def replace_one(match: re.Match) -> str:
        name = match.group(1)
        if name not in params:
            raise TemplateError(f"Missing param {name!r}")
        return str(params[name])

    return PLACEHOLDER.sub(replace_one, value)


def resolve(template: Template, request_params: dict[str, Any] | None = None) -> Template:
    params = {**template.params, **(request_params or {})}
    layers = [
        Layer(layer.name, layer.kind,
              [Source(s.collector, substitute(s.ref, params), substitute(s.selectors, params)) for s in layer.sources])
        for layer in template.layers
    ]
    return replace(template, layers=layers, params=params)
