import pytest


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
async def test_results_carry_dataset_url(mock_fetch):
    import main

    mock_fetch(
        {
            "total_count": 1,
            "results": [
                {
                    "dataset_id": "100010",
                    "metas": {"default": {"title": "x", "description": "d " * 400}},
                }
            ],
        }
    )
    found = await main.get_datasets(search="leer")
    assert found["results"][0]["url"] == f"https://{main.DOMAIN}/explore/dataset/100010/"
    assert len(found["results"][0]["description"]) <= main.LIST_DESCRIPTION_CHARS + 2

    mock_fetch({"total_count": 0, "results": []})
    records = await main.get_records(dataset_id="100010")
    assert records["url"].endswith("/explore/dataset/100010/") and records["dataset_id"] == "100010"


@pytest.mark.anyio
async def test_api_error_message_reaches_the_model(monkeypatch):
    import httpx
    from mcp.server.mcpserver.exceptions import ToolError

    import main

    def handler(request):
        return httpx.Response(
            400,
            json={
                "error_code": "IncompatibleTypesInComparisonFilter",
                "message": "Incompatible types in comparison filter.",
            },
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        main.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    with pytest.raises(ToolError, match="IncompatibleTypesInComparisonFilter"):
        await main.get_records(dataset_id="100010", where="jahr >= '2016'")
