"""Anthropic transport wrapper for Claude subscription OAuth requests."""

from __future__ import annotations

import copy
from typing import Any

from .prompt_rewrite import rewrite_explicit_tool_references
from .tool_aliases import (
    ToolNameMaps,
    assert_plan_safe_wire_name,
    build_tool_name_maps,
)


def _as_system_blocks(system: Any) -> list[dict[str, Any]]:
    if isinstance(system, str):
        return [{"type": "text", "text": system}] if system else []
    if isinstance(system, list):
        return [copy.deepcopy(block) for block in system if isinstance(block, dict)]
    return []


def _restore_original_system(
    upstream_system: Any,
    original_system: Any,
) -> list[dict[str, Any]]:
    """Keep upstream's OAuth identity block and restore original system text."""

    upstream_blocks = _as_system_blocks(upstream_system)
    identity_blocks: list[dict[str, Any]] = []
    if upstream_blocks:
        first = upstream_blocks[0]
        first_text = first.get("text") if first.get("type") == "text" else None
        if isinstance(first_text, str) and "Claude Code" in first_text:
            identity_blocks.append(first)
    return identity_blocks + _as_system_blocks(original_system)


def _rewrite_system_blocks(
    system: Any,
    name_maps: ToolNameMaps,
) -> list[dict[str, Any]]:
    blocks = _as_system_blocks(system)
    for block in blocks:
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            block["text"] = rewrite_explicit_tool_references(
                block["text"], name_maps.forward
            )
    return blocks


def _validate_request_identity(
    kwargs: dict[str, Any],
    name_maps: ToolNameMaps,
) -> None:
    actual_tool_names: set[str] = set()
    for tool in kwargs.get("tools") or ():
        if not isinstance(tool, dict):
            raise ValueError("Anthropic tool schemas must be dictionaries")
        name = tool.get("name")
        assert_plan_safe_wire_name(name)
        actual_tool_names.add(name)

    expected_tool_names = set(name_maps.forward.values())
    if actual_tool_names != expected_tool_names:
        raise ValueError(
            "Hermes Anthropic tool alias behavior is incompatible with the "
            "subscription provider: "
            f"expected {sorted(expected_tool_names)!r}, got {sorted(actual_tool_names)!r}"
        )

    for message in kwargs.get("messages") or ():
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and "name" in block
            ):
                assert_plan_safe_wire_name(block["name"])


def make_subscription_transport(
    base_transport_class: type,
    *,
    compatibility_error: str = "",
) -> type:
    """Return the transport class registered for ``anthropic_messages``."""

    class SubscriptionAnthropicTransport(base_transport_class):
        _anthropic_subscription_provider = True
        _anthropic_subscription_compatibility_error = compatibility_error

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._oauth_wire_to_internal: dict[str, str] = {}
            self._last_request_was_oauth = False

        def build_kwargs(self, model, messages, tools=None, **params):
            is_oauth = bool(params.get("is_oauth", False))
            self._oauth_wire_to_internal = {}
            self._last_request_was_oauth = is_oauth

            if not is_oauth:
                return super().build_kwargs(
                    model=model,
                    messages=messages,
                    tools=tools,
                    **params,
                )

            if compatibility_error:
                raise RuntimeError(
                    "Anthropic subscription provider cannot safely shape this "
                    f"Hermes OAuth request: {compatibility_error}"
                )

            safe_messages = copy.deepcopy(messages)
            safe_tools = copy.deepcopy(tools) if tools is not None else None
            name_maps = build_tool_name_maps(safe_tools)

            # Convert the original system independently so we can retain the
            # upstream OAuth identity block without inheriting Hermes's broad
            # product-name substitutions.
            from agent.anthropic_adapter import convert_messages_to_anthropic

            original_system, _ = convert_messages_to_anthropic(
                copy.deepcopy(safe_messages),
                base_url=params.get("base_url"),
                model=model,
            )

            result = super().build_kwargs(
                model=model,
                messages=safe_messages,
                tools=safe_tools,
                **params,
            )
            result = copy.deepcopy(result)

            restored_system = _restore_original_system(
                result.get("system"), original_system
            )
            rewritten_system = _rewrite_system_blocks(restored_system, name_maps)
            if rewritten_system:
                result["system"] = rewritten_system
            else:
                result.pop("system", None)

            specific_choice = params.get("tool_choice")
            choice = result.get("tool_choice")
            if (
                isinstance(specific_choice, str)
                and specific_choice not in {"auto", "required", "none"}
                and isinstance(choice, dict)
                and choice.get("type") == "tool"
            ):
                if specific_choice not in name_maps.forward:
                    raise ValueError(
                        f"Specific tool_choice {specific_choice!r} is not "
                        "present in the request tools"
                    )
                choice["name"] = name_maps.forward[specific_choice]

            _validate_request_identity(result, name_maps)
            self._oauth_wire_to_internal = dict(name_maps.reverse)
            return result

        def normalize_response(self, response, **kwargs):
            normalize_kwargs = dict(kwargs)
            normalize_kwargs["strip_tool_prefix"] = False
            normalize_kwargs.pop("tool_name_map", None)
            normalized = super().normalize_response(response, **normalize_kwargs)
            if self._last_request_was_oauth and normalized.tool_calls:
                for tool_call in normalized.tool_calls:
                    tool_call.name = self._oauth_wire_to_internal.get(
                        tool_call.name,
                        tool_call.name,
                    )
            return normalized

    SubscriptionAnthropicTransport.__name__ = "SubscriptionAnthropicTransport"
    SubscriptionAnthropicTransport.__qualname__ = "SubscriptionAnthropicTransport"
    return SubscriptionAnthropicTransport
