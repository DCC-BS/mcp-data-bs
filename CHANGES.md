# Änderungsübersicht — mcp-data-bs

Stand: 08.10.2026 · Basis: `c465b1f` (Merge branch `feat/hostable-mcp-and-skills`)
Alle Änderungen liegen **unkommitted** im Working Directory.

## Geänderte Dateien

| Datei | Änderung |
|---|---|
| `main.py` | MCP 2.x-Migration, Middleware, Status-Seite, Docstring-Verbesserungen |
| `api_docs.py` | **neu** — OpenAPI-3.1-Generator + Swagger-UI-Seite |
| `tests/test_http.py` | 6 neue Tests (HTTP-Endpunkte, Accept-Fallback, Spec) |
| `tests/test_tools.py` | Test an Feld-Capability-Flags angepasst |
| `uv.lock` | Upgrade aller Dependencies (2 Majors) |
| `README.md` | Copy-Paste-Client-Snippets, API-Reference-, Hosting-Doku |

---

## 1. MCP-Endpunkt auf `/mcp` verschoben

- `main.py`: `streamable_http_path` von `/` → `/mcp`.
- Begründung: Ein bloßes `GET /` mit `text/event-stream` (idle SSE-Stream) lies
  Clients wie `agy` ("serverUrl") auf einen traditionellen SSE-Handshake
  warten und blockieren.

## 2. Abhängigkeiten aktualisiert (`uv lock --upgrade`)

Kein Age-Gate konfiguriert → neueste Versionen genommen.

| Paket | Vorher | Nachher |
|---|---|---|
| **mcp** (Major!) | 1.26.0 | **2.3.0** |
| **starlette** (Major!) | 0.52.1 | **1.7.0** |
| uvicorn | 0.53.0 | 0.54.0 |
| ruff (dev) | 0.16.8 | 0.16.10 |

Dazu transitive Updates (rich 15, sse-starlette 3.5, typer 0.27, rpds-py,
truststore, …).

**Migration wegen mcp 2.x (brechende Änderungen):**

- `from mcp.server.fastmcp import FastMCP` →
  `from mcp.server.mcpserver import MCPServer` (FastMCP wirft in 2.x explizit
  einen `ModuleNotFoundError` mit Migrationshinweis).
- Transport-Einstellungen (`stateless_http`, `streamable_http_path`,
  `transport_security`) stehen nicht mehr im Konstruktor, sondern werden an
  `streamable_http_app(**MCP_HTTP_SETTINGS)` übergeben (zentral in
  `MCP_HTTP_SETTINGS` in `main.py`).
- Session-Manager liegt nun am Lowlevel-Server
  (`mcp._lowlevel_server._session_manager`) — Test-Reset in
  `tests/test_http.py::_app` entsprechend angepasst.

## 3. Accept-Header-Falle (HTTP 400/406) entschärft

`RelaxedAcceptHeaderMiddleware` (`main.py`) — registriert via
`app.add_middleware(...)` in `create_http_app()`:

- Rewritten bei **POST**: `Accept: application/json`, `*/*` oder fehlender
  `application/json`-Anteil → `application/json, text/event-stream`,
  bevor der Transport validiert (der warf sonst 406).
- Gegenüber der Vorlage (User-Snippet) gehärtet: deckt auch `*/*`-only und
  fehlendes `application/json` ab.
- Livetest: `Accept: application/json` → **200**, `Accept: */*` → **200**
  (vorher 406), strikter Header unverändert funktionsfähig.

Damit sind einfache JSON-RPC-POST-Clients ohne Anpassung kompatibel.

## 4. `GET /` statt idle SSE-Stream

- Neue Route `GET /` (`main.py::_index`) liefert eine JSON-Status-Seite:
  `{"server": ..., "status": "ok", "transport": "mcp-streamable-http",
  "endpoints": {mcp, health, openapi, docs}}`.
- Verhindert, dass SSE-erwartende Clients beim Root-GET auf einen
  Handshake blockieren, der nie kommt (`event: endpoint` wurde nie gesendet).
- Kein separater Legacy-`/sse`-Endpunkt; falls ein Client (z. B. `agy`)
  weiterhin Probleme macht, ist das der nächste Schritt.

## 5. OpenAPI-Dokumentation mit gerendertem Swagger-UI

Neu: `api_docs.py`, verdrahtet in `create_http_app()`:

- `GET /docs` — Swagger UI (Assets vom unpkg-CDN im HTML); jede Ansicht ist
  per *Try it out* gegen den laufenden Server ausführbar.
- `GET /openapi.json` — rohe **OpenAPI 3.1**-Spec.
- Spec wird **zur Laufzeit aus der live MCP-Tool-Registry generiert**
  (`mcp._tool_manager.list_tools()`): Schemas, Beschreibungen und Beispiele
  leiten sich aus den `@mcp.tool`-Definitionen ab — neue Tools erscheinen
  automatisch, kein Drift.
- Da MCP ein einzelner JSON-RPC-Endpunkt ist, ist jedes Tool als
  Request-Schema modelliert (`GetDatasetsRequest`, `ToolsListRequest`, …),
  aufgelistet im `oneOf` des Request-Bodies → Swagger zeigt pro Tool ein
  fertiges Beispiel.
