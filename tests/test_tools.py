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
        "fields": [{"name": "a", "type": "text"}],
    }
    calls = mock_fetch(body)

    result = await main.get_dataset("100113")

    assert calls == [{"endpoint": "/catalog/datasets/100113", "params": {}}]
    assert result["fields"] == [{"name": "a", "type": "text"}]
