"""Request-scoped OAuth wire aliases for Hermes tool names."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_WIRE_PREFIX = "mcp__"
_FORBIDDEN_SINGLE_MCP = re.compile(r"^mcp_[^_]")


@dataclass(frozen=True)
class ToolNameMaps:
    """Bidirectional tool identity for one Anthropic request."""

    forward: dict[str, str]
    reverse: dict[str, str]


def to_oauth_wire_name(name: str) -> str:
    """Return the empirically accepted Anthropic OAuth alias for *name*."""

    if not isinstance(name, str) or not name:
        raise ValueError("Tool names must be non-empty strings")
    if name.startswith(_WIRE_PREFIX):
        wire_name = name
    elif name.startswith("mcp_"):
        wire_name = _WIRE_PREFIX + name[len("mcp_") :]
    else:
        wire_name = _WIRE_PREFIX + name
    if _FORBIDDEN_SINGLE_MCP.match(wire_name):
        raise ValueError(
            f"OAuth tool alias {wire_name!r} uses the forbidden single-underscore mcp_ form"
        )
    return wire_name


def _iter_tool_names(tools: Iterable[dict[str, Any]] | None) -> Iterable[str]:
    for tool in tools or ():
        if not isinstance(tool, dict):
            raise ValueError("Tool definitions must be dictionaries")
        function = tool.get("function")
        if isinstance(function, dict):
            name = function.get("name")
        else:
            # Accept an Anthropic-format schema for diagnostics and probes.
            name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Every tool definition must contain a non-empty name")
        yield name


def build_tool_name_maps(
    tools: Iterable[dict[str, Any]] | None,
) -> ToolNameMaps:
    """Build collision-checked maps for the enabled request tool set."""

    forward: dict[str, str] = {}
    reverse: dict[str, str] = {}
    for internal_name in _iter_tool_names(tools):
        if internal_name in forward:
            continue
        wire_name = to_oauth_wire_name(internal_name)
        previous = reverse.get(wire_name)
        if previous is not None and previous != internal_name:
            raise ValueError(
                "Anthropic OAuth tool alias collision: "
                f"{previous!r} and {internal_name!r} both map to {wire_name!r}"
            )
        forward[internal_name] = wire_name
        reverse[wire_name] = internal_name
    return ToolNameMaps(forward=forward, reverse=reverse)


def assert_plan_safe_wire_name(name: str) -> None:
    """Raise locally if a request would expose a forbidden MCP name."""

    if not isinstance(name, str) or not name:
        raise ValueError("Anthropic wire tool names must be non-empty strings")
    if _FORBIDDEN_SINGLE_MCP.match(name):
        raise ValueError(
            f"Anthropic OAuth request contains forbidden wire tool name {name!r}"
        )
