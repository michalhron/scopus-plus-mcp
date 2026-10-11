"""Connection, quota and version checks."""
import mcp.types as types

from .common import server_module


TOOLS = [
    types.Tool(
        name="diagnose_connection",
        description=(
            "Diagnose Scopus connectivity and entitlement. Checks config "
            "presence, api.elsevier.com reachability, metadata and search "
            "entitlement, and per-API capabilities (REF-view references, "
            "ScienceDirect full text, Serial Title journal metrics). Returns "
            "a JSON report with a one-line verdict and 'unavailable_tools', "
            "the tools that cannot work with the current access. Run this "
            "first when Scopus behaves strangely "
            "— especially when valid searches fail with 'Error translating "
            "query', which usually means missing subscriber entitlement "
            "(off-network without SCOPUS_INSTTOKEN), not bad query syntax."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),
    types.Tool(
        name="get_quota_status",
        description="Get the current API quota status (remaining/limit). Note: Values are updated only after making a request.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),
    types.Tool(
        name="get_server_info",
        description=(
            "Return the server version and a health summary. "
            "Call this to confirm which build you are talking to."
        ),
        inputSchema={"type": "object", "properties": {}}
    ),
]


async def _diagnose_connection(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    report = await client.diagnose_connection()
    import json as _json
    return [types.TextContent(type="text", text=_json.dumps(report, indent=2))]


async def _get_quota_status(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    quota = await client.get_quota_status()
    lines = [f"Scopus: {quota}" if quota else
             "Scopus: no quota information yet (make a Scopus request first)."]
    budget = {k: v for k, v in (openalex.budget or {}).items() if v is not None}
    if budget:
        lines.append(
            f"OpenAlex: ${budget.get('remaining_usd')} left of ${budget.get('limit_usd')} "
            f"per day, resets in {budget.get('reset_seconds')} s "
            f"(key: {'yes, from ' + openalex.key_source if openalex.has_key else 'no, anonymous'}).")
    else:
        lines.append("OpenAlex: no budget information yet (make an OpenAlex request first); "
                     f"key: {'yes, from ' + openalex.key_source if openalex.has_key else 'no, anonymous'}.")
    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_server_info(arguments: dict) -> list:
    srv = server_module()
    SERVER_VERSION = srv.SERVER_VERSION
    return [types.TextContent(
        type="text",
        text=(
            f"scopus-plus-mcp server\n"
            f"version: {SERVER_VERSION}\n"
            f"status: ok\n"
        ),
    )]


HANDLERS = {
    'diagnose_connection': _diagnose_connection,
    'get_quota_status': _get_quota_status,
    'get_server_info': _get_server_info,
}
