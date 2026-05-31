# Phase 4.5.3 — one-command Ragas evaluation shortcut (canonical Windows entry).
# Reproduces task 4.5.2: runs the harness, which writes estc/tests/eval/results.csv when a
# judge LLM + the orchestrator dependencies are available, or skips cleanly otherwise.
# NOTE: do not set $ErrorActionPreference = "Stop" here — the harness emits third-party
# DeprecationWarnings on stderr, which PowerShell 5.1 would otherwise treat as fatal.
$repoRoot = Split-Path -Parent $PSScriptRoot
& "$repoRoot\.venv\Scripts\python.exe" "$repoRoot\estc\tests\eval\ragas_eval.py"
exit $LASTEXITCODE
