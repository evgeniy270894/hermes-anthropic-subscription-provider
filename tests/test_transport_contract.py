from __future__ import annotations

import copy
import importlib
from types import SimpleNamespace

import pytest


def _function_tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Tool {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _transport(plugin_package):
    from agent.transports.anthropic import AnthropicTransport

    module = importlib.import_module(f"{plugin_package.__name__}.transport")
    return module.make_subscription_transport(AnthropicTransport)()


def test_oauth_request_aligns_schema_prompt_and_specific_choice(plugin_package):
    transport = _transport(plugin_package)
    messages = [
        {
            "role": "system",
            "content": (
                "Hermes Agent docs: https://hermes-agent.nousresearch.com/docs\n"
                "Use skill_manage(action='patch') and `skills_list`.\n"
                "Path: /Users/evgenii/Desktop/My projects/hermes-agent/tests/read_file.py"
            ),
        },
        {"role": "user", "content": "Run the requested tool."},
    ]
    tools = [_function_tool("skill_manage"), _function_tool("skills_list")]
    original_messages = copy.deepcopy(messages)
    original_tools = copy.deepcopy(tools)

    kwargs = transport.build_kwargs(
        model="claude-opus-4-8",
        messages=messages,
        tools=tools,
        max_tokens=64,
        is_oauth=True,
        tool_choice="skills_list",
    )

    assert [tool["name"] for tool in kwargs["tools"]] == [
        "mcp__skill_manage",
        "mcp__skills_list",
    ]
    system_text = "\n".join(
        block.get("text", "") for block in kwargs["system"] if isinstance(block, dict)
    )
    assert "mcp__skill_manage(action='patch')" in system_text
    assert "`mcp__skills_list`" in system_text
    assert "Hermes Agent docs: https://hermes-agent.nousresearch.com/docs" in system_text
    assert "/hermes-agent/tests/read_file.py" in system_text
    assert kwargs["tool_choice"] == {"type": "tool", "name": "mcp__skills_list"}
    assert messages == original_messages
    assert tools == original_tools


def test_non_oauth_delegates_exactly(plugin_package):
    from agent.transports.anthropic import AnthropicTransport

    transport = _transport(plugin_package)
    base = AnthropicTransport()
    arguments = {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "OK"}],
        "tools": [_function_tool("read_file")],
        "max_tokens": 32,
        "is_oauth": False,
    }
    assert transport.build_kwargs(**copy.deepcopy(arguments)) == base.build_kwargs(
        **copy.deepcopy(arguments)
    )


def test_oauth_tool_choice_none_omits_schemas_without_alias_validation(plugin_package):
    transport = _transport(plugin_package)
    kwargs = transport.build_kwargs(
        model="claude-opus-4-8",
        messages=[
            {"role": "system", "content": "Use read_file(path='/tmp/check')."},
            {"role": "user", "content": "Do not use a tool."},
        ],
        tools=[_function_tool("read_file")],
        max_tokens=32,
        is_oauth=True,
        tool_choice="none",
    )
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs
    system_text = "\n".join(block.get("text", "") for block in kwargs["system"])
    assert "read_file(path='/tmp/check')" in system_text
    assert "mcp__read_file" not in system_text


def test_oauth_identity_is_preserved_structurally_and_drift_fails_closed(
    plugin_package,
):
    module = importlib.import_module(f"{plugin_package.__name__}.transport")
    original = [{"type": "text", "text": "Original system"}]
    future_identity = {"type": "text", "text": "Future upstream OAuth identity"}
    assert module._restore_original_system([future_identity, *original], original) == [
        future_identity,
        *original,
    ]
    with pytest.raises(RuntimeError, match="exactly one"):
        module._restore_original_system(original, original)


def test_response_tool_name_round_trips_through_exact_request_map(plugin_package):
    transport = _transport(plugin_package)
    transport.build_kwargs(
        model="claude-opus-4-8",
        messages=[{"role": "user", "content": "List skills"}],
        tools=[_function_tool("skills_list")],
        max_tokens=64,
        is_oauth=True,
    )
    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="toolu_probe",
                name="mcp__skills_list",
                input={},
            )
        ],
        stop_reason="tool_use",
    )
    normalized = transport.normalize_response(response, strip_tool_prefix=True)
    assert normalized.tool_calls[0].name == "skills_list"
    assert normalized.tool_calls[0].id == "toolu_probe"


def test_replayed_native_mcp_name_is_plan_safe(plugin_package):
    transport = _transport(plugin_package)
    kwargs = transport.build_kwargs(
        model="claude-opus-4-8",
        messages=[
            {"role": "user", "content": "Probe"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "toolu_old",
                        "type": "function",
                        "function": {
                            "name": "mcp_diag_echo",
                            "arguments": '{"text":"probe"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_old", "content": "ok"},
            {"role": "user", "content": "Continue"},
        ],
        tools=[_function_tool("mcp_diag_echo")],
        max_tokens=64,
        is_oauth=True,
    )
    tool_use_names = [
        block["name"]
        for message in kwargs["messages"]
        for block in (message.get("content") if isinstance(message.get("content"), list) else [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    assert tool_use_names == ["mcp__diag_echo"]


def test_collision_is_rejected_before_upstream_request(plugin_package):
    transport = _transport(plugin_package)
    with pytest.raises(ValueError, match="collision"):
        transport.build_kwargs(
            model="claude-opus-4-8",
            messages=[{"role": "user", "content": "Probe"}],
            tools=[_function_tool("diag_echo"), _function_tool("mcp_diag_echo")],
            max_tokens=64,
            is_oauth=True,
        )
