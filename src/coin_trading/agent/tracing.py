"""Opik tracing helpers for LangGraph agent runs.

Wrap the compiled LangGraph once at construction time via
``track_langgraph`` — every subsequent invoke is automatically traced.
No-op when OPIK_API_KEY is unset.
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
    except Exception as exc:
        logger.warning("[Opik] failed to initialize: %s", exc)
        return False


def wrap_graph_with_opik(app: Any, settings: Settings) -> Any:
    """Wrap a compiled LangGraph with Opik tracing.

    Returns the original ``app`` unchanged when Opik is disabled or the
    integration cannot be loaded — callers should always replace their
    handle with the return value.
    """
    if not configure_opik(settings):
        return app
    try:
        from opik.integrations.langchain import OpikTracer, track_langgraph

        tracer = OpikTracer(
            project_name=settings.opik_project_name,
            tags=["opentrading"],
        )
        return track_langgraph(app, tracer)
    except Exception as exc:
        logger.warning("[Opik] failed to wrap LangGraph: %s", exc)
        return app
