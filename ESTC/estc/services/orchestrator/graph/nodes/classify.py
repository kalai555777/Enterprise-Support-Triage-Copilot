"""`classify` node (Phase 4.3, task 4.3.1).

Calls the local PyTorch classifier API and writes ``state.intent`` plus a seeded
``state.confidence_score``. Intent classification is done by the local FastAPI
service, never an LLM (design.md Component A). The ``client`` keyword is injectable
so tests can supply an ``httpx.MockTransport`` and make no real network call.
"""

from __future__ import annotations

from typing import Optional

import httpx

from estc.shared.config import Settings
from estc.shared.schemas.agent_state import AgentState


async def classify(
    state: AgentState,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, object]:
    base = Settings().CLASSIFIER_API_URL
    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base, timeout=5.0)
    try:
        resp = await client.post("/classify", json={"text": state.raw_issue_text})
        resp.raise_for_status()
        body = resp.json()
    finally:
        if owns_client:
            await client.aclose()

    intent = body["intent"]
    confidence = float(body["confidence"])
    return {
        "intent": intent,
        "confidence_score": confidence,
        "execution_logs": state.execution_logs + [f"classified:{intent}"],
    }
