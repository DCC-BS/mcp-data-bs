from starlette.testclient import TestClient


def _app():
    import main

    return main.create_http_app()


def test_healthz_ok():
    with TestClient(_app()) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_routes_present():
    app = _app()
    paths = [r.path for r in app.router.routes]
    assert "/healthz" in paths
    assert "/" in paths
