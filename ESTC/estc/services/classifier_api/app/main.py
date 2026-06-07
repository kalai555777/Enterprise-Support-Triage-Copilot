"""Intent Classifier API (Phase 2, FR-1).

Serves the fine-tuned DistilBERT intent model behind ``POST /classify``. Replaces
the original keyword-matching mock: intent and confidence are now real model
outputs, which is what makes the orchestrator's confidence-based escalation
(supervisor threshold, task 4.3.7) meaningful.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from estc.shared.auth import ApiKeyMiddleware
from estc.shared.logging_setup import RequestIdMiddleware, configure_logging

from .model_loader import get_classifier
from .schemas import ClassifyRequest, ClassifyResponse

configure_logging("classifier-api")
logger = logging.getLogger("classifier_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load weights at startup so the first real request isn't slow and so the
    # healthcheck reflects a genuinely ready model. A load failure is logged but
    # not fatal — /healthz reports model_loaded=false and surfaces it.
    try:
        get_classifier()
        logger.info("classifier model loaded")
    except Exception:  # noqa: BLE001 - report readiness via /healthz, don't crash boot
        logger.exception("classifier model failed to load at startup")
    yield


app = FastAPI(title="ESTC Intent Classifier API", lifespan=lifespan)
# Added last = outermost: request-id wraps auth so even 401s carry X-Request-ID.
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(RequestIdMiddleware)


@app.get("/healthz")
def health_check() -> dict[str, object]:
    try:
        get_classifier()
        model_loaded = True
    except Exception:  # noqa: BLE001
        model_loaded = False
    return {"status": "ok" if model_loaded else "degraded", "model_loaded": model_loaded}


@app.post("/classify", response_model=ClassifyResponse)
def classify_text(request: ClassifyRequest) -> ClassifyResponse:
    start = time.perf_counter()
    intent, confidence = get_classifier().predict(request.text)
    latency_ms = (time.perf_counter() - start) * 1000
    return ClassifyResponse(intent=intent, confidence=confidence, latency_ms=latency_ms)
