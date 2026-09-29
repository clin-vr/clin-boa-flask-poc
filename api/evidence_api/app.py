import logging
import os
import time
from urllib.parse import urlparse

from flask import Flask, jsonify, request

from . import debug, envelope
from .collectors.base import Collector
from .collectors.fixture import FixtureCollector
from .collectors.sharepoint import SharePointCollector
from .opa_client import OpaClient, OpaUnavailable
from .templates import TemplateError, TemplateNotFound, load_template, resolve

logger = logging.getLogger(__name__)


def _default_config() -> dict:
    base_url = os.environ.get("SHAREPOINT_BASE_URL", "http://localhost:8000")
    return {
        "SHAREPOINT_BASE_URL": base_url,
        "SHAREPOINT_ALLOWED_HOSTS": os.environ.get("SHAREPOINT_ALLOWED_HOSTS", urlparse(base_url).hostname or ""),
        "OPA_URL": os.environ.get("OPA_URL", "http://localhost:8181"),
        "TEMPLATES_DIR": os.environ.get("TEMPLATES_DIR", "templates"),
        "FIXTURES_DIR": os.environ.get("FIXTURES_DIR", "fixtures"),
        "COLLECTOR_TIMEOUT": float(os.environ.get("COLLECTOR_TIMEOUT", "5")),
        "OPA_TIMEOUT": float(os.environ.get("OPA_TIMEOUT", "5")),
    }


def create_app(config: dict | None = None, collectors: dict[str, Collector] | None = None,
               opa: OpaClient | None = None) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False
    app.config.update(_default_config())
    app.config.update(config or {})
    allowed_hosts = {h.strip() for h in app.config["SHAREPOINT_ALLOWED_HOSTS"].split(",") if h.strip()}

    sharepoint = SharePointCollector(app.config["SHAREPOINT_BASE_URL"], timeout=app.config["COLLECTOR_TIMEOUT"])
    collectors = collectors or {"sharepoint": sharepoint, "fixture": FixtureCollector(app.config["FIXTURES_DIR"])}
    opa = opa or OpaClient(app.config["OPA_URL"], timeout=app.config["OPA_TIMEOUT"])
    executed_as = sharepoint.principal()

    def bad_request(message: str, status: int = 400):
        return jsonify(error=message), status

    def template_for(body: dict, evaluate: bool):
        if "control_id" in body:
            template = load_template(app.config["TEMPLATES_DIR"], str(body["control_id"]))
        elif "url" in body:
            template = debug.build_template(body, allowed_hosts, require_policy=evaluate)
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
        return run(evaluate=False)

    @app.post("/evaluate")
    def evaluate():
        return run(evaluate=True)

    @app.get("/health")
    def health():
        return jsonify(status="ok", opa="ok" if opa.healthy() else "unreachable", collectors=sorted(collectors))

    return app
