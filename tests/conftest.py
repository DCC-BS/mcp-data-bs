import pytest


@pytest.fixture
def mock_fetch(monkeypatch):
    """Replace main.fetch with a stub that records params and returns a canned body."""

    def _install(body):
        calls = []

        async def fake_fetch(endpoint, params=None):
            calls.append({"endpoint": endpoint, "params": params or {}})
            return body

        monkeypatch.setattr("main.fetch", fake_fetch)
        return calls

    return _install
