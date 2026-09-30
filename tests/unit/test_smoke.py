"""Smoke test that the API app starts and serves health; needs no services."""

from evidence_api.app import create_app


def test_health_reports_unreachable_opa():
    """Health returns 200 and reports OPA unreachable when nothing listens."""
    app = create_app({"OPA_URL": "http://127.0.0.1:9"})
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["opa"] == "unreachable"
