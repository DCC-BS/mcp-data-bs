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

    call = calls[0]
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
async def test_export_url_encodes_where(mock_fetch):
    import main

    mock_fetch({"results": []})
    url = (
        await main.export_dataset_url(dataset_id="100113", format="csv", where="jahr >= '2016'")
    )["download_url"]

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
async def test_export_url_without_where_has_no_query(mock_fetch):
    import main

    mock_fetch({"results": []})
    result = await main.export_dataset_url("100113", format="geojson")

    assert result["download_url"] == f"{main.BASE_URL}/catalog/datasets/100113/exports/geojson"
    assert result["url"] == "https://data.bs.ch/explore/dataset/100113/"


@pytest.mark.anyio
async def test_export_url_encodes_where_clause(mock_fetch):
    from urllib.parse import parse_qs, urlsplit

    import main

    mock_fetch({"results": []})
    where = 'commune="La Hulpe" and year>=2020 & x'
    url = (await main.export_dataset_url("100113", format="csv", where=where))["download_url"]

    parts = urlsplit(url)
    assert " " not in url and '"' not in url
    assert parts.path.endswith("/catalog/datasets/100113/exports/csv")
    # round-trip: the API receives exactly the clause that was passed
    assert parse_qs(parts.query) == {"where": [where]}


def _router(monkeypatch, routes):
    """fetch stub that answers by endpoint; a value that is an Exception is raised."""
    calls = []

    async def fake_fetch(endpoint, params=None):
        calls.append({"endpoint": endpoint, "params": params or {}})
        body = routes[endpoint]
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr("main.fetch", fake_fetch)
    return calls


META = {
    "results": [
        {"dataset_id": "100010", "title": "Leerstehende Wohnungen", "modified": "2026-08-21"}
    ]
}


@pytest.mark.anyio
async def test_get_records_is_citable(monkeypatch):
    import main

    calls = _router(
        monkeypatch,
        {
            "/catalog/datasets/100010/records": {"total_count": 1, "results": [{"jahr": "2024"}]},
            "/catalog/datasets": META,
        },
    )

    result = await main.get_records("100010")

    assert result["dataset_id"] == "100010"
    assert result["url"] == "https://data.bs.ch/explore/dataset/100010/"
    assert result["title"] == "Leerstehende Wohnungen"
    assert result["modified"] == "2026-08-21"
    assert result["results"] == [{"jahr": "2024"}]
    assert result["next_offset"] is None
    meta_call = calls[1]
    assert meta_call["params"]["where"] == 'dataset_id="100010"'


@pytest.mark.anyio
async def test_metadata_is_cached(monkeypatch):
    import main

    calls = _router(
        monkeypatch,
        {
            "/catalog/datasets/100010/records": {"total_count": 0, "results": []},
            "/catalog/datasets": META,
        },
    )

    await main.get_records("100010")
    await main.get_records("100010")

    assert [c["endpoint"] for c in calls].count("/catalog/datasets") == 1


@pytest.mark.anyio
async def test_metadata_failure_does_not_fail_records(monkeypatch):
    import main

    _router(
        monkeypatch,
        {
            "/catalog/datasets/100010/records": {"total_count": 1, "results": [{"a": 1}]},
            "/catalog/datasets": ToolError("boom"),
        },
    )

    result = await main.get_records("100010")

    assert result["results"] == [{"a": 1}]
    assert result["url"].endswith("/100010/")
    assert "title" not in result and "modified" not in result


@pytest.mark.anyio
async def test_records_error_still_raises(monkeypatch):
    import main

    _router(
        monkeypatch,
        {"/catalog/datasets/1/records": ToolError("Unknown field: x"), "/catalog/datasets": META},
    )

    with pytest.raises(ToolError, match="Unknown field"):
        await main.get_records("1")


@pytest.mark.anyio
async def test_records_paging_next_offset(mock_fetch):
    import main

    mock_fetch({"total_count": 25, "results": [{"a": i} for i in range(10)]})

    assert (await main.get_records("x", limit=10, offset=0))["next_offset"] == 10
    assert (await main.get_records("x", limit=10, offset=10))["next_offset"] == 20


