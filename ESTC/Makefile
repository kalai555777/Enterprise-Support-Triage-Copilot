# ESTC developer shortcuts.
# `make eval` reproduces Phase 4.5.2 (Ragas evaluation -> estc/tests/eval/results.csv).
# On Windows where `make` is unavailable, use the canonical entry: pwsh ./scripts/eval.ps1

PYTHON ?= .venv/Scripts/python

.PHONY: eval
eval:
	$(PYTHON) estc/tests/eval/ragas_eval.py
