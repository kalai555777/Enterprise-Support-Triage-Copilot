"""Fine-tune DistilBERT for intent classification (Phase 2, task 2.2.1).

Trains ``distilbert-base-uncased`` as a 4-way sequence classifier over the
templated support tickets in ``estc/data/training`` and writes the weights,
tokenizer, and ``label_map.json`` to ``models/distilbert_intent/``.

Reproducible: fixed seed, reads the committed train/val CSVs. Run directly
(``python -m estc.services.classifier_api.train``) or it is invoked by the
Docker build stage so the runtime image ships a trained model.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import accuracy_score, f1_score
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
    set_seed,
)

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]  # classifier_api -> services -> estc -> repo root

DATA_DIR = Path(os.getenv("CLASSIFIER_DATA_DIR", str(_REPO_ROOT / "estc" / "data" / "training")))
OUTPUT_DIR = Path(os.getenv("CLASSIFIER_MODEL_DIR", str(_HERE / "models" / "distilbert_intent")))
BASE_MODEL = os.getenv("CLASSIFIER_BASE_MODEL", "distilbert-base-uncased")

# Stable label ordering — must match design.md / the orchestrator's intents.
LABELS = ["billing", "bug", "feature", "lockout"]
LABEL2ID = {label: i for i, label in enumerate(LABELS)}
ID2LABEL = {i: label for label, i in LABEL2ID.items()}

SEED = 42
EPOCHS = int(os.getenv("CLASSIFIER_EPOCHS", "3"))


class _TicketDataset(torch.utils.data.Dataset):
    def __init__(self, texts: list[str], labels: list[int], tokenizer) -> None:
        self.encodings = tokenizer(
            texts, truncation=True, padding=True, max_length=512, return_token_type_ids=False
        )
        self.labels = labels

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        item = {k: torch.tensor(v[idx]) for k, v in self.encodings.items()}
        item["labels"] = torch.tensor(self.labels[idx])
        return item


def _load_split(name: str) -> tuple[list[str], list[int]]:
    df = pd.read_csv(DATA_DIR / f"{name}.csv")
    texts = df["text"].astype(str).tolist()
    labels = [LABEL2ID[label] for label in df["label"]]
    return texts, labels


def _compute_metrics(eval_pred) -> dict[str, float]:
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)
    return {
        "accuracy": accuracy_score(labels, preds),
        "macro_f1": f1_score(labels, preds, average="macro"),
    }


def main() -> None:
    set_seed(SEED)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL, num_labels=len(LABELS), id2label=ID2LABEL, label2id=LABEL2ID
    )

    train_texts, train_labels = _load_split("train")
    val_texts, val_labels = _load_split("val")
    train_ds = _TicketDataset(train_texts, train_labels, tokenizer)
    val_ds = _TicketDataset(val_texts, val_labels, tokenizer)

    args = TrainingArguments(
        output_dir=str(OUTPUT_DIR / "_checkpoints"),
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=16,
        per_device_eval_batch_size=32,
        learning_rate=5e-5,
        eval_strategy="epoch",
        save_strategy="no",
        logging_steps=20,
        seed=SEED,
        report_to=[],
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        compute_metrics=_compute_metrics,
    )
    trainer.train()
    metrics = trainer.evaluate()
    print(f"Validation metrics: {metrics}")

    model.save_pretrained(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    with open(OUTPUT_DIR / "label_map.json", "w", encoding="utf-8") as fh:
        json.dump({str(i): label for i, label in ID2LABEL.items()}, fh)

    acc = metrics.get("eval_accuracy", 0.0)
    print(f"Saved model to {OUTPUT_DIR} (val accuracy={acc:.4f})")
    if acc < 0.90:
        raise SystemExit(f"Validation accuracy {acc:.4f} below 0.90 target")


if __name__ == "__main__":
    main()
