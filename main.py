import json
import os
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

import api_docs


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
# systemd) and falls back to a local .env next to this module, so the
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


class RelaxedAcceptHeaderMiddleware(BaseHTTPMiddleware):
    """Tolerate JSON-RPC clients that send incomplete Accept headers.

    The streamable HTTP transport rejects POST requests without an Accept
    header listing both application/json and text/event-stream (406), which
    plain JSON-RPC clients routinely omit. Rewrite those Accept headers to
    what the transport requires before the request reaches it."""

    async def dispatch(self, request, call_next):
        if request.method == "POST":
            accept = request.headers.get("accept", "")
            if "text/event-stream" not in accept or "application/json" not in accept:
                request.scope["headers"] = [
                    (k, v) for k, v in request.scope["headers"] if k.lower() != b"accept"
                ] + [(b"accept", b"application/json, text/event-stream")]
        return await call_next(request)


mcp = MCPServer(DOMAIN)

MCP_HTTP_SETTINGS = {
    "streamable_http_path": "/mcp",
    "stateless_http": True,
    "transport_security": _transport_security(),
}


async def fetch(endpoint: str, params: dict[str, str | int] | None = None) -> dict:
    """GET from the Explore API. API errors are raised as ToolError with the portal's own message
    (e.g. "Incompatible types in comparison filter ... where"), so the calling model can fix its query;
    any other exception would reach the client only as "Error executing tool <name>"."""
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
            response = await client.get(endpoint, params=params)
    except httpx.TimeoutException as exc:
        raise ToolError(f"Upstream API timed out after 30s while requesting {endpoint}.") from exc
    except httpx.HTTPError as exc:
        raise ToolError(
            f"Could not reach the upstream API while requesting {endpoint}: {exc}"
        ) from exc

    if response.status_code >= 400:
        body = None
        try:
            body = response.json()
        except ValueError:
            pass
        detail = body.get("message", "") if isinstance(body, dict) else ""
        raise ToolError(
            f"Upstream API returned {response.status_code} for {endpoint}: {detail or response.reason_phrase}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ToolError(f"Upstream API returned malformed JSON for {endpoint}: {exc}") from exc


def dataset_url(dataset_id) -> str:
    """Public page of a dataset: the URL to cite for anything taken from it."""
    return f"https://{DOMAIN}/explore/dataset/{dataset_id}/"


def _to_str(value) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value) if value else ""


LIST_DESCRIPTION_CHARS = 400
FIELD_DESCRIPTION_CHARS = 200


def _short(text: str, limit: int = LIST_DESCRIPTION_CHARS) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def _simplify_dataset(data: dict) -> dict:
    metas = data.get("metas", {})
    default = metas.get("default", {})
    explore = metas.get("explore", {})
    return {
        "dataset_id": data.get("dataset_id"),
        "title": _to_str(default.get("title")),
        "url": dataset_url(data.get("dataset_id")),
        # list results: the start of the description; get_dataset returns all of it
        "description": _short(_to_str(default.get("description"))),
        "theme": _to_str(default.get("theme")),
        "keyword": default.get("keyword", []) or [],
        "publisher": _to_str(default.get("publisher")),
        "modified": default.get("modified"),
        "language": default.get("language", []) or [],
        "records_count": explore.get("records_count"),
    }


def _escape_odsql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _next_offset(offset: int, returned: int, total) -> int | None:
    """Offset for the next page, or None when this page reached the end."""
    if not returned:
        return None
    nxt = offset + returned
    if isinstance(total, int) and nxt >= total:
        return None
    return nxt


# Dataset title/modified for results that do not carry them (records, export).
# Small in-memory cache so a records call costs at most one extra request.
META_TTL_SECONDS = 3600
_meta_cache: dict[str, tuple[float, dict]] = {}


