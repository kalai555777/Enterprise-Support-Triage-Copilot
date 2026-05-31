"""Ragas import compatibility shim (Phase 4.5.0, Risk 1).

The installed ``ragas`` (0.4.3) imports ``langchain_community.chat_models.vertexai``
(``ChatVertexAI``), a module removed in ``langchain-community 0.4.2`` — the version the
RAG pipeline depends on (``ingest.py``). Downgrading langchain-community to restore that
module would risk the working RAG stack, so instead we register an **inert stub** for the
removed module *before* ragas is imported. ``ChatVertexAI`` is never instantiated here
(the Ragas judge is selected from the Anthropic/OpenAI ladder), so the stub only needs to
satisfy the import.

``ensure_ragas_importable()`` is idempotent and must be called before ``import ragas``.
"""

from __future__ import annotations

import sys
import types

# Modules ragas may import from langchain_community that no longer exist in 0.4.x.
_MISSING_MODULES = ("langchain_community.chat_models.vertexai",)
_STUB_ATTRS = ("ChatVertexAI",)


def ensure_ragas_importable() -> None:
    """Register inert stub modules so ``import ragas`` resolves on langchain-community 0.4.x."""
    for mod_name in _MISSING_MODULES:
        if mod_name in sys.modules:
            continue
        stub = types.ModuleType(mod_name)
        for attr in _STUB_ATTRS:
            setattr(stub, attr, object)  # placeholder; never instantiated
        sys.modules[mod_name] = stub
