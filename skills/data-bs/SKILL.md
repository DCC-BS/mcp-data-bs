---
name: data-bs
description: >
  Query the canton of Basel-Stadt open data portal (data.bs.ch, Huwise/Opendatasoft).
  Use to discover, describe, and query open datasets via the Explore v2.1 REST API
  directly — no MCP server needed. Covers catalog search (semantic + lexical),
  dataset metadata, records with ODSQL, facets, and export URLs.
---

# Basel-Stadt Open Data (data.bs.ch)

Query open datasets from the Basel-Stadt data portal through its public HTTP API.
Everything here works with plain HTTP (curl, python `httpx`, or any HTTP client);
there is no SDK or MCP dependency.

## Base URL

```
https://data.bs.ch/api/explore/v2.1
```

- API family: Huwise / Opendatasoft Explore 2.1. Any portal on the same platform
  (e.g. `data.bl.ch`) shares this path; just change the domain.
- Auth: none. The catalog is public open data.
- Response format: JSON by default. Set `Accept: application/json`.
- ODSQL is the query language used in `where`/`order_by`/`group_by`/`select`.

## 1. Search datasets

`GET /catalog/datasets`

Two search modes:

- **semantic** (default): rank the whole catalog by meaning using
  `order_by=vector_similarity("<query>") desc`. Handles synonyms and other
  languages; returns the most relevant results, not a filter.
- **lexical**: exact full-text match via `where=search("<query>")`.

Always URL-encode the query string.

```bash
# semantic — natural language, ranked by relevance
curl -G "https://data.bs.ch/api/explore/v2.1/catalog/datasets" \
  --data-urlencode 'order_by=vector_similarity("air quality measurements") desc' \
  --data-urlencode 'limit=10'

# lexical — exact full-text match
curl -G "https://data.bs.ch/api/explore/v2.1/catalog/datasets" \
  --data-urlencode 'where=search("luft")' \
  --data-urlencode 'order_by=title asc' \
  --data-urlencode 'limit=10'

# facet filter
curl -G "https://data.bs.ch/api/explore/v2.1/catalog/datasets" \
  --data-urlencode "refine=publisher:Statistisches Amt" \
  --data-urlencode 'limit=10'
```

### Query params

| Param | Meaning |
|-------|---------|
| `limit` | Items to return (max 100) |
| `offset` | Pagination offset |
| `where` | ODSQL WHERE (e.g. `search("term")`, facet conditions) |
| `order_by` | Sort field, e.g. `modified desc`, `title asc` |
| `refine` | Facet filter to keep results, e.g. `publisher:...`, `modified:2019/12` |
| `exclude` | Facet filter to drop results |
| `include_app_metas` | `true` for app metadata |

Each dataset's entry has an `id` field (the `dataset_id`) to use in later calls,
plus `metas.default.title`, `metas.default.description`, `metas.default.theme`,
`metas.default.keyword`, `metas.default.publisher`, and
`metas.explore.records_count`.

## 2. Get dataset metadata

`GET /catalog/datasets/{dataset_id}`

Returns detailed metadata plus the `fields` array (column `name` + `type`).
Read this before querying records so you use accurate field names.

```bash
curl "https://data.bs.ch/api/explore/v2.1/catalog/datasets/100113"
```

## 3. Query records

`GET /catalog/datasets/{dataset_id}/records?limit=N`

```bash
# filter with ODSQL WHERE
curl -G "https://data.bs.ch/api/explore/v2.1/catalog/datasets/100113/records" \
  --data-urlencode 'where=pm25 > 10' \
  --data-urlencode 'order_by=time DESC' \
  --data-urlencode 'limit=100'
```

### ODSQL quick reference

- Where clause: `pm25 > 10`, `time >= '2020-01-01'`, string equality `city='Basel'`.
- `select` expression to reshape output: `size`, `size * 2 as bigger_size`, `*`.
- `group_by` for aggregations: `group_by=city_field as city`.
- `order_by`: `time DESC`, `pm25 ASC` (ignored in semantic catalog search).
- `refine`/`exclude` also work on records.

### Record query params

| Param | Meaning |
|-------|---------|
| `select` | Field expression (default `*`) |
| `where` | ODSQL WHERE clause |
| `group_by` | Grouping expression |
| `order_by` | Sort expression |
| `limit` | Max 100 (up to 20000 when using `group_by`) |
| `offset` | Pagination |
| `refine`/`exclude` | Facet filters |
| `lang` | Locale for formatting (`en`, `de`, `fr`) |
| `timezone` | TZ for datetimes, e.g. `Europe/Zurich` |

Always respect `limit`; paginate with `offset` for large result sets.

## 4. Facets

`GET /catalog/facets?facet=<name>` or omit `facet` for all.

Useful facet names: `publisher`, `keyword`, `theme`, `features`, `modified`,
`language`. Use these to discover what values exist before filtering.

```bash
curl -G "https://data.bs.ch/api/explore/v2.1/catalog/facets" \
  --data-urlencode 'facet=publisher'
```

## 5. Export URLs

`GET /catalog/datasets/{dataset_id}/exports/{format}`

Formats: `csv`, `json`, `geojson`, `xlsx`, `shp`, `parquet`, `gpx`, `kml`,
`rdfxml`, `jsonld`, `turtle`. Append `?where=<odsql>` to filter the export.

```bash
# CSV download of a filtered subset
curl -G "https://data.bs.ch/api/explore/v2.1/catalog/datasets/100113/exports/csv" \
  --data-urlencode 'where=sensornr=240' -o output.csv
```

## Suggested workflow

1. Search the catalog (semantic for a topic, lexical for an exact name).
2. Read metadata for the top hit(s) to learn real field names/types.
3. Query records with an ODSQL `where`/`group_by`, respecting `limit`/`offset`.
4. Or produce an export URL if the consumer wants a download / bulk file.
