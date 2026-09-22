import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# The catalog domain comes from the environment first (container, compose,
# systemd) and falls back to the committed .env next to this module, so the
# same checkout works locally and in a container.
def _resolve_domain() -> str:
    value = os.environ.get("DATA_PORTAL_DOMAIN", "").strip()
    if value:
        return value
    _env_path = Path(__file__).parent / ".env"
    if _env_path.is_file():
        return _read_env_file(_env_path).get("DATA_PORTAL_DOMAIN", "").strip()
    return ""


DOMAIN = _resolve_domain()

if not DOMAIN:
    raise RuntimeError(
        "DATA_PORTAL_DOMAIN is not set in the environment or the .env file next to main.py"
    )

DOMAIN = DOMAIN.removeprefix("https://").removeprefix("http://").strip("/")
BASE_URL = f"https://{DOMAIN}/api/explore/v2.1"


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection for the HTTP endpoint. FastMCP auto-enables
    Host-header validation limited to localhost when no host is given, which
    would reject real deployment hosts with 421. Configure the allowed hosts
    via MCP_ALLOWED_HOSTS (comma-separated, e.g. "mcp.bs.ch:*"); when unset,
    protection is disabled so the server is reachable on any Host header."""
    allowed = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if not allowed:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed,
    )


mcp = FastMCP(
    DOMAIN,
    stateless_http=True,
    streamable_http_path="/mcp",
    transport_security=_transport_security(),
)


async def fetch(endpoint: str, params: dict[str, str | int] | None = None) -> dict:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        response = await client.get(endpoint, params=params)
        response.raise_for_status()
        return response.json()


def _to_str(value) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value) if value else ""


def _simplify_dataset(data: dict) -> dict:
    metas = data.get("metas", {})
    default = metas.get("default", {})
    explore = metas.get("explore", {})
    return {
        "dataset_id": data.get("dataset_id"),
        "title": _to_str(default.get("title")),
        "description": _to_str(default.get("description")),
        "theme": _to_str(default.get("theme")),
        "keyword": default.get("keyword", []) or [],
        "publisher": _to_str(default.get("publisher")),
        "modified": default.get("modified"),
        "language": default.get("language", []) or [],
        "records_count": explore.get("records_count"),
    }


def _escape_odsql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


@mcp.tool(
    title="Search Datasets",
    description=(
        f"Search and list available open datasets from {DOMAIN}. Two modes: 'semantic' "
        "(default) ranks the catalog by meaning using natural-language queries "
        "(handles synonyms and other languages); 'lexical' does a classic full-text "
        "match on the exact terms. Use semantic for conceptual discovery, lexical for "
        "precise term/name lookups."
    ),
)
async def get_datasets(
    limit: int = 10,
    offset: int = 0,
    search: str | None = None,
    search_mode: str = "semantic",
    refine: str | None = None,
    exclude: str | None = None,
    order_by: str | None = None,
    timezone: str | None = None,
    include_app_metas: bool = False,
) -> dict:
    """
    List available datasets from the configured catalog with optional filtering.

    Args:
        limit: Number of items to return (default: 10, max: 100)
        offset: Index of first item to return (default: 0)
        search: Search string. Interpreted according to search_mode.
        search_mode: "semantic" (default) ranks the whole catalog by meaning via
            vector_similarity (best for natural-language/conceptual queries, also
            matches synonyms and other languages); "lexical" filters by exact
            full-text match. Ignored when search is empty.
        refine: Facet filter to limit results (e.g., "publisher:Statistisches Amt")
        exclude: Facet filter to exclude values (e.g., "modified:2019/12")
        order_by: Field to sort results (e.g., "modified desc", "title asc").
            Ignored in semantic mode, where results are ordered by relevance.
        timezone: Timezone for datetime fields (e.g., "Europe/Zurich")
        include_app_metas: Include application metadata in response

    Returns:
        Dictionary with total_count and results array containing dataset metadata.
        Note: in semantic mode the catalog is ranked rather than filtered, so
        total_count reflects the whole catalog and the top results are the most
        relevant.
    """
    params: dict[str, str | int] = {"limit": min(limit, 100), "offset": offset}
    if refine:
        params["refine"] = refine
    if exclude:
        params["exclude"] = exclude
    if timezone:
        params["timezone"] = timezone
    if include_app_metas:
        params["include_app_metas"] = "true"
    normalized_search = " ".join(search.split()) if search else ""
    if normalized_search:
        query = _escape_odsql(normalized_search)
        if search_mode == "lexical":
            params["where"] = f'search("{query}")'
            if order_by:
                params["order_by"] = order_by
        else:
            params["order_by"] = f'vector_similarity("{query}") desc'
    elif order_by:
        params["order_by"] = order_by
    data = await fetch("/catalog/datasets", params)
    return {
        "total_count": data.get("total_count"),
        "results": [_simplify_dataset(d) for d in data.get("results", [])],
    }


@mcp.tool(
    title="Get Dataset Metadata",
    description="Get detailed metadata for a specific dataset including field definitions, schema, publisher info, and record count. Use this to understand a dataset's structure before querying records.",
)
async def get_dataset(dataset_id: str) -> dict:
    """
    Get detailed metadata for a specific dataset.

    Args:
        dataset_id: The dataset identifier (e.g., "100113")

    Returns:
        Dataset metadata including title, description, theme, keywords, etc.
    """
    data = await fetch(f"/catalog/datasets/{dataset_id}")
    metas = data.get("metas", {})
    return {
        "dataset_id": data.get("dataset_id"),
        "title": metas.get("default", {}).get("title"),
        "description": metas.get("default", {}).get("description"),
        "theme": metas.get("default", {}).get("theme"),
        "keyword": metas.get("default", {}).get("keyword", []),
        "publisher": metas.get("default", {}).get("publisher"),
        "modified": metas.get("default", {}).get("modified"),
        "language": metas.get("default", {}).get("language", []),
        "records_count": data.get("metas", {}).get("explore", {}).get("records_count"),
        "fields": [{"name": f.get("name"), "type": f.get("type")} for f in data.get("fields", [])],
    }


@mcp.tool(
    title="Query Dataset Records",
    description="Query and filter records from a dataset using ODSQL syntax. Use this to retrieve actual data from a dataset with optional WHERE clauses, ordering, and pagination.",
)
async def get_records(
    dataset_id: str,
    select: str | None = None,
    where: str | None = None,
    group_by: str | None = None,
    order_by: str | None = None,
    limit: int = 10,
    offset: int = 0,
    refine: str | None = None,
    exclude: str | None = None,
    lang: str | None = None,
    timezone: str | None = None,
    include_links: bool = False,
) -> dict:
    """
    Query records from a dataset with ODSQL filtering.

    Args:
        dataset_id: The dataset identifier (e.g., "100113")
        select: Select expression to add/remove/change fields (e.g., "size", "size * 2 as bigger_size", "*")
        where: ODSQL WHERE clause (e.g., "pm25 > 10", "time >= '2020-01-01'")
        group_by: Grouping expression for aggregations (e.g., "city_field as city")
        order_by: Field to order results by (e.g., "time DESC", "pm25 ASC")
        limit: Number of items to return (default: 10, max: 100, or 20000 with group_by)
        offset: Index of first item to return (default: 0)
        refine: Facet filter to limit results (e.g., "city:Paris")
        exclude: Facet filter to exclude values (e.g., "modified:2019/12")
        lang: Language for formatting (e.g., "en", "de", "fr")
        timezone: Timezone for datetime fields (e.g., "Europe/Zurich")
        include_links: Include HATEOAS links in response

    Returns:
        Dictionary with total_count and results array containing record data
    """
    params: dict[str, str | int] = {"limit": min(limit, 20000), "offset": offset}
    if select:
        params["select"] = select
    if where:
        params["where"] = where
    if group_by:
        params["group_by"] = group_by
    if order_by:
        params["order_by"] = order_by
    if refine:
        params["refine"] = refine
    if exclude:
        params["exclude"] = exclude
    if lang:
        params["lang"] = lang
    if timezone:
        params["timezone"] = timezone
    if include_links:
        params["include_links"] = "true"
    return await fetch(f"/catalog/datasets/{dataset_id}/records", params)


@mcp.tool(
    title="Get Facet Values",
    description="Get available filter values for categorizing datasets. Useful for discovering publishers, keywords, themes, or other facets to refine dataset searches.",
)
async def get_facets(facet: str | None = None) -> dict:
    """
    Get available facet values for filtering datasets.

    Args:
        facet: Specific facet to retrieve: "publisher", "keyword", "theme", "features", "modified", "language"
               If None, returns all facets

    Returns:
        Dictionary with facet name and array of values with counts
    """
    params: dict[str, str | int] = {}
    if facet:
        params["facet"] = facet
    data = await fetch("/catalog/facets", params)
    if facet and "facets" in data:
        for f in data["facets"]:
            if f["name"] == facet:
                return {"facet": facet, "values": f.get("facets", [])}
    return data


@mcp.tool(
    title="Get Export URL",
    description="Generate a download URL for exporting a dataset in various formats (CSV, JSON, GeoJSON, XLSX, Shapefile, Parquet, etc.). Use this when you need to download or share dataset exports.",
)
async def export_dataset_url(
    dataset_id: str,
    format: str = "json",
    where: str | None = None,
) -> str:
    """
    Get the export URL for downloading a dataset in various formats.

    Args:
        dataset_id: The dataset identifier (e.g., "100113")
        format: Export format: csv, json, geojson, xlsx, shp, parquet, gpx, kml, rdfxml, jsonld, turtle
        where: Optional ODSQL WHERE clause to filter exported records

    Returns:
        Full URL to download the exported dataset
    """
    url = f"{BASE_URL}/catalog/datasets/{dataset_id}/exports/{format}"
    if where:
        url += f"?where={where}"
    return url


def _healthz(request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_http_app():
    """Build a fresh ASGI app. Call once per process; tests create one per case
    because the underlying session manager may run only once per instance."""
    app = mcp.streamable_http_app()
    app.router.routes.insert(0, Route("/healthz", _healthz))
    return app


http_app = create_http_app()


def main():
    mcp.run(transport="stdio")


def main_http():
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(http_app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
