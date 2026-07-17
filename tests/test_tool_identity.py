from __future__ import annotations

import importlib

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


def test_bidirectional_aliases_cover_native_mcp_and_safe_names(plugin_package):
    aliases = importlib.import_module(f"{plugin_package.__name__}.tool_aliases")
    maps = aliases.build_tool_name_maps(
        [
            _function_tool("skill_manage"),
            _function_tool("read_file"),
            _function_tool("mcp_linear_get_issue"),
            _function_tool("mcp__already_safe"),
        ]
    )
    assert maps.forward == {
        "skill_manage": "mcp__skill_manage",
        "read_file": "mcp__read_file",
        "mcp_linear_get_issue": "mcp__linear_get_issue",
        "mcp__already_safe": "mcp__already_safe",
    }
    assert maps.reverse["mcp__linear_get_issue"] == "mcp_linear_get_issue"


def test_alias_collisions_fail_locally(plugin_package):
    aliases = importlib.import_module(f"{plugin_package.__name__}.tool_aliases")
    with pytest.raises(ValueError, match="collision"):
        aliases.build_tool_name_maps(
            [_function_tool("linear_get_issue"), _function_tool("mcp_linear_get_issue")]
        )


def test_explicit_prompt_rewrite_preserves_paths_urls_and_prose(plugin_package):
    rewrite = importlib.import_module(f"{plugin_package.__name__}.prompt_rewrite")
    mapping = {
        "skill_manage": "mcp__skill_manage",
        "skill_view": "mcp__skill_view",
        "skills_list": "mcp__skills_list",
        "read_file": "mcp__read_file",
        "terminal": "mcp__terminal",
    }
    text = (
        "Use skill_manage(action='patch'), `skill_view`, or the skills_list tool.\n"
        "Available tools: read_file or terminal\n"
        "Docs: https://hermes-agent.nousresearch.com/read_file\n"
        "Query: https://example.test/?tool=read_file(mode=raw)\n"
        "Shell: `bash -lc \"read_file(mode=raw)\"`\n"
        "Path: /Users/evgenii/Desktop/My projects/hermes-agent/tests/read_file.py\n"
        "The terminal window and read_file.py example are ordinary prose."
    )
    result = rewrite.rewrite_explicit_tool_references(text, mapping)
    assert "mcp__skill_manage(action='patch')" in result
    assert "`mcp__skill_view`" in result
    assert "mcp__skills_list tool" in result
    assert "Available tools: mcp__read_file or mcp__terminal" in result
    assert "https://hermes-agent.nousresearch.com/read_file" in result
    assert "https://example.test/?tool=read_file(mode=raw)" in result
    assert '`bash -lc "read_file(mode=raw)"`' in result
    assert "/hermes-agent/tests/read_file.py" in result
    assert "The terminal window and read_file.py example" in result


def test_tool_arguments_are_not_part_of_identity_mapping(plugin_package):
    aliases = importlib.import_module(f"{plugin_package.__name__}.tool_aliases")
    command = "bash -lc 'pwd'"
    workdir = "/tmp/Hermes Agent OAuth Path/hermes-agent"
    tool = _function_tool("terminal")
    tool["function"]["parameters"]["example"] = {
        "command": command,
        "workdir": workdir,
    }
    aliases.build_tool_name_maps([tool])
    assert tool["function"]["parameters"]["example"] == {
        "command": command,
        "workdir": workdir,
    }
