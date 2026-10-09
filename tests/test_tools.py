import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError


@pytest.mark.anyio
async def test_upstream_error_raises_tool_error(monkeypatch):
    import main

    async def fake_get(self, endpoint, params=None):
        request = httpx.Request("GET", f"{main.BASE_URL}{endpoint}")
        response = httpx.Response(
            400,
            request=request,
            json={
                "error_code": "ODSQLError",
                "message": "ODSQL query is malformed: Unknown field: anzahl",
            },
        )
        return response

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    with pytest.raises(ToolError) as exc_info:
        await main.get_records(
            dataset_id="100059", select="jahr, sum(anzahl) as bevoelkerung", group_by="jahr"
        )

    assert "400" in str(exc_info.value)
    assert "Unknown field: anzahl" in str(exc_info.value)


@pytest.mark.anyio
async def test_search_semantic_builds_vector_similarity(mock_fetch):
    import main

    body = {"total_count": 2, "results": [{"metas": {"default": {"title": "x"}}}]}
    calls = mock_fetch(body)

    result = await main.get_datasets(search="air quality", search_mode="semantic")

    assert result["total_count"] == 2
    (call,) = calls
    assert call["endpoint"] == "/catalog/datasets"
    assert call["params"]["order_by"] == 'vector_similarity("air quality") desc'
    assert "where" not in call["params"]


@pytest.mark.anyio
async def test_search_lexical_builds_where(mock_fetch):
    import main

    calls = mock_fetch({"results": []})

    await main.get_datasets(search="luft", search_mode="lexical")

    (call,) = calls
    assert call["params"]["where"] == 'search("luft")'
    assert "order_by" not in call["params"]


@pytest.mark.anyio
async def test_search_empty_uses_plain_order_by(mock_fetch):
    import main

    calls = mock_fetch({"results": []})

    await main.get_datasets(order_by="title asc")

    (call,) = calls
    assert call["params"]["order_by"] == "title asc"
    assert "where" not in call["params"]


@pytest.mark.anyio
async def test_record_limit_is_capped(mock_fetch):
    import main

    calls = mock_fetch({"results": []})

    await main.get_records(dataset_id="100113", limit=99999)

    (call,) = calls
    assert call["params"]["limit"] == 20000


@pytest.mark.anyio
async def test_get_dataset_simplifies_fields(mock_fetch):
    import main

    body = {
        "metas": {"default": {"title": "t"}, "explore": {"records_count": 5}},
        "fields": [
            {"name": "a", "type": "text", "annotations": {"sortable": True}},
            {"name": "b", "type": "text", "annotations": {"facet": True, "disjunctive": True}},
            {"name": "c", "type": "date", "annotations": {"timerangeFilter": True}},
            {"name": "d", "type": "text"},
        ],
    }
    calls = mock_fetch(body)

    result = await main.get_dataset("100113")

    (call,) = calls
    assert call["endpoint"] == "/catalog/datasets/100113"
    assert result["fields"] == [
        {"name": "a", "type": "text", "sortable": True},
        {"name": "b", "type": "text", "facet": True, "disjunctive": True},
        {"name": "c", "type": "date", "timerange_filter": True},
        {"name": "d", "type": "text"},
    ]


@pytest.mark.anyio
async def test_upstream_error_with_non_json_body(monkeypatch):
    import main

    async def fake_get(self, endpoint, params=None):
        request = httpx.Request("GET", f"{main.BASE_URL}{endpoint}")
        return httpx.Response(502, request=request, text="<html>Bad Gateway</html>")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    with pytest.raises(ToolError) as exc_info:
        await main.get_dataset("100113")

    assert "502" in str(exc_info.value)
    assert "Bad Gateway" in str(exc_info.value)


@pytest.mark.anyio
async def test_connect_error_raises_tool_error(monkeypatch):
    import main

    async def fake_get(self, endpoint, params=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    with pytest.raises(ToolError) as exc_info:
        await main.get_dataset("100113")

    assert "Could not reach" in str(exc_info.value)


@pytest.mark.anyio
async def test_timeout_raises_tool_error(monkeypatch):
    import main

    async def fake_get(self, endpoint, params=None):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    with pytest.raises(ToolError) as exc_info:
        await main.get_dataset("100113")

    assert "timed out" in str(exc_info.value)


@pytest.mark.anyio
async def test_malformed_success_body_raises_tool_error(monkeypatch):
    import main

    async def fake_get(self, endpoint, params=None):
        request = httpx.Request("GET", f"{main.BASE_URL}{endpoint}")
        return httpx.Response(200, request=request, text="<html>not json</html>")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    with pytest.raises(ToolError) as exc_info:
        await main.get_dataset("100113")

    assert "malformed JSON" in str(exc_info.value)


@pytest.mark.anyio
async def test_unsupported_export_format_raises_tool_error():
    import main

    with pytest.raises(ToolError) as exc_info:
        await main.export_dataset_url(dataset_id="100113", format="excel")

    assert "Unsupported export format" in str(exc_info.value)
    assert "excel" in str(exc_info.value)


@pytest.mark.anyio
async def test_export_url_encodes_where():
    import main

    url = await main.export_dataset_url(dataset_id="100113", format="csv", where="jahr >= '2016'")

    assert "/exports/csv?where=" in url
    assert "jahr%20%3E%3D%20%272016%27" in url


@pytest.mark.anyio
async def test_get_facets_tolerates_unexpected_shape(mock_fetch):
    import main

    mock_fetch({"unexpected": "shape"})

    result = await main.get_facets()

    assert result == {"unexpected": "shape"}


@pytest.mark.anyio
async def test_get_facets_missing_requested_facet_returns_data(mock_fetch):
    import main

    body = {"facets": [{"name": "other", "facets": []}]}
    mock_fetch(body)

    result = await main.get_facets(facet="theme")

    assert result == body


@pytest.mark.anyio
async def test_export_url_without_where_has_no_query():
    import main

    url = await main.export_dataset_url("100113", format="geojson")

    assert url == f"{main.BASE_URL}/catalog/datasets/100113/exports/geojson"


@pytest.mark.anyio
async def test_export_url_encodes_where_clause():
    from urllib.parse import parse_qs, urlsplit

    import main

    where = 'commune="La Hulpe" and year>=2020 & x'
    url = await main.export_dataset_url("100113", format="csv", where=where)

    parts = urlsplit(url)
    assert " " not in url and '"' not in url
    assert parts.path.endswith("/catalog/datasets/100113/exports/csv")
    # round-trip: the API receives exactly the clause that was passed
    assert parse_qs(parts.query) == {"where": [where]}