async def _dataset_meta(dataset_id: str) -> dict:
    """{"title", "modified"} of a dataset from the catalog; {} if unavailable.
    Never raises: citing metadata must not make a data call fail."""
    key = dataset_id.strip()
    cached = _meta_cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < META_TTL_SECONDS:
        return cached[1]
    try:
        data = await fetch(
            "/catalog/datasets",
            {
                "select": "dataset_id, title, modified",
                "where": f'dataset_id="{_escape_odsql(key)}"',
                "limit": 1,
            },
        )
        row = (data.get("results") or [{}])[0]
        meta = {k: row[k] for k in ("title", "modified") if isinstance(row, dict) and row.get(k)}
        meta = {k: _to_str(v) if k == "title" else v for k, v in meta.items()}
    except (ToolError, AttributeError, IndexError, TypeError):
        return {}
    if meta:
        _meta_cache[key] = (now, meta)
    return meta


@mcp.tool(
    title="Search Datasets",
    description=(
        f"Search the {DOMAIN} dataset catalog; returns dataset_id, title, url, short description "
        "and modified per hit, plus next_offset for paging. Use search_mode 'semantic' (default) "
        "for topics and questions in any language, 'lexical' only for exact names or terms. "
        "Next step: get_dataset(dataset_id) before querying records."
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
    Args:
        limit: Hits per page (default 10, max 100).
        offset: Start index; pass next_offset from the previous result to get the next page.
        search: Query text. Empty lists the whole catalog.
        search_mode: "semantic" (ranked by meaning; total_count is then the whole catalog) or
            "lexical" (full-text filter; matches partial words, so use quoted phrases and AND/NOT).
        refine: Exact facet filter, e.g. "publisher:Statistisches Amt" (values via get_facets).
        exclude: Facet filter to exclude, same syntax as refine.
        order_by: e.g. "modified desc". Ignored in semantic mode.
        timezone: e.g. "Europe/Zurich".
        include_app_metas: Include application metadata.
    """
    params: dict[str, str | int] = {"limit": max(1, min(limit, 100)), "offset": max(0, offset)}
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
    results = [_simplify_dataset(d) for d in data.get("results", [])]
    total = data.get("total_count")
    return {
        "total_count": total,
        "next_offset": _next_offset(int(params["offset"]), len(results), total),
        "results": results,
    }


@mcp.tool(
    title="Get Dataset Metadata",
    description=(
        "Get one dataset's title, url, full description, modified date, record count and its "
        "fields (name, type, label, description, unit, sortable/facet flags). Always call this "
        "before get_records: use the exact field names listed here, and note that year fields "
        "are often type text."
    ),
)
async def get_dataset(dataset_id: str) -> dict:
    """
    Args:
        dataset_id: Dataset identifier, e.g. "100010" (from get_datasets).

    Field flags: `sortable` = usable in order_by, `facet` = usable in refine/exclude;
    a missing flag means not supported.
    """
    data = await fetch(f"/catalog/datasets/{quote(dataset_id.strip(), safe='')}")
    metas = data.get("metas", {})
    annotations: list[dict] = []
    for f in data.get("fields", []):
        caps = f.get("annotations", {}) or {}
        entry: dict = {"name": f.get("name"), "type": f.get("type")}
        if f.get("label") and f.get("label") != f.get("name"):
            entry["label"] = f["label"]
        if f.get("description"):
            entry["description"] = _short(_to_str(f["description"]), FIELD_DESCRIPTION_CHARS)
        if caps.get("unit"):
            entry["unit"] = _to_str(caps["unit"])
        if caps.get("sortable"):
            entry["sortable"] = True
        if caps.get("facet"):
            entry["facet"] = True
        if caps.get("disjunctive"):
            entry["disjunctive"] = True
        if caps.get("timerangeFilter"):
            entry["timerange_filter"] = True
        annotations.append(entry)
    return {
        "dataset_id": data.get("dataset_id"),
        "title": metas.get("default", {}).get("title"),
        "url": dataset_url(data.get("dataset_id")),
        "description": metas.get("default", {}).get("description"),
        "theme": metas.get("default", {}).get("theme"),
        "keyword": metas.get("default", {}).get("keyword", []),
        "publisher": metas.get("default", {}).get("publisher"),
        "modified": metas.get("default", {}).get("modified"),
        "language": metas.get("default", {}).get("language", []),
        "records_count": data.get("metas", {}).get("explore", {}).get("records_count"),
        "fields": annotations,
    }


MAX_RESULT_CHARS = 12000


def _cap_rows(rows: list, budget: int) -> tuple[list, bool]:
    """Longest prefix of rows whose JSON stays within budget characters (at least one row)."""
    kept: list = []
    used = 2
    for row in rows:
        size = len(json.dumps(row, ensure_ascii=False, default=str)) + 2
        if kept and used + size > budget:
            return kept, True
        kept.append(row)
        used += size
    return kept, False


@mcp.tool(
    title="Query Dataset Records",
    description=(
        "Query rows of a dataset with ODSQL; returns dataset_id, url, title, modified (cite these), "
        "total_count, next_offset and results. Call get_dataset first for exact field names. "
        'Year fields are often text: use group_by/order_by or `in ("2020","2021")`, not >=. '
        "For totals use select with sum()/count() plus group_by instead of fetching many rows; "
        "output is capped at about 12000 characters (truncated=true)."
    ),
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
    Args:
        dataset_id: Dataset identifier, e.g. "100010".
        select: Fields or expressions, e.g. "jahr, sum(total) as leer" or "*".
        where: ODSQL filter, e.g. `jahr in ("2022","2023")`, `name like "Basel%"`,
            `search("Hitze")`. Combine with AND/OR/NOT. Strings in double quotes.
        group_by: Grouping for aggregations, e.g. "jahr". Needs aggregates in select.
        order_by: e.g. "jahr desc".
        limit: Rows per page (default 10, max 100; up to 20000 with group_by).
        offset: Start row; use next_offset from the previous result to page.
        refine: Exact facet filter, e.g. "wohnviertel:Altstadt".
        exclude: Facet filter to exclude.
        lang: Formatting language: "de", "en", "fr".
        timezone: e.g. "Europe/Zurich".
        include_links: Include HATEOAS links.
    """
    params: dict[str, str | int] = {
        "limit": max(1, min(limit, 20000)),
        "offset": max(0, offset),
    }
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

    data = await fetch(f"/catalog/datasets/{quote(dataset_id.strip(), safe='')}/records", params)
    meta = await _dataset_meta(dataset_id)
    head: dict = {"dataset_id": dataset_id.strip(), "url": dataset_url(dataset_id.strip())}
    head.update(meta)
    rows = data.get("results")
    if not isinstance(rows, list):
        return {**head, **data}
    rest = {k: v for k, v in data.items() if k != "results"}
    head.update(rest)
    overhead = len(json.dumps(head, ensure_ascii=False, default=str)) + 200
    kept, truncated = _cap_rows(rows, max(MAX_RESULT_CHARS - overhead, 1000))
    total = data.get("total_count")
    head["next_offset"] = _next_offset(int(params["offset"]), len(kept), total)
    if head["next_offset"] is None and len(kept) == len(rows) == params["limit"] and total is None:
        head["next_offset"] = int(params["offset"]) + len(kept)
    head["results"] = kept
    if truncated:
        head["truncated"] = True
        head["hint"] = (
            f"Output cut to {len(kept)} of {len(rows)} rows (about {MAX_RESULT_CHARS} characters). "
            "Use select to return fewer fields, group_by with aggregates, a smaller limit, "
            "or next_offset to page."
        )
    return head


FACET_VALUES_ALL = 30
FACET_VALUES_ONE = 100


@mcp.tool(
    title="Get Facet Values",
    description=(
        "List catalog filter values (publisher, theme, keyword, features, modified, language) "
        "with counts, to build an exact `refine` filter for get_datasets. Pass one facet name; "
        "without it every facet is returned, capped at 30 values each."
    ),
)
async def get_facets(facet: str | None = None) -> dict:
    """
    Args:
        facet: "publisher", "keyword", "theme", "features", "modified" or "language".
            Omit for a capped overview of all facets.
    """
    params: dict[str, str | int] = {}
    if facet:
        params["facet"] = facet
    data = await fetch("/catalog/facets", params)
    facets = data.get("facets") if isinstance(data, dict) else None
    if not isinstance(facets, list):
        return data

    def capped(values, cap: int) -> dict:
        values = values if isinstance(values, list) else []
        out: dict = {"total_values": len(values), "values": values[:cap]}
        if len(values) > cap:
            out["truncated"] = True
        return out

    if facet:
        for f in facets:
            if isinstance(f, dict) and f.get("name") == facet:
                return {"facet": facet, **capped(f.get("facets", []), FACET_VALUES_ONE)}
        return data
    return {
        "facets": [
            {"name": f.get("name"), **capped(f.get("facets", []), FACET_VALUES_ALL)}
            for f in facets
            if isinstance(f, dict)
        ],
        "hint": f"Max {FACET_VALUES_ALL} values per facet; pass facet=<name> for up to {FACET_VALUES_ONE}.",
    }


EXPORT_FORMATS = (
    "csv",
    "json",
    "geojson",
    "xlsx",
    "tsv",
    "ods",
    "shp",
    "parquet",
    "gpx",
    "kml",
    "rdfxml",
    "jsonld",
    "turtle",
)


@mcp.tool(
    title="Get Export URL",
    description=(
        "Build a download link for a whole dataset (csv, json, geojson, xlsx, parquet, ...), "
        "optionally filtered by an ODSQL where. Returns download_url plus the dataset page url "
        "and title to cite. Does not return data itself; use get_records to read values."
    ),
)
async def export_dataset_url(
    dataset_id: str,
    format: str = "json",
    where: str | None = None,
) -> dict:
    """
    Args:
        dataset_id: Dataset identifier, e.g. "100113".
        format: csv, json, geojson, xlsx, tsv, ods, shp, parquet, gpx, kml, rdfxml, jsonld, turtle.
        where: Optional ODSQL filter for the exported records.

    Returns:
        {"download_url", "format", "dataset_id", "url", "title"?, "modified"?}.
        (Before this version the tool returned the download URL as a plain string.)
    """
    normalized_format = format.strip().lower()
    if normalized_format not in EXPORT_FORMATS:
        raise ToolError(
            f"Unsupported export format {format!r}. Supported: {', '.join(EXPORT_FORMATS)}"
        )
    url = f"{BASE_URL}/catalog/datasets/{quote(dataset_id.strip(), safe='')}/exports/{normalized_format}"
    if where:
        # ODSQL clauses routinely contain spaces, quotes, "=" and "&" (e.g.
        # commune="La Hulpe" and year>=2020): percent-encode so the URL is valid
        # and the clause reaches the API intact.
        url += f"?where={quote(where, safe='')}"
    meta = await _dataset_meta(dataset_id)
    return {
        "download_url": url,
        "format": normalized_format,
        "dataset_id": dataset_id.strip(),
        "url": dataset_url(dataset_id.strip()),
        **meta,
    }


def _healthz(request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _index(request) -> JSONResponse:
    return JSONResponse(
        {
            "server": DOMAIN,
            "status": "ok",
            "transport": "mcp-streamable-http",
            "endpoints": {
                "mcp": "/mcp",
                "health": "/healthz",
                "openapi": "/openapi.json",
                "docs": "/docs",
            },
        }
    )


def _openapi(request) -> JSONResponse:
    return JSONResponse(api_docs.build_openapi(DOMAIN, mcp._tool_manager.list_tools()))


def _docs(request) -> HTMLResponse:
    return HTMLResponse(api_docs.docs_page(DOMAIN))


def create_http_app():
    """Build a fresh ASGI app. Call once per process; tests create one per case
    because the underlying session manager may run only once per instance."""
    app = mcp.streamable_http_app(**MCP_HTTP_SETTINGS)
    app.add_middleware(RelaxedAcceptHeaderMiddleware)
    app.router.routes.insert(0, Route("/", _index))
    app.router.routes.insert(0, Route("/healthz", _healthz))
    app.router.routes.insert(0, Route("/openapi.json", _openapi))
    app.router.routes.insert(0, Route("/docs", _docs))
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
