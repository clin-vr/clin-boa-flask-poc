"""Flask app for the evidence service: the /collect, /evaluate and /health endpoints."""

import logging
import os
import time

from flask import Flask, jsonify, request

from . import envelope
from .collectors import build_collectors, collector_config_defaults, sharepoint
from .collectors.base import Collector
from .collectors.sharepoint import adhoc
from .opa_client import OpaClient, OpaUnavailable
from .templates import TemplateError, TemplateNotFound, load_template, resolve

logger = logging.getLogger(__name__)


def _default_config() -> dict:
    """Return the default config: collector defaults plus OPA and template settings from the environment."""
    return {
        **collector_config_defaults(),
        "OPA_URL": os.environ.get("OPA_URL", "http://localhost:8181"),
        "TEMPLATES_DIR": os.environ.get("TEMPLATES_DIR", "templates"),
        "COLLECTOR_TIMEOUT": float(os.environ.get("COLLECTOR_TIMEOUT", "5")),
        "OPA_TIMEOUT": float(os.environ.get("OPA_TIMEOUT", "5")),
    }


def create_app(config: dict | None = None, collectors: dict[str, Collector] | None = None,
               opa: OpaClient | None = None) -> Flask:
    """Build the Flask app, using the given collectors and OPA client or building them from config."""
    app = Flask(__name__)
    app.json.sort_keys = False
    app.config.update(_default_config())
    app.config.update(config or {})
    allowed_hosts = sharepoint.allowed_hosts(app.config)

    collectors = collectors or build_collectors(app.config)
    opa = opa or OpaClient(app.config["OPA_URL"], timeout=app.config["OPA_TIMEOUT"])
    executed_as = ", ".join(sorted({collector.principal() for collector in collectors.values()}))

    def bad_request(message: str, status: int = 400):
        """Return a JSON error response with the given status."""
        return jsonify(error=message), status

    def template_for(body: dict, evaluate: bool):
        """Load the control's template, or build an ad-hoc one from url, and resolve its params.

        Raises TemplateNotFound or TemplateError, including when a source names an unknown collector.
        """
        if "control_id" in body:
            template = load_template(app.config["TEMPLATES_DIR"], str(body["control_id"]))
        elif "url" in body:
            template = adhoc.build_template(body, allowed_hosts, require_policy=evaluate)
        else:
            raise TemplateError("Body needs control_id, or url for an ad-hoc run")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise TemplateError("params must be an object")
        resolved = resolve(template, params)
        for layer in resolved.layers:
            for source in layer.sources:
                if source.collector not in collectors:
                    raise TemplateError(f"Unknown collector {source.collector!r}")
        return resolved

    def gather(template, include_raw: bool) -> list[dict]:
        """Collect evidence from every source; a source whose collector raises is reported as unreachable."""
        evidence = []
        for layer in template.layers:
            for source in layer.sources:
                collector = collectors[source.collector]
                try:
                    item = collector.collect(layer.name, source.ref, source.selectors,
                                             owner=template.owner, include_raw=include_raw)
                except Exception as exc:  # noqa: BLE001 - one failing source must not fail the request
                    logger.exception("Collector %s failed", source.collector)
                    item = collector.unreachable(layer.name, exc)
                evidence.append(item.to_dict())
        return evidence

    def run(evaluate: bool):
        """Handle a collect or evaluate request and return the envelope as a JSON response.

        Returns 400 for a bad body or template and 404 for an unknown control or policy;
        an unreachable OPA gives an ERROR result instead of failing the request.
        """
        started = time.monotonic()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return bad_request("Request body must be a JSON object")
        try:
            template = template_for(body, evaluate)
        except TemplateNotFound as exc:
            return bad_request(str(exc), 404)
        except TemplateError as exc:
            return bad_request(str(exc))

        include_raw = request.args.get("include_raw", "").lower() in ("1", "true")
        evidence = gather(template, include_raw)

        result, policy_revision = None, None
        if evaluate:
            try:
                result = opa.evaluate(template.policy, envelope.opa_input(template, evidence))
                if result is None:
                    return bad_request(f"Unknown policy {template.policy!r}", 404)
                policy_revision = opa.policy_revision()
            except OpaUnavailable as exc:
                result = envelope.error_result(f"OPA unavailable: {exc}")

        return jsonify(envelope.build(body, template, evidence, started=started, finished=time.monotonic(),
                                      executed_as=executed_as, result=result,
                                      policy_revision=policy_revision, evaluate=evaluate))

    @app.post("/collect")
    def collect():
        """Collect evidence for the requested control without a policy verdict."""
        return run(evaluate=False)

    @app.post("/evaluate")
    def evaluate():
        """Collect evidence for the requested control and return OPA's verdict on it."""
        return run(evaluate=True)

    @app.get("/health")
    def health():
        """Report service health, whether OPA is reachable, and the configured collector names."""
        return jsonify(status="ok", opa="ok" if opa.healthy() else "unreachable", collectors=sorted(collectors))

    return app