@pytest.mark.anyio
async def test_records_last_page_has_no_next_offset(mock_fetch):
    import main

    mock_fetch({"total_count": 12, "results": [{"a": 1}, {"a": 2}]})

    assert (await main.get_records("x", limit=10, offset=10))["next_offset"] is None


@pytest.mark.anyio
async def test_records_are_capped(mock_fetch):
    import json

    import main

    rows = [{"text": "x" * 1000} for _ in range(50)]
    mock_fetch({"total_count": 50, "results": rows})

    result = await main.get_records("x", limit=50)

    assert result["truncated"] is True
    assert "select" in result["hint"] and "group_by" in result["hint"]
    assert 0 < len(result["results"]) < 50
    assert result["next_offset"] == len(result["results"])
    assert len(json.dumps(result, ensure_ascii=False)) < 12500


@pytest.mark.anyio
async def test_small_records_not_truncated(mock_fetch):
    import main

    mock_fetch({"total_count": 1, "results": [{"a": 1}]})

    assert "truncated" not in await main.get_records("x")


@pytest.mark.anyio
async def test_get_datasets_next_offset(mock_fetch):
    import main

    mock_fetch(
        {"total_count": 25, "results": [{"dataset_id": str(i), "metas": {}} for i in range(10)]}
    )

    assert (await main.get_datasets(limit=10, offset=10))["next_offset"] == 20
    assert (await main.get_datasets(limit=10, offset=20))["next_offset"] is None
    mock_fetch({"total_count": 3, "results": [{"dataset_id": "1", "metas": {}}]})
    assert (await main.get_datasets(limit=10, offset=2))["next_offset"] is None


@pytest.mark.anyio
async def test_get_dataset_fields_have_label_description_unit(mock_fetch):
    import main

    long = "w " * 200
    mock_fetch(
        {
            "dataset_id": "1",
            "metas": {"default": {"title": "t", "modified": "2026-01-01"}},
            "fields": [
                {
                    "name": "pm25",
                    "type": "double",
                    "label": "Feinstaub PM2.5",
                    "description": long,
                    "annotations": {"unit": "μg/m3"},
                },
                {"name": "x", "type": "text", "label": "x", "description": None, "annotations": {}},
            ],
        }
    )

    result = await main.get_dataset("1")

    a, b = result["fields"]
    assert a["label"] == "Feinstaub PM2.5" and a["unit"] == "μg/m3"
    assert len(a["description"]) <= 205
    assert b == {"name": "x", "type": "text"}
    assert result["url"].endswith("/explore/dataset/1/")
    assert result["modified"] == "2026-01-01"


@pytest.mark.anyio
async def test_get_facets_caps_values_without_name(mock_fetch):
    import main

    mock_fetch(
        {
            "facets": [
                {"name": "keyword", "facets": [{"name": str(i), "count": 1} for i in range(100)]},
                {"name": "theme", "facets": [{"name": "a", "count": 1}]},
            ]
        }
    )

    result = await main.get_facets()

    kw, th = result["facets"]
    assert kw["total_values"] == 100 and len(kw["values"]) == 30 and kw["truncated"] is True
    assert th["total_values"] == 1 and "truncated" not in th


@pytest.mark.anyio
async def test_get_facets_named_reports_total(mock_fetch):
    import main

    mock_fetch({"facets": [{"name": "theme", "facets": [{"name": "a", "count": 1}]}]})

    assert await main.get_facets(facet="theme") == {
        "facet": "theme",
        "total_values": 1,
        "values": [{"name": "a", "count": 1}],
    }


@pytest.mark.anyio
async def test_export_returns_citation(monkeypatch):
    import main

    _router(monkeypatch, {"/catalog/datasets": META})

    result = await main.export_dataset_url("100010", format="CSV")

    assert result["format"] == "csv"
    assert result["download_url"].endswith("/100010/exports/csv")
    assert result["title"] == "Leerstehende Wohnungen"
    assert result["url"] == "https://data.bs.ch/explore/dataset/100010/"


def test_tool_descriptions_are_short():
    import main

    for tool in main.mcp._tool_manager.list_tools():
        assert len(tool.description) < 600, tool.name
