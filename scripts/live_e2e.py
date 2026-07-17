#!/usr/bin/env python3
"""Live Anthropic OAuth acceptance matrix for the installed Hermes plugin.

This script intentionally uses Hermes's real AIAgent and configured credentials.
It prints no credential or request-header data.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL = "claude-opus-4-8"
PROVIDER = "anthropic"
EXPECTED_PLUGIN = "anthropic-subscription-provider"
REPO_PATH = Path(__file__).resolve().parents[1]
DEFAULT_HERMES_PATH = Path(
    "/Users/evgenii/Desktop/My projects/hermes-agent/.worktrees/plugin-e2e"
)


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str
    api_calls: int = 0
    tools: tuple[str, ...] = ()
    history: list[dict[str, Any]] | None = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="required acknowledgement that live Anthropic requests will run",
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=(
            "no-tool",
            "full-tools",
            "skills-list",
            "skill-manage",
            "read-file",
            "terminal",
            "terminal-spaced-path",
            "synthetic-mcp",
            "replay",
            "auxiliary",
        ),
        help="run only this case; repeat to select multiple cases",
    )
    parser.add_argument(
        "--hermes-path",
        type=Path,
        default=DEFAULT_HERMES_PATH,
        help="absolute existing path used by terminal and read-file cases",
    )
    parser.add_argument("--model", default=MODEL)
    return parser.parse_args()


def _tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            if isinstance(call, dict):
                calls.append(call)
    return calls


def _tool_names(messages: list[dict[str, Any]]) -> tuple[str, ...]:
    names: list[str] = []
    for call in _tool_calls(messages):
        name = (call.get("function") or {}).get("name")
        if isinstance(name, str):
            names.append(name)
    return tuple(names)


def _tool_arguments(messages: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    arguments: list[dict[str, Any]] = []
    for call in _tool_calls(messages):
        function = call.get("function") or {}
        if function.get("name") != name:
            continue
        raw = function.get("arguments") or "{}"
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            arguments.append(value)
    return arguments


def _tool_outputs(messages: list[dict[str, Any]]) -> list[str]:
    outputs: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str):
            outputs.append(content)
    return outputs


def _make_agent(model: str, toolsets: list[str]):
    from agent.anthropic_adapter import resolve_anthropic_token
    from run_agent import AIAgent

    token = resolve_anthropic_token()
    if not token:
        raise RuntimeError(
            "Hermes could not resolve an Anthropic setup token; run hermes setup first"
        )
    return AIAgent(
        provider=PROVIDER,
        api_mode="anthropic_messages",
        api_key=token,
        model=model,
        max_iterations=12,
        tool_delay=0,
        enabled_toolsets=toolsets,
        quiet_mode=True,
        tool_progress_mode="none",
        skip_context_files=True,
        load_soul_identity=False,
        skip_memory=True,
        checkpoints_enabled=False,
    )


def _run_agent_case(
    name: str,
    *,
    model: str,
    toolsets: list[str],
    prompt: str,
    require_tools: set[str] | None = None,
    history: list[dict[str, Any]] | None = None,
    validator: Callable[[list[dict[str, Any]], str], str | None] | None = None,
) -> CaseResult:
    agent = _make_agent(model, toolsets)
    result = agent.run_conversation(prompt, conversation_history=history or [])
    messages = result.get("messages") or []
    final = str(result.get("final_response") or "")
    names = _tool_names(messages)
    error = str(result.get("error") or "")
    passed = bool(final) and not bool(result.get("failed"))
    detail = "ok"
    if not passed:
        detail = error or "empty or failed agent response"
    if passed and require_tools and not require_tools.issubset(set(names)):
        passed = False
        detail = f"missing internal tool call(s): {sorted(require_tools - set(names))}"
    if passed and any(tool.startswith("mcp__") for tool in names):
        passed = False
        detail = "wire alias leaked into Hermes internal dispatch history"
    if passed and validator:
        validation_error = validator(messages, final)
        if validation_error:
            passed = False
            detail = validation_error
    return CaseResult(
        name=name,
        passed=passed,
        detail=detail,
        api_calls=int(result.get("api_calls") or 0),
        tools=names,
        history=messages,
    )


def _terminal_validator(
    expected_path: Path, marker: str
) -> Callable[[list[dict[str, Any]], str], str | None]:
    def validate(messages: list[dict[str, Any]], _final: str) -> str | None:
        calls = _tool_arguments(messages, "terminal")
        if not calls:
            return "terminal arguments were not preserved in history"
        if not any(call.get("workdir") == str(expected_path) for call in calls):
            return f"terminal workdir did not round-trip exactly: {expected_path}"
        if not any(marker in str(call.get("command", "")) for call in calls):
            return "terminal command marker was changed before dispatch"
        outputs = _tool_outputs(messages)
        if not any(marker in output for output in outputs):
            return "terminal output marker is missing"
        if not any(str(expected_path) in output for output in outputs):
            return "terminal output does not contain the expected cwd"
        return None

    return validate


def _run_no_tool(model: str, _path: Path) -> CaseResult:
    return _run_agent_case(
        "no-tool",
        model=model,
        toolsets=[],
        prompt="Reply with exactly LIVE_NO_TOOL_OK and nothing else.",
        validator=lambda _m, final: None if "LIVE_NO_TOOL_OK" in final else "marker missing",
    )


def _run_full_tools(model: str, _path: Path) -> CaseResult:
    return _run_agent_case(
        "full-tools",
        model=model,
        toolsets=["hermes-cli"],
        prompt=(
            "Do not call a tool. Confirm that you can see the available tools by "
            "replying with exactly LIVE_FULL_TOOLS_OK."
        ),
        validator=lambda _m, final: None if "LIVE_FULL_TOOLS_OK" in final else "marker missing",
    )


def _run_skills_list(model: str, _path: Path) -> CaseResult:
    return _run_agent_case(
        "skills-list",
        model=model,
        toolsets=["skills"],
        prompt=(
            "Call skills_list exactly once. After the tool returns, reply with "
            "LIVE_SKILLS_LIST_OK."
        ),
        require_tools={"skills_list"},
        validator=lambda _m, final: None if "LIVE_SKILLS_LIST_OK" in final else "marker missing",
    )


def _run_skill_manage(model: str, _path: Path) -> CaseResult:
    from hermes_constants import get_hermes_home

    skill_name = f"oauth-live-check-{uuid.uuid4().hex[:8]}"
    skill_root = Path(get_hermes_home()) / "skills" / skill_name
    prompt = (
        "I explicitly confirm creation of a temporary diagnostic skill. Call "
        "skill_manage exactly once with action='create', name='"
        f"{skill_name}', and complete SKILL.md content whose description and body "
        "say it is a temporary OAuth live check. After success reply with "
        "LIVE_SKILL_MANAGE_OK."
    )
    try:
        return _run_agent_case(
            "skill-manage",
            model=model,
            toolsets=["skills"],
            prompt=prompt,
            require_tools={"skill_manage"},
            validator=lambda _m, final: (
                None
                if skill_root.joinpath("SKILL.md").is_file()
                and "LIVE_SKILL_MANAGE_OK" in final
                else "temporary skill was not created or marker is missing"
            ),
        )
    finally:
        shutil.rmtree(skill_root, ignore_errors=True)


def _run_read_file(model: str, path: Path) -> CaseResult:
    target = path / "plugin.yaml"
    if not target.is_file():
        target = REPO_PATH / "plugin.yaml"
    return _run_agent_case(
        "read-file",
        model=model,
        toolsets=["file"],
        prompt=(
            f"Call read_file exactly once for this absolute path: {target}. "
            "Then reply with LIVE_READ_FILE_OK."
        ),
        require_tools={"read_file"},
        validator=lambda outputs, final: (
            None
            if any(str(target) == str(a.get("path")) for a in _tool_arguments(outputs, "read_file"))
            and "LIVE_READ_FILE_OK" in final
            else "read_file path did not round-trip or marker is missing"
        ),
    )


def _run_terminal(model: str, path: Path) -> CaseResult:
    marker = "LIVE_TERMINAL_HERMES_PATH_OK"
    if not path.is_dir():
        return CaseResult("terminal", False, f"missing --hermes-path: {path}")
    return _run_agent_case(
        "terminal",
        model=model,
        toolsets=["terminal"],
        prompt=(
            "Call terminal exactly once with command "
            f"`pwd && printf '{marker}\\n'` and workdir exactly `{path}`. "
            f"After exit code zero, reply with {marker}."
        ),
        require_tools={"terminal"},
        validator=_terminal_validator(path, marker),
    )


def _run_terminal_spaced_path(model: str, _path: Path) -> CaseResult:
    marker = "LIVE_TERMINAL_SPACED_PATH_OK"
    path = Path(tempfile.mkdtemp(prefix="Hermes Agent OAuth Path "))
    try:
        return _run_agent_case(
            "terminal-spaced-path",
            model=model,
            toolsets=["terminal"],
            prompt=(
                "Call terminal exactly once with command "
                f"`pwd && printf '{marker}\\n'` and workdir exactly `{path}`. "
                f"After exit code zero, reply with {marker}."
            ),
            require_tools={"terminal"},
            validator=_terminal_validator(path, marker),
        )
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _run_synthetic_mcp(model: str, _path: Path) -> CaseResult:
    from tools.registry import registry

    name = "mcp_diag_echo"
    marker = "LIVE_SYNTHETIC_MCP_OK"

    def handler(args: dict[str, Any], **_kwargs: Any) -> str:
        return json.dumps({"echo": args.get("text"), "marker": marker})

    registry.register(
        name=name,
        toolset="mcp-oauth-live",
        schema={
            "name": name,
            "description": "Return an OAuth live-test marker and echo input.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
        handler=handler,
    )
    try:
        return _run_agent_case(
            "synthetic-mcp",
            model=model,
            toolsets=["mcp-oauth-live"],
            prompt=(
                f"Call {name} exactly once with text='round-trip'. After the tool "
                f"returns, reply with {marker}."
            ),
            require_tools={name},
            validator=lambda messages, final: (
                None
                if any(marker in output for output in _tool_outputs(messages))
                and marker in final
                else "synthetic MCP dispatch or marker failed"
            ),
        )
    finally:
        registry.deregister(name)


def _run_replay(model: str, _path: Path) -> CaseResult:
    from tools.registry import registry

    name = "mcp_replay_echo"
    first_marker = "LIVE_REPLAY_FIRST_OK"
    second_marker = "LIVE_REPLAY_SECOND_OK"

    def handler(args: dict[str, Any], **_kwargs: Any) -> str:
        return json.dumps({"value": args.get("value"), "marker": first_marker})

    registry.register(
        name=name,
        toolset="mcp-oauth-replay",
        schema={
            "name": name,
            "description": "Return a replay-test marker.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
        handler=handler,
    )
    try:
        first = _run_agent_case(
            "replay-first",
            model=model,
            toolsets=["mcp-oauth-replay"],
            prompt=(
                f"Call {name} exactly once with value='persisted'. Then reply with "
                f"{first_marker}."
            ),
            require_tools={name},
        )
        if not first.passed or not first.history:
            return CaseResult(
                "replay", False, f"first turn failed: {first.detail}", first.api_calls, first.tools
            )
        second = _run_agent_case(
            "replay",
            model=model,
            toolsets=["mcp-oauth-replay"],
            prompt=(
                "Do not call a tool. Use the prior tool result in conversation "
                f"history and reply with exactly {second_marker}."
            ),
            history=first.history,
            validator=lambda _m, final: (
                None if second_marker in final else "second-turn replay marker missing"
            ),
        )
        second.api_calls += first.api_calls
        second.tools = first.tools + second.tools
        return second
    finally:
        registry.deregister(name)


def _run_auxiliary(model: str, _path: Path) -> CaseResult:
    from agent.anthropic_adapter import resolve_anthropic_token
    from agent.auxiliary_client import call_llm

    token = resolve_anthropic_token()
    if not token:
        return CaseResult("auxiliary", False, "Anthropic setup token was not resolved")
    try:
        response = call_llm(
            task="title_generation",
            provider=PROVIDER,
            model=model,
            api_key=token,
            api_mode="anthropic_messages",
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly LIVE_AUXILIARY_OK and nothing else.",
                }
            ],
            max_tokens=32,
            timeout=90,
        )
        content = str(response.choices[0].message.content or "")
        passed = "LIVE_AUXILIARY_OK" in content
        return CaseResult(
            "auxiliary",
            passed,
            "ok" if passed else "auxiliary marker missing",
            api_calls=1,
        )
    except Exception as exc:
        return CaseResult("auxiliary", False, f"{type(exc).__name__}: {exc}")


CASES: dict[str, Callable[[str, Path], CaseResult]] = {
    "no-tool": _run_no_tool,
    "full-tools": _run_full_tools,
    "skills-list": _run_skills_list,
    "skill-manage": _run_skill_manage,
    "read-file": _run_read_file,
    "terminal": _run_terminal,
    "terminal-spaced-path": _run_terminal_spaced_path,
    "synthetic-mcp": _run_synthetic_mcp,
    "replay": _run_replay,
    "auxiliary": _run_auxiliary,
}


def _verify_plugin_active() -> None:
    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    discover_plugins()
    manager = get_plugin_manager()
    plugins = getattr(manager, "_plugins", {})
    if EXPECTED_PLUGIN not in plugins:
        raise RuntimeError(
            f"{EXPECTED_PLUGIN} is not loaded from the active HERMES_HOME"
        )
    from agent.transports import get_transport

    transport = get_transport("anthropic_messages")
    if not getattr(type(transport), "_anthropic_subscription_provider", False):
        raise RuntimeError("Anthropic subscription transport override is not active")


def main() -> int:
    args = _parse_args()
    if not args.confirm_live:
        print("REFUSED: pass --confirm-live to make real Anthropic requests", file=sys.stderr)
        return 2
    if not args.hermes_path.is_absolute():
        print("REFUSED: --hermes-path must be absolute", file=sys.stderr)
        return 2

    # Prevent accidental safe-mode execution, which would skip the plugin.
    if os.getenv("HERMES_SAFE_MODE", "").lower() in {"1", "true", "yes", "on"}:
        print("REFUSED: HERMES_SAFE_MODE disables plugins", file=sys.stderr)
        return 2

    try:
        _verify_plugin_active()
    except Exception as exc:
        print(f"PLUGIN_FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    selected = args.case or list(CASES)
    failures = 0
    print(f"PLUGIN_ACTIVE model={args.model} cases={len(selected)}")
    for name in selected:
        try:
            result = CASES[name](args.model, args.hermes_path.resolve())
        except Exception as exc:
            result = CaseResult(name, False, f"{type(exc).__name__}: {exc}")
        status = "PASS" if result.passed else "FAIL"
        tools = ",".join(result.tools) if result.tools else "-"
        # detail is deliberately bounded and carries no request or credential data.
        detail = result.detail.replace("\n", " ")[:300]
        print(
            f"{status} case={result.name} api_calls={result.api_calls} "
            f"tools={tools} detail={detail}"
        )
        failures += int(not result.passed)

    print(f"SUMMARY passed={len(selected) - failures} failed={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
