from pydantic import BaseModel, Field


class ClassifyRequest(BaseModel):
    # Reject empty/blank text and cap length to protect the model from abuse.
    # DistilBERT truncates at 512 tokens anyway; 10k chars is a generous ceiling.
    text: str = Field(min_length=1, max_length=10_000)


class ClassifyResponse(BaseModel):
    intent: str
    confidence: float
    latency_ms: float
