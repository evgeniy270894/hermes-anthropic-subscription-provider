from __future__ import annotations

import importlib


def _make_resolver(plugin_package):
    module = importlib.import_module(f"{plugin_package.__name__}.credential_guard")
    return module.make_suppression_aware_resolver


def test_unsuppressed_delegates_to_upstream(monkeypatch, plugin_package):
    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    wrapped = _make_resolver(plugin_package)(
        lambda: "claude-code-token",
        is_source_suppressed=lambda provider, source: False,
        pool_token_resolver=lambda: None,
    )
    assert wrapped() == "claude-code-token"


def test_suppressed_never_calls_upstream_or_uses_claude_env(monkeypatch, plugin_package):
    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "forbidden-token")

    def forbidden():
        raise AssertionError("Claude Code resolver must not run")

    wrapped = _make_resolver(plugin_package)(
        forbidden,
        is_source_suppressed=lambda provider, source: True,
        pool_token_resolver=lambda: None,
    )
    assert wrapped() is None


def test_suppressed_prefers_explicit_anthropic_token(monkeypatch, plugin_package):
    monkeypatch.setenv("ANTHROPIC_TOKEN", "explicit-setup-token")
    wrapped = _make_resolver(plugin_package)(
        lambda: "forbidden",
        is_source_suppressed=lambda provider, source: True,
        pool_token_resolver=lambda: "pool-token",
    )
    assert wrapped() == "explicit-setup-token"


def test_suppressed_uses_available_pool_token(monkeypatch, plugin_package):
    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    wrapped = _make_resolver(plugin_package)(
        lambda: "forbidden",
        is_source_suppressed=lambda provider, source: True,
        pool_token_resolver=lambda: "pool-token",
    )
    assert wrapped() == "pool-token"
