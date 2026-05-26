from fastapi import FastAPI
from .schemas import ClassifyRequest, ClassifyResponse
import time

app = FastAPI(title="Mock Classifier API")

@app.get("/healthz")
def health_check():
    return {"status": "ok", "model_loaded": True}

@app.post("/classify", response_model=ClassifyResponse)
def classify_text(request: ClassifyRequest):
    start_time = time.time()
    text_lower = request.text.lower()

    # Mock Routing Logic
    if "500" in text_lower or "error" in text_lower or "bug" in text_lower:
        intent = "bug"
    elif "login" in text_lower or "lock" in text_lower or "access" in text_lower:
        intent = "lockout"
    elif "feature" in text_lower or "add" in text_lower or "idea" in text_lower:
        intent = "feature"
    else:
        intent = "billing"

    latency_ms = (time.time() - start_time) * 1000

    return ClassifyResponse(
        intent=intent,
        confidence=0.85, 
        latency_ms=latency_ms
    )