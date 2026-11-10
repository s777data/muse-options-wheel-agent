"""Robinhood transport shim (the ONLY transport module in this repo).

The source wheel code expects Hermes MCP bindings named
``call_robinhood_tool()`` / ``_robinhood_tool()`` (csp_screening.py) and
``call_mcp()`` (order_lifecycle.py). This module provides those exact
call surfaces on top of the local read-only Robinhood client:

    python3 ~/workspace/custom-connectors/custom.robinhood/bin/client.py \
        call-tool --name <tool> --arguments-file <tmp.json>

and returns the parsed ``structuredContent`` payload.

READ-ONLY CORE: the 15 original read tools in the local client's
ALLOWED_TOOLS. On 2026-11-09 BIG SANDY approved extending the client
allowlist with 5 non-order tools for this agent: ``get_equity_tax_lots``
and ``get_trade_approvals`` (reads), plus ``create_scan``, ``run_scan``
and ``review_option_order`` (they create scanner runs and order
*previews* — they cannot place, cancel, or modify any order; see
NON_ORDER_ACTION_TOOLS). Any order write tool
(place/cancel/exercise/...) still raises ``WriteToolDisabled``; any
other non-allowlisted tool raises ``ToolNotAllowlisted``. Nothing here
ever invents data: a failed call raises ``RobinhoodTransportError``.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

CLIENT = Path.home() / "workspace/custom-connectors/custom.robinhood/bin/client.py"

READ_TOOLS = {
    "get_accounts", "get_portfolio", "get_equity_positions",
    "get_option_positions", "get_equity_orders", "get_option_orders",
    "get_equity_quotes", "get_equity_fundamentals", "get_equity_historicals",
    "get_option_chains", "get_option_instruments", "get_option_quotes",
    "get_equity_technical_indicators", "get_earnings_calendar",
    "get_earnings_results",
    # Added 2026-11-09 with BIG SANDY's approval (pure reads):
    "get_equity_tax_lots", "get_trade_approvals",
}

# Approved non-order action tools (2026-11-09, BIG SANDY): scanner runs
# and order *review* previews. None of these can place, cancel, replace,
# or exercise an order; order writes stay in WRITE_TOOLS below.
NON_ORDER_ACTION_TOOLS = {
    "create_scan", "run_scan", "review_option_order",
}

WRITE_TOOLS = {
    "place_option_order", "place_equity_order", "place_crypto_order",
    "cancel_option_order", "cancel_equity_order", "cancel_crypto_order",
    "cancel_option_exercise", "exercise_option",
    "review_equity_order", "preview_crypto_order", "decline_trade_approval",
    "update_scan_config", "update_scan_filters",
    "preview_scan", "create_watchlist", "add_to_watchlist",
    "add_option_to_watchlist", "remove_from_watchlist",
    "remove_option_from_watchlist", "follow_watchlist", "unfollow_watchlist",
    "update_watchlist", "create_alert", "update_alert", "delete_alert",
    "mark_alerts_read", "add_legend_indicator", "remove_legend_indicator",
    "update_legend_indicator", "set_legend_chart_settings",
}


class RobinhoodTransportError(RuntimeError):
    pass


class WriteToolDisabled(RobinhoodTransportError):
    """Raised for any write/mutating tool — hard disabled, by design."""


class ToolNotAllowlisted(RobinhoodTransportError):
    """Server offers the tool but the local read-only client blocks it."""


def call_robinhood_tool(name: str, arguments: dict | None = None,
                        timeout_seconds: int = 120) -> dict:
    """Call one allowlisted read tool via the local client; return the
    parsed structuredContent (or the full result envelope's data)."""
    if name in WRITE_TOOLS:
        raise WriteToolDisabled(
            f"write tool {name!r} is hard-disabled in muse-options-wheel-agent")
    if name not in READ_TOOLS and name not in NON_ORDER_ACTION_TOOLS:
        raise ToolNotAllowlisted(
            f"tool {name!r} is not in the local Robinhood client's "
            f"read allowlist (see INVENTORY.md parity gaps)")
    args = arguments or {}
    with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="rh_args_", delete=False) as fh:
        json.dump(args, fh)
        args_path = fh.name
    try:
        proc = subprocess.run(
            ["python3", str(CLIENT), "call-tool", "--name", name,
             "--arguments-file", args_path],
            capture_output=True, text=True, timeout=timeout_seconds)
    finally:
        Path(args_path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RobinhoodTransportError(
            f"{name} failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RobinhoodTransportError(f"{name}: unparseable response: {exc}")
    # client.py prints the MCP result envelope; unwrap structuredContent.
    if isinstance(payload, dict):
        structured = payload.get("structuredContent")
        if structured is not None:
            return structured
        result = payload.get("result")
        if isinstance(result, dict) and result.get("structuredContent") is not None:
            return result["structuredContent"]
    return payload


# Aliases matching the source modules' expected binding names.
_robinhood_tool = call_robinhood_tool


def call_mcp(name: str, arguments: dict | None = None, **kwargs):
    """order_lifecycle.py binding name — same read-only enforcement."""
    return call_robinhood_tool(name, arguments, **kwargs)


_LIST_KEYS = ("accounts", "positions", "orders", "instruments", "quotes",
              "chains", "lots", "contracts")


def hermes_envelope(tool_name: str, arguments: dict | None = None) -> dict:
    """Call a tool and return the Hermes result envelope the source wheel
    modules parse: {"result": "<json string of {data: ...}>"}.

    Normalization (transport-level only): the local client returns the
    MCP structuredContent; if it lacks a "data" object it is wrapped as
    one, and for list endpoints a missing "results" list is aliased from
    the endpoint's named list (e.g. get_accounts' "accounts"), which is
    the shape the Hermes-bound source code was written against.
    """
    structured = call_robinhood_tool(tool_name, arguments or {})
    payload = structured
    if not (isinstance(payload, dict) and isinstance(payload.get("data"), dict)):
        payload = {"data": payload}
    data = payload["data"]
    if isinstance(data, dict) and "results" not in data:
        for key in _LIST_KEYS:
            if isinstance(data.get(key), list):
                data = dict(data)
                data["results"] = data[key]
                payload = {"data": data}
                break
    return {"result": json.dumps(payload)}
