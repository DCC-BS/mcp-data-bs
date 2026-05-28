# data-bs-mcp

MCP server for [data.bs.ch](https://data.bs.ch) OpenDataSoft API v2.1.

## Installation

```bash
uv sync
```

## Usage

```bash
uv run main.py
```

## Debug
```bash
npx @modelcontextprotocol/inspector uv run main.py
```

### Install with uvx
```bash
uvx --from git+https://github.com/DCC-BS/mcp-data-bs data-bs-mcp
```

## Configuration

### OpenCode

Add to your OpenCode config:

```json
{
  "mcpServers": {
    "data-bs": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/TO/data-bs-mcp",
        "run",
        "main.py"
      ]
    }
  }
}
```

### Cursor

Add to your Cursor config (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "data-bs": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/TO/data-bs-mcp",
        "run",
        "main.py"
      ]
    }
  }
}
```

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
Get detailed metadata for a specific dataset.

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
