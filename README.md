# data-bs-mcp

MCP server for any Huwise/Opendatasoft data portal — query open datasets from
data.bs.ch (and other portals on the same platform) through the Explore 2.1 API.

It runs in two modes:

- **stdio** — for local MCP clients (opencode, Cursor, Claude Desktop).
- **streamable HTTP** — hosted as a container, so it can be wired into ChatGPT
  connectors and OpenWebUI. Also ships a **skill** as a no-server alternative.

## Installation

```bash
uv sync
```

Requires [mise](https://mise.jdx.dev/) for the pinned toolchain and task runner
(tools = uv only; Python version comes from `pyproject.toml`).

```bash
mise run install   # alias: i — uv sync --locked
```

## Local usage

```bash
mise run dev            # stdio (alias: d)
mise run check          # format + lint + lockfile (alias: c)
mise run test:unit      # pytest (alias: t)
mise run dev:http       # streamable HTTP on :8000 for local testing (alias: dh)
```

## Selecting a catalog

The catalog is chosen by whoever runs the server. Configuration is read from the
`DATA_PORTAL_DOMAIN` environment variable first, else a local `.env` file next to
`main.py`. Copy the example and fill it in:

```
cp .env.example .env
```

```bash
# .env
DATA_PORTAL_DOMAIN=data.bs.ch
MCP_ALLOWED_HOSTS=mcp.bs.ch:*   # optional, see Hosting
```

The API base URL is built as `https://<domain>/api/explore/v2.1`. All
Huwise/Opendatasoft portals share this path, so changing the domain targets a
different portal.

## Hosting (streamable HTTP)

Deploy as a container. The image is built against the DCC shared base image
(`ghcr.io/dcc-bs/dcc-docker-images/mise:13-slim`); see `Dockerfile`.

```bash
docker build -f Dockerfile -t mcp-data-bs .
docker run --rm -p 8000:8000 -e DATA_PORTAL_DOMAIN=data.bs.ch mcp-data-bs
```

Healthcheck: `GET /healthz -> {"status":"ok"}`.

> **Remote hosting requires `MCP_ALLOWED_HOSTS`.** The MCP HTTP endpoint has
> DNS-rebinding protection on by default and, without config, accepts only
> localhost `Host` headers — a remote deploy would get `421 Invalid Host header`.
> When the server is reachable via a public hostname, list it (comma-separated,
> port-wildcard allowed): `MCP_ALLOWED_HOSTS="mcp.bs.ch:*"`. When unset,
> protection is disabled so any `Host` header is accepted.

### Docker Compose

`compose.yml` pulls the published GHCR image, sets the domain and allowed
hostname(s), and healthchecks: `docker compose up -d`.

### Publishing (CI)

This repo uses the DCC reusable workflows ([ci-workflows](https://github.com/DCC-BS/ci-workflows)):

- `.github/workflows/ci.yml` runs `mise run check` and `mise run test:unit`
  automatically on push/PR (tasks absent from `mise.toml` are skipped).
- `.github/workflows/publish.yml` (manual `workflow_dispatch`) builds and pushes
  to GHCR using `publish-docker.yml@v2`. Bump the version in `pyproject.toml`,
  then dispatch to tag the image `<version>` + `latest`.

## Connecting clients

### ChatGPT (developer-mode connector)

Add a custom connector pointing at the hosted HTTP URL (e.g.
`https://mcp.your-domain/mcp`), no auth. All five tools are exposed and usable.

### OpenWebUI

Recent OpenWebUI versions support MCP over streamable HTTP natively:
Settings → Tools → add the hosted URL (e.g. `https://mcp.your-domain/mcp`).

### Local stdio clients

- **opencode**: add to OpenCode config:
  ```json
  {
    "mcpServers": {
      "data-bs": {
        "command": "uv",
        "args": ["--directory", "/ABSOLUTE/PATH/TO/data-bs-mcp", "run", "main.py"]
      }
    }
  }
  ```
- **uvx** (anywhere): set `DATA_PORTAL_DOMAIN` in your environment first (the
  `.env` is no longer committed). `uvx` runs the same code as a local checkout.
  ```bash
  uvx --from git+https://github.com/DCC-BS/mcp-data-bs data-bs-mcp
  ```

## Skills (no MCP needed)

Prefer not to run a server? Install the agent skill instead — it teaches an
agent to hit the public REST API directly with plain HTTP (curl/httpx). See
`skills/data-bs/SKILL.md`; copy or symlink it into your agent's skills directory
(e.g. `~/.agents/skills/data-bs`).

## Tools

### `get_datasets`
Search and list available datasets.

Two search modes:
- `semantic` (default): ranks the catalog by meaning using the `vector_similarity` explore endpoint from Huwise. Best for natural-language / conceptual queries. Matches synonyms and other languages.
- `lexical`: classic full-text match on the exact terms.

```
# semantic (default) — natural language, ranked by relevance
get_datasets(search="air quality measurements")

# lexical — exact full-text match
get_datasets(search="luft", search_mode="lexical")

# combine with facet filters
get_datasets(search="bevölkerung", refine="publisher:Statistisches Amt")
```

### `get_dataset`
Get detailed metadata for a specific dataset (fields, schema, publisher).

```
get_dataset(dataset_id="100113")
```

### `get_records`
Query records from a dataset with ODSQL filtering.

```
get_records(dataset_id="100113", where="pm25 > 10", limit=100, order_by="time DESC")
```

### `get_facets`
Get available facet values for filtering.

```
get_facets(facet="publisher")  # Options: publisher, keyword, theme, features, modified, language
```

### `export_dataset_url`
Get download URL for dataset export.

```
export_dataset_url(dataset_id="100113", format="csv", where="sensornr=240")
```

Formats: `csv`, `json`, `geojson`, `xlsx`, `shp`, `parquet`, `gpx`, `kml`, `rdfxml`, `jsonld`, `turtle`

## Debug

```bash
npx @modelcontextprotocol/inspector uv run main.py
```
