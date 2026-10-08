"""OpenAPI 3.1 spec and Swagger UI page for the streamable HTTP MCP endpoint.

The spec is generated from the live FastMCP tool registry, so tools and their
JSON schemas stay in sync with the server without a separate manual spec.
Because MCP over streamable HTTP is a single JSON-RPC endpoint (POST /mcp),
each tool is modelled as a reusable request schema listed in the body's
oneOf; the rendered Swagger UI offers a ready-made example per tool.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mcp.types import DEFAULT_NEGOTIATED_VERSION

MCP_ACCEPT = "application/json, text/event-stream"

_SSE_EXAMPLE = (
    "event: message\n"
    'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"<tool output>"}],"isError":false}}'
)

_REQUEST_EXAMPLES: dict[str, dict] = {
    "get_datasets": {"search": "feinstaub", "limit": 5},
    "get_dataset": {"dataset_id": "100113"},
    "get_records": {"dataset_id": "100113", "limit": 5},
    "get_facets": {"facet": "theme"},
    "export_dataset_url": {"dataset_id": "100113", "format": "csv"},
}


def _ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{name}"}


def _hoist_defs(schema: Any, defs: dict[str, dict]) -> Any:
    """Rewrite #/$defs/... refs to #/components/schemas/..., hoisting the
    targets. OpenAPI only resolves refs against components, not $defs."""
    if isinstance(schema, list):
        return [_hoist_defs(item, defs) for item in schema]
    if not isinstance(schema, dict):
        return schema
    hoisted: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "$defs":
            for name, sub_schema in value.items():
                if name not in defs:
                    defs[name] = _hoist_defs(sub_schema, defs)
        elif key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/"):
            hoisted[key] = f"#/components/schemas/{value.removeprefix('#/$defs/')}"
        else:
            hoisted[key] = _hoist_defs(value, defs)
    return hoisted


def _request_schema(method: str, params: Any, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "jsonrpc": {
                "const": "2.0",
                "description": "JSON-RPC protocol version",
            },
            "id": {
                "oneOf": [{"type": "integer"}, {"type": "string"}],
                "description": "Request id, echoed by the response",
            },
            "method": {"const": method},
            "params": params,
        },
        "required": required or ["jsonrpc", "id", "method", "params"],
        "additionalProperties": False,
    }


def _result_schema(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "description": "JSON-RPC message carried in one SSE data: frame",
        "properties": {
            "jsonrpc": {"const": "2.0"},
            "id": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
            "result": result,
        },
        "required": ["jsonrpc", "id", "result"],
    }


def _component_name(tool_name: str) -> str:
    return "".join(part.capitalize() for part in tool_name.split("_")) + "Request"


def _tool_component(tool: Any) -> dict[str, Any]:
    defs: dict[str, dict] = {}
    arguments = _hoist_defs(dict(tool.parameters), defs)
    arguments.pop("title", None)
    arguments["additionalProperties"] = False
    component = _request_schema(
        "tools/call",
        {
            "type": "object",
            "description": "Tool name must match the schema",
            "properties": {"name": {"const": tool.name}, "arguments": arguments},
            "required": ["name"],
            "additionalProperties": False,
        },
    )
    component["description"] = f"`tools/call` → `{tool.name}`: {tool.description}"
    component["example"] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool.name, "arguments": _REQUEST_EXAMPLES.get(tool.name, {})},
    }
    return component


def build_openapi(domain: str, tools: list[Any]) -> dict[str, Any]:
    schemas: dict[str, Any] = {}
    for tool in tools:
        schemas[_component_name(tool.name)] = _tool_component(tool)
    schemas["ToolsListRequest"] = _request_schema(
        "tools/list",
        {
            "type": "object",
            "properties": {"cursor": {"type": "string"}},
            "additionalProperties": False,
        },
        required=["jsonrpc", "id", "method"],
    )
    schemas["ToolsListRequest"]["example"] = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    schemas["ToolCallResponse"] = _result_schema(
        {
            "type": "object",
            "properties": {
                "content": {
                    "type": "array",
                    "items": _ref("TextContent"),
                    "description": "Plain-text frames; tool results are JSON strings",
                },
                "isError": {"type": "boolean"},
                "structuredContent": {"type": "object", "additionalProperties": True},
            },
            "required": ["content"],
        }
    )
    schemas["ToolsListResponse"] = _result_schema(
        {
            "type": "object",
            "properties": {
                "tools": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "inputSchema": {"type": "object"},
                        },
                        "required": ["name", "description", "inputSchema"],
                    },
                }
            },
            "required": ["tools"],
        }
    )
    schemas["TextContent"] = {
        "type": "object",
        "properties": {"type": {"const": "text"}, "text": {"type": "string"}},
        "required": ["type", "text"],
    }
    schemas["JsonRpcError"] = {
        "type": "object",
        "properties": {
            "jsonrpc": {"const": "2.0"},
            "id": {"oneOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}]},
            "error": {
                "type": "object",
                "properties": {
                    "code": {"type": "integer"},
                    "message": {"type": "string"},
                    "data": {},
                },
                "required": ["code", "message"],
            },
        },
        "required": ["jsonrpc", "id", "error"],
    }

    error_response = {
        "description": (
            "Protocol failure: 400 malformed JSON-RPC, 405 wrong method, "
            "406 invalid Accept, 415 wrong Content-Type"
        ),
        "content": {
            "application/json": {"schema": _ref("JsonRpcError")},
            "text/event-stream": {"schema": _ref("JsonRpcError")},
        },
    }
    header_params = [
        {
            "name": "Accept",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "default": MCP_ACCEPT},
            "description": "MCP transport requires both application/json and text/event-stream",
        },
        {
            "name": "mcp-protocol-version",
            "in": "header",
            "required": False,
            "schema": {"type": "string", "default": DEFAULT_NEGOTIATED_VERSION},
            "description": "MCP protocol version; omitted headers default to "
            + DEFAULT_NEGOTIATED_VERSION,
        },
    ]

    try:
        api_version = version("data-bs-mcp")
    except PackageNotFoundError:
        api_version = "0.0.0"

    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"{domain} — MCP API",
            "version": api_version,
            "description": (
                f"MCP server for the {domain} open data portal, exposed over the Model Context "
                "Protocol via streamable HTTP.\n\n"
                "The endpoint is JSON-RPC 2.0: every tool is a `tools/call` request on "
                "`POST /mcp`, and `tools/list` discovers all tools. Pick a request schema from "
                "the **oneOf** list for a ready-made example per tool.\n\n"
                "The server runs in **stateless** mode: no `initialize` handshake is required, "
                "no `mcp-session-id` is issued, and each request stands alone (see the MCP "
                "specification for the wire format).\n\n"
                "POST requests with incomplete `Accept` headers are tolerated: the server "
                "rewrites them to `application/json, text/event-stream` before the transport "
                "validates them.\n\n"
                "Interactive reference: `GET /docs` (this page). Raw spec: `GET /openapi.json`."
            ),
        },
        "servers": [{"url": "/"}],
        "tags": [
            {"name": "MCP", "description": "JSON-RPC endpoint consumed by MCP clients"},
            {"name": "Meta", "description": "Service status and API reference"},
        ],
        "paths": {
            "/mcp": {
                "post": {
                    "operationId": "mcpPost",
                    "summary": "MCP JSON-RPC (tools/call · tools/list)",
                    "tags": ["MCP"],
                    "description": (
                        "One endpoint carries all MCP requests. Request body is a JSON-RPC 2.0 "
                        "envelope; the oneOf members are per-tool schemas (and tools/list for "
                        "discovery) with working examples."
                    ),
                    "parameters": header_params,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        _ref(name) for name in schemas if name.endswith("Request")
                                    ]
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Result, streamed as server-sent events; each data: "
                            "frame is a JSON-RPC message (see ToolCallResponse / ToolsListResponse)",
                            "content": {
                                "text/event-stream": {
                                    "schema": {"type": "string"},
                                    "example": _SSE_EXAMPLE,
                                }
                            },
                        },
                        "202": {
                            "description": "Notification or response accepted, no reply expected"
                        },
                        "default": error_response,
                    },
                }
            },
            "/healthz": {
                "get": {
                    "operationId": "healthz",
                    "summary": "Liveness probe",
                    "tags": ["Meta"],
                    "description": (
                        'Container healthcheck endpoint. Always returns `{"status": "ok"}` '
                        "with HTTP 200 when the process is serving."
                    ),
                    "responses": {
                        "200": {
                            "description": "Service is up",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"status": {"const": "ok"}},
                                        "required": ["status"],
                                    },
                                    "example": {"status": "ok"},
                                }
                            },
                        }
                    },
                }
            },
            "/": {
                "get": {
                    "operationId": "index",
                    "summary": "Service info",
                    "tags": ["Meta"],
                    "description": (
                        "Human- and machine-readable status page: server name, transport, "
                        "and links to all endpoints. Prevents GET requests on the root from "
                        "hitting the MCP transport."
                    ),
                    "responses": {
                        "200": {
                            "description": "Service metadata",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "server": {"type": "string"},
                                            "status": {"const": "ok"},
                                            "transport": {"const": "mcp-streamable-http"},
                                            "endpoints": {
                                                "type": "object",
                                                "properties": {
                                                    "mcp": {"const": "/mcp"},
                                                    "health": {"const": "/healthz"},
                                                    "openapi": {"const": "/openapi.json"},
                                                    "docs": {"const": "/docs"},
                                                },
                                                "required": ["mcp", "health", "openapi", "docs"],
                                            },
                                        },
                                        "required": ["server", "status", "transport", "endpoints"],
                                    },
                                    "example": {
                                        "server": domain,
                                        "status": "ok",
                                        "transport": "mcp-streamable-http",
                                        "endpoints": {
                                            "mcp": "/mcp",
                                            "health": "/healthz",
                                            "openapi": "/openapi.json",
                                            "docs": "/docs",
                                        },
                                    },
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {"schemas": schemas},
    }


def docs_page(domain: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{domain} — MCP API reference</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
    <style>body {{ margin: 0; }}</style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js" crossorigin></script>
    <script>
      window.addEventListener('load', function () {{
        SwaggerUIBundle({{
          url: '/openapi.json',
          dom_id: '#swagger-ui',
          deepLinking: true,
        }});
      }});
    </script>
  </body>
</html>
"""
