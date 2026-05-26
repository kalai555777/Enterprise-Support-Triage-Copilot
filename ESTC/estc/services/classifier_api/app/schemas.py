from pydantic import BaseModel

class ClassifyRequest(BaseModel):
    text: str

class ClassifyResponse(BaseModel):
    intent: str
    confidence: float
    latency_ms: float