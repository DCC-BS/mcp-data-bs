import json

from starlette.testclient import TestClient


def _app():
    import main

    main.mcp._lowlevel_server._session_manager = None
    return main.create_http_app()


def test_healthz_ok():
    with TestClient(_app()) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_info_page():
    with TestClient(_app()) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["transport"] == "mcp-streamable-http"
    assert body["endpoints"]["mcp"] == "/mcp"


def test_post_with_relaxed_accept_header():
    with TestClient(_app()) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json"},
        )
    assert resp.status_code == 200
    frame = next(line[6:] for line in resp.text.splitlines() if line.startswith("data: "))
    assert "tools" in json.loads(frame)["result"]


def test_post_with_star_accept_header():
    with TestClient(_app()) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "*/*"},
        )
    assert resp.status_code == 200


def test_routes_present():
    app = _app()
    paths = [r.path for r in app.router.routes]
    assert "/healthz" in paths
    assert "/" in paths
    assert "/mcp" in paths
    assert "/openapi.json" in paths
    assert "/docs" in paths


def test_openapi_lists_all_tools():
    with TestClient(_app()) as client:
        resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["openapi"].startswith("3.1")
    body = spec["paths"]["/mcp"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    one_of = [ref["$ref"] for ref in body["oneOf"]]
    for request_name in (
        "GetDatasetsRequest",
        "GetDatasetRequest",
        "GetRecordsRequest",
        "GetFacetsRequest",
        "ExportDatasetUrlRequest",
        "ToolsListRequest",
    ):
        assert f"#/components/schemas/{request_name}" in one_of
    assert "/healthz" in spec["paths"]
    assert "/" in spec["paths"]


def test_openapi_request_schema_matches_tool():
    with TestClient(_app()) as client:
        spec = client.get("/openapi.json").json()
    schema = spec["components"]["schemas"]["GetDatasetRequest"]
    args = schema["properties"]["params"]["properties"]["arguments"]
    assert args == {
        "properties": {"dataset_id": {"title": "Dataset Id", "type": "string"}},
        "required": ["dataset_id"],
        "type": "object",
        "additionalProperties": False,
    }
    assert schema["example"]["method"] == "tools/call"


def test_docs_page_loads_spec():
    with TestClient(_app()) as client:
        resp = client.get("/docs")
    assert resp.status_code == 200
    assert "swagger-ui" in resp.text
    assert "text/html" in resp.headers["content-type"]
