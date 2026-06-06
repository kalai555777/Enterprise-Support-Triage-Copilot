"""Lazy-loaded DistilBERT intent classifier (Phase 2, task 2.3.2).

Loads the fine-tuned ``distilbert_intent`` weights + tokenizer exactly once and
exposes :func:`classify` returning ``(intent, confidence)``. Intent routing is
done entirely by this local model — never an LLM (design.md Component A).

Threads are capped (``torch.set_num_threads``) so a single classification stays
within the < 50ms p99 latency target on CPU and doesn't contend with the rest of
the service. The model path is configurable via ``CLASSIFIER_MODEL_DIR`` so the
container and local-dev layouts can both be satisfied.
"""

from __future__ import annotations

import json
import os
import threading
from functools import lru_cache
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

# Cap intra-op threads: this is a latency-sensitive single-sample service, not a
# throughput batch job. Two threads beats the GIL-bound default on small inputs.
torch.set_num_threads(int(os.getenv("CLASSIFIER_TORCH_THREADS", "2")))

# Default resolves to ``<this dir>/../models/distilbert_intent`` which is correct
# both locally (estc/services/classifier_api/models/...) and in the container
# (/app/models/...). Override with CLASSIFIER_MODEL_DIR.
_DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "distilbert_intent"
MODEL_DIR = Path(os.getenv("CLASSIFIER_MODEL_DIR", str(_DEFAULT_MODEL_DIR)))

# torch inference is not guaranteed thread-safe across a shared module; serialize.
_INFER_LOCK = threading.Lock()


class _Classifier:
    """Holds the loaded model, tokenizer, and id->label map."""

    def __init__(self, model_dir: Path) -> None:
        if not model_dir.is_dir():
            raise FileNotFoundError(
                f"Classifier model not found at {model_dir}. Run train.py "
                "(or build the image, which trains at build time) first."
            )
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            str(model_dir), local_files_only=True
        )
        self.model.eval()
        with open(model_dir / "label_map.json", encoding="utf-8") as fh:
            # JSON keys are strings ("0".."3"); normalise to int for argmax lookup.
            self.id2label: dict[int, str] = {int(k): v for k, v in json.load(fh).items()}

    def predict(self, text: str) -> tuple[str, float]:
        encoded = self.tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            return_token_type_ids=False,
        )
        with _INFER_LOCK, torch.no_grad():
            logits = self.model(**encoded).logits
        probs = torch.softmax(logits, dim=-1)[0]
        pred_id = int(torch.argmax(probs))
        return self.id2label[pred_id], float(probs[pred_id])


@lru_cache(maxsize=1)
def get_classifier() -> _Classifier:
    """Return the process-wide classifier singleton, loading it on first call."""
    return _Classifier(MODEL_DIR)


def classify(text: str) -> tuple[str, float]:
    """Classify ``text`` into an intent label with its softmax confidence."""
    return get_classifier().predict(text)
