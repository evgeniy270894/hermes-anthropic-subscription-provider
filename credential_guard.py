"""Prevent suppressed Claude Code credentials from bypassing Hermes pools."""

from __future__ import annotations

import os
from collections.abc import Callable

_GUARD_MARKER = "_anthropic_subscription_claude_suppression_guard"


def make_suppression_aware_resolver(
    original: Callable[[], str | None],
    *,
    is_source_suppressed: Callable[[str, str], bool],
    pool_token_resolver: Callable[[], str | None],
) -> Callable[[], str | None]:
    """Wrap Hermes token resolution while preserving unsuppressed behavior."""

    def resolve() -> str | None:
        if not is_source_suppressed("anthropic", "claude_code"):
            return original()

        token = os.getenv("ANTHROPIC_TOKEN", "").strip()
        if token:
            return token

        pool_token = (pool_token_resolver() or "").strip()
        if pool_token:
            return pool_token

        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        return api_key or None

    setattr(resolve, _GUARD_MARKER, True)
    resolve.__wrapped__ = original
    return resolve


def install_claude_code_suppression_guard() -> bool:
    """Install the wrapper once; return True only on first installation."""

    import agent.anthropic_adapter as adapter
    from hermes_cli.auth import is_source_suppressed

    current = adapter.resolve_anthropic_token
    if getattr(current, _GUARD_MARKER, False):
        return False

    pool_resolver = getattr(adapter, "_resolve_anthropic_pool_token", None)
    if not callable(pool_resolver):
        raise RuntimeError(
            "Hermes Anthropic resolver has no callable _resolve_anthropic_pool_token"
        )

    adapter.resolve_anthropic_token = make_suppression_aware_resolver(
        current,
        is_source_suppressed=is_source_suppressed,
        pool_token_resolver=pool_resolver,
    )
    return True
