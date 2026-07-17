"""Hermes plugin entry point for Anthropic subscription compatibility."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


def register(ctx) -> None:
    """Register the OAuth-aware Anthropic transport override.

    Hermes calls this function during normal plugin discovery. Imports stay
    lazy so plugin inspection and management commands do not eagerly load the
    agent/SDK stack.
    """

    from .compat import install_transport_override

    compatibility = install_transport_override()
    if compatibility.error:
        logger.error(
            "Anthropic subscription provider loaded in guarded mode: %s",
            compatibility.error,
        )
    elif compatibility.warning:
        logger.warning(
            "Anthropic subscription provider active (%s): %s",
            compatibility.hermes_version,
            compatibility.warning,
        )
    else:
        logger.info(
            "Anthropic subscription provider active (Hermes %s)",
            compatibility.hermes_version,
        )

