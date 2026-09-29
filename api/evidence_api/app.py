import os

import requests
from flask import Flask, jsonify


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SHAREPOINT_BASE_URL=os.environ.get("SHAREPOINT_BASE_URL", "http://localhost:8000"),
        OPA_URL=os.environ.get("OPA_URL", "http://localhost:8181"),
    )
    app.config.update(config or {})

    @app.get("/health")
    def health():
        try:
            requests.get(f"{app.config['OPA_URL']}/health", timeout=2).raise_for_status()
            opa = "ok"
        except requests.RequestException:
            opa = "unreachable"
        return jsonify(status="ok", opa=opa, collectors=[])

    return app
