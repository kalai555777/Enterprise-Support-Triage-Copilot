"""Evaluate the fine-tuned classifier on the held-out test split (task 2.2.3).

Prints a per-class classification report and macro F1 against
``estc/data/training/test.csv``. Gate: macro F1 >= 0.88.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import torch
from sklearn.metrics import classification_report, f1_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]

DATA_DIR = Path(os.getenv("CLASSIFIER_DATA_DIR", str(_REPO_ROOT / "estc" / "data" / "training")))
MODEL_DIR = Path(os.getenv("CLASSIFIER_MODEL_DIR", str(_HERE / "models" / "distilbert_intent")))


def main() -> None:
    tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(str(MODEL_DIR), local_files_only=True)
    model.eval()
    with open(MODEL_DIR / "label_map.json", encoding="utf-8") as fh:
        id2label = {int(k): v for k, v in json.load(fh).items()}
    label2id = {v: k for k, v in id2label.items()}

    df = pd.read_csv(DATA_DIR / "test.csv")
    y_true = [label2id[label] for label in df["label"]]
    y_pred: list[int] = []
    for text in df["text"].astype(str):
        enc = tokenizer(
            text, return_tensors="pt", truncation=True, max_length=512, return_token_type_ids=False
        )
        with torch.no_grad():
            logits = model(**enc).logits
        y_pred.append(int(torch.argmax(logits, dim=-1)))

    target_names = [id2label[i] for i in range(len(id2label))]
    print(classification_report(y_true, y_pred, target_names=target_names, digits=4))
    macro_f1 = f1_score(y_true, y_pred, average="macro")
    print(f"Macro F1: {macro_f1:.4f}")
    if macro_f1 < 0.88:
        raise SystemExit(f"Macro F1 {macro_f1:.4f} below 0.88 target")


if __name__ == "__main__":
    main()
