"""LangSmith tracing bootstrap (Phase 4.5, task 4.5.1).

``configure_tracing()`` is the opt-in hook the orchestrator process calls once at
startup (Phase 4.6 FastAPI app; the eval harness; tests). Tracing is **active only when**
``LANGSMITH_TRACING`` is true AND a ``LANGSMITH_API_KEY`` is set — otherwise it is forced
*off* so no run ever attempts network egress to smith.langchain.com. This preserves the
clean-checkout invariant: with no key, the graph runs untraced rather than erroring.

LangGraph/LangChain emit the per-node child-run tree automatically once these env vars are
set, so this module deliberately does NO manual span instrumentation (node bodies stay
untouched — consistent with the Phase 4.4 "edges only" rule). It only normalizes the env
LangChain reads and reports whether tracing is active. Idempotent; never raises.
"""

from __future__ import annotations

import os

from estc.shared.config import Settings


def configure_tracing() -> bool:
    """Enable LangSmith tracing iff configured. Returns ``True`` when tracing is active.

    Sets both the modern ``LANGSMITH_*`` names and the ``LANGCHAIN_*`` v2 aliases so the
    handler is picked up regardless of the installed langchain/langsmith version.
    """
    settings = Settings()
    active = bool(settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY)
    if active:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
        os.environ["LANGCHAIN_PROJECT"] = settings.LANGSMITH_PROJECT
        os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY  # type: ignore[assignment]
    else:
        # Force off so a stray env var can't trigger an egress attempt with no key.
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
    return active
