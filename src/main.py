"""Apify Actor entry point supporting Batch and Standby HTTP/MCP modes."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from aiohttp import web
from apify import Actor

from .tse_client import run_tse_query

EVENT_NAME = "record-delivered"

MCP_TOOLS = [
    {
        "name": "query_brazil_elections",
        "description": (
            "Query official Brazilian TSE election results (Presidente, Governador, Municipal), "
            "election catalogs, municipalities/zones, and geocoded polling stations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["results", "elections_catalog", "municipalities", "polling_stations"],
                },
                "cycle": {"type": "string", "default": "ele2026"},
                "electionId": {"type": "integer", "default": 6258},
                "scope": {"type": "string", "default": "br"},
                "officeCode": {"type": "integer", "default": 1},
                "municipalityCode": {"type": "string"},
                "query": {"type": "string"},
                "maxResults": {"type": "integer", "default": 50},
            },
            "required": ["mode"],
        },
    }
]


async def _charge_records(count: int) -> int:
    if count <= 0:
        return 0
    max_items_env = os.environ.get("ACTOR_MAX_PAID_DATASET_ITEMS")
    allowed = count
    if max_items_env and max_items_env.isdigit():
        allowed = min(count, int(max_items_env))
    if allowed <= 0:
        return 0
    try:
        charge_res = await Actor.charge(event_name=EVENT_NAME, count=allowed)
        if hasattr(charge_res, "event_charge_limit_reached") and charge_res.event_charge_limit_reached:
            Actor.log.warning("Pay-per-event spending limit reached on %s.", EVENT_NAME)
    except Exception as exc:
        Actor.log.warning("Charge skipped in local/non-monetized mode: %s", exc)
    return allowed


async def _handle_mcp_rpc(payload: dict[str, Any]) -> dict[str, Any]:
    rpc_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "brazil-elections-tse", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": MCP_TOOLS}}
    if method == "tools/call":
        if params.get("name") != "query_brazil_elections":
            return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Unknown tool"}}
        rows = await run_tse_query(params.get("arguments") or {})
        await _charge_records(len(rows))
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"content": [{"type": "text", "text": json.dumps(rows, ensure_ascii=False)}]},
        }
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"Unsupported method: {method}"}}


async def _run_standby_server() -> None:
    port = int(os.environ.get("ACTOR_STANDBY_PORT", "8080"))

    async def handle_root(request: web.Request) -> web.Response:
        if request.headers.get("x-apify-container-server-readiness-probe"):
            return web.Response(text="OK", status=200)
        rows = await run_tse_query(
            {
                "mode": request.query.get("mode", "results"),
                "cycle": request.query.get("cycle", "ele2026"),
                "electionId": int(request.query.get("electionId", "6258")),
                "scope": request.query.get("scope", "br"),
                "officeCode": int(request.query.get("officeCode", "1")),
                "municipalityCode": request.query.get("municipalityCode", ""),
                "query": request.query.get("query", ""),
                "maxResults": int(request.query.get("maxResults", "50")),
            }
        )
        await _charge_records(len(rows))
        return web.json_response({"count": len(rows), "items": rows})

    async def handle_post(request: web.Request) -> web.Response:
        payload = await request.json()
        if isinstance(payload, dict) and payload.get("jsonrpc") == "2.0":
            return web.json_response(await _handle_mcp_rpc(payload))
        rows = await run_tse_query(payload if isinstance(payload, dict) else {})
        await _charge_records(len(rows))
        return web.json_response({"count": len(rows), "items": rows})

    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_post("/", handle_post)
    app.router.add_post("/mcp", handle_post)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    Actor.log.info("Brazil Elections Standby HTTP/MCP server listening on port %s", port)
    await asyncio.Event().wait()


async def main() -> None:
    async with Actor:
        if os.environ.get("APIFY_META_ORIGIN") == "STANDBY":
            await _run_standby_server()
            return
        actor_input = await Actor.get_input() or {}
        rows = await run_tse_query(actor_input)
        allowed = await _charge_records(len(rows))
        to_push = rows[:allowed] if allowed else []
        if to_push:
            await Actor.push_data(to_push)
        Actor.log.info("Delivered %d TSE election records.", len(to_push))


if __name__ == "__main__":
    asyncio.run(main())
