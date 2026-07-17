from __future__ import annotations

import importlib


def test_install_transport_override_is_active_and_idempotent(plugin_package):
    compat = importlib.import_module(f"{plugin_package.__name__}.compat")
    first = compat.install_transport_override()
    assert first.supported, first.error

    from agent.transports import get_transport

    installed = get_transport("anthropic_messages")
    assert getattr(type(installed), "_anthropic_subscription_provider", False)
    installed_class = type(installed)

    second = compat.install_transport_override()
    assert second.supported, second.error
    assert type(get_transport("anthropic_messages")) is installed_class


def test_plugin_entrypoint_registers_transport(plugin_package):
    plugin_package.register(object())
    from agent.transports import get_transport

    assert getattr(
        type(get_transport("anthropic_messages")),
        "_anthropic_subscription_provider",
        False,
    )

