"""Syntax-aware alignment of system-prompt tool references."""

from __future__ import annotations

import re


def rewrite_explicit_tool_references(
    text: str,
    forward_map: dict[str, str],
) -> str:
    """Rewrite explicit tool identifiers without touching paths or prose.

    Hermes's OAuth schemas already use ``mcp__`` aliases. This function makes
    explicit instructions use the same aliases while deliberately avoiding a
    global word replacement that could corrupt URLs, paths, source examples,
    or ordinary uses of names such as ``terminal`` and ``memory``.
    """

    if not isinstance(text, str) or not text or not forward_map:
        return text

    ordered_names = sorted(forward_map, key=len, reverse=True)

    def identifier_pattern(internal_name: str) -> str:
        # Slash/dot exclusions protect paths and qualified-code components.
        return (
            rf"(?<![A-Za-z0-9_/]){re.escape(internal_name)}"
            rf"(?![A-Za-z0-9_./])"
        )

    # Explicit syntax is safe to replace wherever it occurs.
    for internal_name in ordered_names:
        wire_name = forward_map[internal_name]
        if not internal_name or internal_name == wire_name:
            continue
        text = text.replace(f"`{internal_name}`", f"`{wire_name}`")
        identifier = identifier_pattern(internal_name)
        text = re.sub(rf"{identifier}(?=\s*\()", wire_name, text)
        text = re.sub(
            rf"{identifier}(?=\s+tool\b)",
            wire_name,
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            rf"(?<=\btool\s){identifier}",
            wire_name,
            text,
            flags=re.IGNORECASE,
        )

    # Within a tool directive, listed known tools are explicit identifiers,
    # including single-word names such as ``terminal``.
    directive = re.compile(
        r"(?i)\b(?:use|using|call|invoke|run|work\s+with|via|through|"
        r"tools?\s+(?:like|include|including|are)|available\s+tools?\s*:)"
        r"[^.!?\n]{0,240}"
    )

    def rewrite_directive(match: re.Match[str]) -> str:
        segment = match.group(0)
        for internal_name in ordered_names:
            wire_name = forward_map[internal_name]
            if internal_name and internal_name != wire_name:
                segment = re.sub(
                    identifier_pattern(internal_name),
                    wire_name,
                    segment,
                )
        return segment

    return directive.sub(rewrite_directive, text)