- Dokumentiert sind alle Pfade: `POST /mcp` (JSON-RPC, SSE-Antworten,
  `Accept`/`mcp-protocol-version`-Header, stateless — kein
  `initialize`-Handshake nötig), `GET /healthz` (Liveness), `GET /` (Service-Info).
- Validiert mit `openapi-spec-validator` gegen den OpenAPI-3.1-Schema-Metastandard.
- Beispiel-Executions getestet: `get_dataset(100113)` lieferte
  „Feinstaubmessungen auf BVB-Trams“ live.

## 6. Tool-Design & LLM-Prompting

### 6a. ODSQL-Suchhinweise in Docstrings (`get_records`, `get_datasets`)

Vorher gegen die Live-API verifiziert (`100311`, `100113`):

- `search()` tokenisiert und matcht **Teilwörter**: `search("Geschä")` → 519
  Treffer (inkl. „Geschäft“) → Teilwort-Treffer wie „Hitzkirch“ bei
  `search("Hitze")` sind systemisch.
- **Phrasensuche**: `search("öffentliche Frage")` → 1 Treffer (exakt).
- **Boolesche Operatoren**: `search("Verkehr") AND NOT search("Tram")` → 503,
  mit OR → 812.
- **`LIKE` mit `%`-Wildcards** als präzise Alternative (case-insensitive):
  `titel_ges LIKE "verkehr%"` → 34 statt Volltext-528.
- Randfall: `100113` hat keine Textfelder → `search()` liefert dort immer 0;
  numerische Vergleiche (`pm25 > 10`) oder `refine` sind der richtige Weg.

Umgesetzt:

- `get_records`-Docstring: neuer Abschnitt „ODSQL search notes for `where`“
  (Teilwort-Falle, Phrasensuche, AND/OR/NOT, `=`/`LIKE`-Vergleiche, Verweis
  auf `refine` + `get_facets` für exakte Werte).
- `get_datasets`-Docstring: gleiche Hinweise auf Catalog-Ebene inkl. des
  `Hitze`→`Hitzkirch`-Beispiels; Empfehlung „prefer refine over search, wenn
  der exakte Begriff bekannt ist“.

### 6b. Kompaktes Schema mit Capability-Flags in `get_dataset`

- API liefert pro Feld `annotations` mit `facet`, `sortable`, `disjunctive`,
  `timerangeFilter`.
- `get_dataset` gibt jetzt pro Feld `{name, type}` plus **nur gesetzte** Flags
  zurück: `sortable` (für `order_by`), `facet` (für `refine`/`exclude`),
  `disjunctive`, `timerange_filter`. Fehlendes Flag = nicht unterstützt
  (dokumentiert im Docstring).
- Beispiel `100311` (30 Spalten): 16× `facet: true`, 28× `sortable`.
- Kosten: ca. +1 KB pro Call — bewusst schlank (keine Labels/Beschreibungen);
  verhindert Halluzinationen bei `where`/`order_by`-Parametern.
- Docstring beschreibt die Semantik und mahnt, Flags statt Feldnamen zu raten.

## 7. README: Copy-Paste-Snippets für gängige Clients

Abschnitt „Connecting clients“ neu gegliedert, führt jetzt mit Schnellstart:

- **Antigravity CLI (`agy`)**:
  `agy mcp add --env DATA_PORTAL_DOMAIN=data.bs.ch databs -- uvx --from git+https://github.com/DCC-BS/mcp-data-bs data-bs-mcp`
- **Claude Desktop / Cursor**: gemeinsames `mcpServers`-JSON (`uvx` + `env`
  mit `DATA_PORTAL_DOMAIN`), inkl. Config-Pfaden.
- **OpenWebUI / ChatGPT**: konkrete Schritte (Settings → Tools bzw.
  Connectors) mit `https://mcp.data.bs.ch/mcp` und Verweis auf `/docs`.
- **Weitere Clients** (opencode, uvx) in Unterabschnitt verschoben; uvx-Snippet
  korrigiert — setzt jetzt `DATA_PORTAL_DOMAIN` (fehlte zuvor).
- `uvx`-Auflösung vom GitHub-Repo live getestet (`Installed 40 packages`).
  ⚠ Hostname `mcp.data.bs.ch` ist angenommen — vor Merge verifizieren.

## Verifikation (alles grün)

- `mise run test:unit` → **13 Tests bestanden** (6 neue in `test_http.py`,
  1 erweitert in `test_tools.py`).
- `mise run check` → ruff format + ruff check sauber.
- OpenAPI-3.1-Validierung bestanden.
- Live-Smoke gegen `data.bs.ch`: Status-Seite `/`, `/healthz`, relaxter
  Accept-Header (200), strikter Header + `tools/call` (echte Daten),
  `get_dataset("100311")` mit Flags.
- `import main` ohne HTTP: ok.

## Offene Punkte / Hinweise

- Alle Änderungen **nicht committed** (8 Dateien, siehe Tabelle oben).
- Hostname `mcp.data.bs.ch` in README-Client-Snippets angenommen; ggf.
  an den realen Produktions-Hostnamen anpassen.
- `agy`-Syntax nicht lokal prüfbar (CLI nicht installiert) — Muster folgt der
  User-Vorlage.
- Legacy-`/sse`-Endpunkt bewusst nicht implementiert; nur nachziehen, wenn
  konkrete Clients damit Probleme haben.