"""Opik tracing helpers for LangGraph agent runs.

Each LangGraph node becomes a span; per-node latency, inputs, and outputs
are visible in the Opik dashboard. No-op when OPIK_API_KEY is unset.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from coin_trading.config import Settings

logger = logging.getLogger(__name__)

_configured = False


def configure_opik(settings: Settings) -> bool:
    """Configure Opik client once per process. Returns True if active."""
    global _configured
    if _configured:
        return bool(settings.opik_api_key)
    if not settings.opik_api_key:
        return False

    os.environ.setdefault("OPIK_API_KEY", settings.opik_api_key)
    if settings.opik_workspace:
        os.environ.setdefault("OPIK_WORKSPACE", settings.opik_workspace)
    os.environ.setdefault("OPIK_PROJECT_NAME", settings.opik_project_name)

    try:
        import opik  # noqa: F401  (import side-effect: reads env)

        _configured = True
        logger.info(
            "[Opik] tracing enabled (project=%s workspace=%s)",
            settings.opik_project_name,
            settings.opik_workspace or "default",
        )
        return True
    except Exception as exc:  # opik install/auth failure must not crash trading
        logger.warning("[Opik] failed to initialize: %s", exc)
        return False


def get_langgraph_callbacks(settings: Settings) -> list[Any]:
    """Return LangChain-compatible callbacks for LangGraph invoke().

    Empty list when Opik is disabled.
    """
    if not configure_opik(settings):
        return []
    try:
        from opik.integrations.langchain import OpikTracer

        return [OpikTracer(project_name=settings.opik_project_name)]
    except Exception as exc:
        logger.warning("[Opik] OpikTracer unavailable: %s", exc)
        return []
