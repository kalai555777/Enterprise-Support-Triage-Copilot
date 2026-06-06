"""`classify` node (Phase 4.3, task 4.3.1).

Calls the local PyTorch classifier API and writes ``state.intent`` plus a seeded
``state.confidence_score``. Intent classification is done by the local FastAPI
service, never an LLM (design.md Component A). The ``client`` keyword is injectable
so tests can supply an ``httpx.MockTransport`` and make no real network call.

Resilience: the call is retried a few times with backoff. If the classifier stays
unreachable, the node degrades gracefully — it writes a ``0.0`` confidence so the
downstream worker + supervisor escalate the ticket to a human rather than crashing
the whole run on a transient outage.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from estc.shared.config import Settings
from estc.shared.schemas.agent_state import AgentState

logger = logging.getLogger("estc.orchestrator.classify")

# Bounded retry for a flaky/restarting classifier. Total worst-case added latency
# is small (0.25 + 0.5 = 0.75s) but rides out a container restart / brief blip.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.25
# Routed-to on classifier failure: route_by_intent maps None -> billing_agent
# (its existing default). Confidence 0.0 then forces supervisor escalation.
_FALLBACK_INTENT: Optional[str] = None


async def classify(
    state: AgentState,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, object]:
    settings = Settings()
    base = settings.CLASSIFIER_API_URL
    headers = {"X-API-Key": settings.ESTC_API_KEY} if settings.ESTC_API_KEY else None
    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base, timeout=5.0, headers=headers)
    last_exc: Optional[Exception] = None
    try:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await client.post("/classify", json={"text": state.raw_issue_text})
                resp.raise_for_status()
                body = resp.json()
                intent = body["intent"]
                confidence = float(body["confidence"])
                return {
                    "intent": intent,
                    "confidence_score": confidence,
                    "execution_logs": state.execution_logs + [f"classified:{intent}"],
                }
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_BACKOFF_BASE * (2**attempt))
    finally:
        if owns_client:
            await client.aclose()

    # Exhausted retries: degrade to escalation rather than failing the graph.
    logger.warning("classifier unreachable after %d attempts: %s", _MAX_ATTEMPTS, last_exc)
    return {
        "intent": _FALLBACK_INTENT,
        "confidence_score": 0.0,
        "execution_logs": state.execution_logs + [f"classify_failed:{type(last_exc).__name__}"],
    }
