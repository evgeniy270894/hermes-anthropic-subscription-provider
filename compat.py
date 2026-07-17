"""Host compatibility probes and transport registration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_MIN_HERMES_VERSION = "0.18.2"
_TESTED_HERMES_VERSION = "0.18.2"


@dataclass(frozen=True)
class HostCompatibility:
    supported: bool
    hermes_version: str
    error: str = ""
    warning: str = ""


def _parse_version(value: str):
    from packaging.version import InvalidVersion, Version

    try:
        return Version(value)
    except InvalidVersion:
        return None


def inspect_host(base_transport: Any) -> HostCompatibility:
    """Feature-probe the Hermes transport contract without making a request."""

    try:
        from hermes_cli import __version__ as hermes_version
    except Exception:
        hermes_version = "unknown"

    parsed = _parse_version(hermes_version)
    minimum = _parse_version(_MIN_HERMES_VERSION)
    if parsed is None:
        return HostCompatibility(
            supported=False,
            hermes_version=hermes_version,
            error=f"could not parse Hermes version {hermes_version!r}",
        )
    if minimum is not None and parsed < minimum:
        return HostCompatibility(
            supported=False,
            hermes_version=hermes_version,
            error=(
                f"Hermes {hermes_version} is older than required "
                f"{_MIN_HERMES_VERSION}"
            ),
        )

    for method_name in ("build_kwargs", "normalize_response"):
        if not callable(getattr(base_transport, method_name, None)):
            return HostCompatibility(
                supported=False,
                hermes_version=hermes_version,
                error=f"Anthropic transport has no callable {method_name}()",
            )

    try:
        probe_messages = [
            {"role": "system", "content": "Use skill_manage(action='list')."},
            {"role": "user", "content": "Reply OK."},
        ]
        probe = base_transport.build_kwargs(
            model="claude-sonnet-4-5",
            messages=probe_messages,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "skill_manage",
                        "description": "Probe",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            max_tokens=8,
            is_oauth=True,
        )
        probe_names = [
            tool.get("name")
            for tool in probe.get("tools", [])
            if isinstance(tool, dict)
        ]
        if probe_names != ["mcp__skill_manage"]:
            raise ValueError(f"unexpected OAuth alias probe result {probe_names!r}")

        from agent.anthropic_adapter import convert_messages_to_anthropic

        original_system, _ = convert_messages_to_anthropic(probe_messages)
        original_blocks = (
            original_system
            if isinstance(original_system, list)
            else ([{"type": "text", "text": original_system}] if original_system else [])
        )
        upstream_blocks = probe.get("system")
        if not isinstance(upstream_blocks, list):
            raise ValueError("OAuth system prompt is not a block list")
        if len(upstream_blocks) != len(original_blocks) + 1:
            raise ValueError(
                "OAuth system prompt does not contain exactly one "
                "upstream-added identity block"
            )
        identity = upstream_blocks[0]
        if (
            not isinstance(identity, dict)
            or identity.get("type") != "text"
            or not isinstance(identity.get("text"), str)
            or not identity["text"].strip()
        ):
            raise ValueError("OAuth identity block is not non-empty text")

        from agent.transports.types import ToolCall

        mutable_call = ToolCall(id="probe", name="mcp__probe", arguments="{}")
        mutable_call.name = "probe"
        if mutable_call.name != "probe":
            raise ValueError("normalized ToolCall.name is not mutable")
    except Exception as exc:
        return HostCompatibility(
            supported=False,
            hermes_version=hermes_version,
            error=f"Anthropic transport behavior probe failed: {exc}",
        )

    warning = ""
    if hermes_version != _TESTED_HERMES_VERSION:
        warning = (
            f"feature probes passed on untested Hermes {hermes_version}; "
            f"latest explicitly tested version is {_TESTED_HERMES_VERSION}"
        )
    return HostCompatibility(
        supported=True,
        hermes_version=hermes_version,
        warning=warning,
    )


def install_transport_override() -> HostCompatibility:
    """Install the transport override once and return compatibility status."""

    from agent.transports import get_transport, register_transport

    base_transport = get_transport("anthropic_messages")
    if base_transport is None:
        raise RuntimeError("Hermes did not register an anthropic_messages transport")

    if getattr(type(base_transport), "_anthropic_subscription_provider", False):
        error = getattr(
            type(base_transport),
            "_anthropic_subscription_compatibility_error",
            "",
        )
        try:
            from hermes_cli import __version__ as hermes_version
        except Exception:
            hermes_version = "unknown"
        return HostCompatibility(
            supported=not bool(error),
            hermes_version=hermes_version,
            error=error,
        )

    compatibility = inspect_host(base_transport)
    from .transport import make_subscription_transport

    transport_class = make_subscription_transport(
        type(base_transport),
        compatibility_error=compatibility.error,
    )
    register_transport("anthropic_messages", transport_class)
    logger.debug(
        "Registered Anthropic subscription transport (supported=%s)",
        compatibility.supported,
    )
    return compatibility
