# Architectural Specification: Phase 4.5 — Observability & Evaluation

**Status:** DRAFT / PROPOSED
**Associated Tasks:** Tasks 4.5.1 – 4.5.3 (`docs/plan.md` § Phase 4.5 — Observability & Evaluation)
**Target Files:**
- `estc/services/orchestrator/graph/observability.py` (new — `configure_tracing()` LangSmith bootstrap, task 4.5.1)
- `estc/shared/config.py` (modify — add `LANGSMITH_TRACING: bool = False`; `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` already present)
- `.env.example` (modify — add `LANGSMITH_TRACING=true`)
- `estc/tests/eval/ragas_eval.py` (new — Faithfulness / Answer Relevance / Context Recall over 20 tickets → `results.csv`, task 4.5.2)
- `estc/tests/eval/fixtures/eval_tickets.jsonl` (new — the 20-ticket gold fixture: `question`, `ground_truth`, `company_id`)
- `estc/tests/eval/__init__`-free dir (PEP 420 namespace, no `__init__.py`)
- `scripts/eval.ps1` (new — `pwsh` shortcut, task 4.5.3) and `Makefile` (new — `make eval` target, task 4.5.3)
- `estc/tests/test_observability.py` (new — offline-safe tracing/eval-wiring contract tests)
- `requirements-orchestrator.txt` (modify — pin a **mutually-compatible** `ragas` + `langchain-community` pair; see § 5 / Risk 1)

**Consumes (unchanged):** `estc.services.orchestrator.graph.build.run_ticket` / `astream_ticket` / `graph` (Phase 4.4), `AgentState` (4.1), the RAG retriever (`aretrieve`, `RetrievedChunk`) and `data/knowledge_base/*.md` (4.2), and the worker nodes' `agent_draft_response` / `retrieved_context` (4.3).

---


## 1. Executive Summary & Problem Statement

### 1.1 Objective & Context

This sub-phase makes the Phase 4.4 triage graph **observable and measurable**. Phase 4.4 delivered a runnable `run_ticket(...)` that streams node transitions; Phase 4.5 wraps that runtime in the `design.md` § 4 "Evaluation-Driven Development" framework — **LangSmith** for distributed tracing and **Ragas** for grounding/quality metrics — so every ticket run is inspectable as a trace tree and the RAG-backed drafts are scored against the three mandated metrics before any of it is exposed through the Phase 4.6 API or the Phase 5 UI.

Three artifacts are produced:

1. **LangSmith tracing (task 4.5.1).** A `configure_tracing()` bootstrap that, when `LANGSMITH_TRACING=true` and an API key is present, registers the LangSmith handler so a single `run_ticket` call appears in project `estc-dev` as **one root run with ≥ 6 child runs** (one per graph node: `classify`, the dispatched worker, `supervisor_review`, plus LangGraph's internal channel steps). LangChain/LangGraph emit these spans automatically once tracing env is set; this phase's job is the deterministic, opt-in *bootstrap* and the guarantee that the graph executes inside a traced context.
2. **Ragas evaluation harness (task 4.5.2).** `estc/tests/eval/ragas_eval.py` runs the graph over a **20-ticket gold fixture**, assembles a Ragas dataset from each run's `question` (raw issue), `answer` (`agent_draft_response`), `contexts` (`retrieved_context`), and `ground_truth` (fixture reference answer), computes **Faithfulness**, **Answer Relevance**, and **Context Recall**, and writes `estc/tests/eval/results.csv`. The exit bar is a **mean ≥ 0.80** on all three metrics.
3. **One-command shortcut (task 4.5.3).** `make eval` and `pwsh ./scripts/eval.ps1` reproduce 4.5.2 so the eval is a single, CI-friendly invocation.

### 1.2 Core Problem Statement

Everything before this phase optimized for **offline determinism** — the suite runs green with no API keys (mock classifier, file-backed GitHub, template LLM drafts). Phase 4.5 is the first phase whose *core deliverable inherently requires live, non-deterministic services*: **LangSmith tracing needs a `LANGSMITH_API_KEY` and network egress to `smith.langchain.com`, and every Ragas metric needs a judge LLM (and an embedding model) to score faithfulness/relevance.** There is no offline path that produces a *real* trace tree or a *real* ≥ 0.80 metric — the deterministic template draft is not a faithful RAG answer and there is no judge to score it.

The challenge is therefore to **add live observability/evaluation without breaking the clean-checkout invariant**: tracing must be a no-op (not an error) when keys are absent, the Ragas harness must `skip` cleanly rather than fail when its judge/keys/deps are missing, and the *wiring* (env plumbing, dataset assembly, CSV schema, child-run-count assertion shape) must be unit-testable offline even though the *scores* are only obtainable live. Compounding this, the installed evaluation stack is **currently broken at import**: `ragas==0.4.3` fails against `langchain-community==0.4.2` (`No module named 'langchain_community.chat_models.vertexai'`), so a version-compatibility fix is a precondition of 4.5.2 (§ 5.2 / Risk 1).

---

## 2. System Boundaries & Constraints

### 2.1 Architectural Boundaries

- **Upstream Trigger / Consumer:**
  - `configure_tracing()` is called once at orchestrator process start — by the Phase 4.6 FastAPI app on startup, and by `ragas_eval.py` / the test harness before invoking the graph. It must be idempotent and safe to call when tracing is disabled.
  - `ragas_eval.py` is a standalone script (and a pytest-collectable module) whose only runtime input is the 20-ticket fixture; its output is `results.csv` (+ a stdout summary). `make eval` / `eval.ps1` are thin wrappers over it.
- **Downstream Dependencies:**
  - **LangSmith SaaS** (`langsmith==0.8.5`, imports cleanly): reached only when `LANGSMITH_TRACING=true` **and** `LANGSMITH_API_KEY` set. The 4.5.1 verify uses `langsmith.Client().list_runs(project_name="estc-dev", limit=10)`.
  - **The Phase 4.4 graph** (`run_ticket` / `astream_ticket` / `graph`): the single thing being traced and evaluated. Phase 4.5 adds **no node logic** and changes **no** graph topology.
  - **Ragas** (`ragas`, currently 0.4.3 — *import-broken*, see Risk 1) + a **judge LLM** (Claude Sonnet 4.6 / `gpt-4o-mini` via the same key-selection ladder as `graph/llm.py`) + an **embedding model** for Answer Relevance. Faithfulness & Context Recall need the LLM; Answer Relevance needs LLM + embeddings.
  - **The 4.2 RAG retriever & knowledge base** (18 `.md` files under `data/knowledge_base/`): the source of `retrieved_context` (Ragas `contexts`) and the basis for authoring fixture `ground_truth`.
- **Instrumentation boundary:** Tracing is achieved through **LangChain's native env-driven callback** (`LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT`) — Phase 4.5 does **not** hand-instrument nodes with manual spans. `configure_tracing()` only normalizes env + validates key presence and returns whether tracing is active. This keeps the node bodies untouched (consistent with FR-9 of Phase 4.4) and lets LangGraph's built-in LangSmith integration produce the child-run tree.

### 2.2 Technical & Operational Constraints

- **Live-vs-offline duality (the defining constraint):** With keys present, 4.5.1/4.5.2 produce real traces and real scores. With keys absent, `configure_tracing()` returns `False` and forces tracing off (no network), and `ragas_eval.py` / its tests **skip** with a clear reason. The clean-checkout test suite must stay green.
- **Async discipline (memory `feedback_mcp_async`):** the harness drives the graph via the async `run_ticket` / `astream_ticket`; any batch over the 20 fixtures awaits them (no blocking `.invoke`). Windows selector loop already set in `conftest.py`.
- **Performance:** 20-ticket eval is dominated by judge-LLM latency; it is an *offline batch* (not request-path) so no hard latency SLA, but the harness should run the 20 graph executions concurrently-bounded (e.g. `asyncio.gather` with a small semaphore) to keep wall-clock reasonable. Tracing adds negligible per-node overhead (async callback flush).
- **Security & Compliance:** `LANGSMITH_API_KEY` and LLM keys come only from env/`.env` (never committed; `.env` is git-ignored from Phase 1). Traces sent to LangSmith **will contain ticket text and drafts** — acceptable for the synthetic `estc-dev` dataset, but the spec records that real PII must not be traced without review (matches the PII-out-of-logs rule from 4.3/4.4). `results.csv` contains only fixture text + numeric scores.
- **Determinism caveat:** Ragas scores are LLM-judged and therefore *not bit-reproducible*; the ≥ 0.80 bar is a **mean over 20 tickets** to absorb per-item judge variance. The fixture is fixed and versioned so the *inputs* are deterministic even though the scores are not.
- **Dependency integrity:** `import ragas` must succeed before 4.5.2 can run. The plan must pin a compatible `ragas`/`langchain-community` pair (or shim the missing `vertexai` import) — this is a hard precondition, not a nicety (§ 5.2, Risk 1).
- **Packaging:** new dirs (`estc/tests/eval/`, `estc/tests/eval/fixtures/`, `scripts/`) follow the PEP 420 no-`__init__.py` convention used project-wide.

---

## 3. Functional Requirements

- **FR-1 (Tracing bootstrap — task 4.5.1):** `configure_tracing() -> bool` reads `Settings()`, and if `LANGSMITH_TRACING` is true **and** `LANGSMITH_API_KEY` is non-empty, sets the process env LangChain reads (`LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT` defaulting to `estc-dev`, and the v2 alias `LANGCHAIN_TRACING_V2`/`LANGCHAIN_PROJECT` for cross-version safety) and returns `True`; otherwise it **forces tracing off** (ensures `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` are not truthy) and returns `False`. Idempotent; never raises on missing keys.
- **FR-2 (Child-run topology — task 4.5.1 verify):** With tracing active, exactly one `run_ticket` call yields, in project `estc-dev`, **≥ 1 root run** whose tree contains **≥ 6 child runs** — at minimum one span per executed graph node (`classify`, one worker, `supervisor_review`) plus the LangGraph/LangChain internal steps that bring the count to ≥ 6. Achieved via LangGraph's native tracing; no manual span code.
- **FR-3 (Config surface):** `Settings` gains `LANGSMITH_TRACING: bool = False` (env-driven, default off so existing verifies stay green). `LANGSMITH_API_KEY` (optional) and `LANGSMITH_PROJECT="estc-dev"` already exist. `.env.example` documents `LANGSMITH_TRACING=true`.
- **FR-4 (Gold fixture — task 4.5.2):** `estc/tests/eval/fixtures/eval_tickets.jsonl` contains **exactly 20** records, each `{"question": <raw issue text>, "ground_truth": <reference answer grounded in the KB>, "company_id": <seeded id>}`, spanning all four intents (billing / bug / feature / lockout) so the eval exercises every worker path. `ground_truth` is authored from the 18 KB docs (the Context-Recall reference).
- **FR-5 (Dataset assembly — task 4.5.2):** For each fixture row, the harness runs the graph (`run_ticket`) and builds a Ragas sample: `question` = fixture question, `answer` = `state.agent_draft_response`, `contexts` = `state.retrieved_context` (list[str]), `ground_truth` = fixture ground_truth. Rows where the graph escalated with an empty draft are still included (they legitimately score low on relevance).
- **FR-6 (Metric computation — task 4.5.2):** Using Ragas `evaluate(...)` with `Faithfulness`, `AnswerRelevancy`, and `ContextRecall` (judge LLM + embeddings selected by the `graph/llm.py` key ladder), compute per-row and mean scores for all three metrics.
- **FR-7 (CSV artifact — task 4.5.2 verify):** Write `estc/tests/eval/results.csv` with one row per ticket plus a final/`mean` row, columns at least `ticket_id,intent,faithfulness,answer_relevancy,context_recall`. Print a summary; the success criterion is **mean ≥ 0.80** for each of the three metrics.
- **FR-8 (Skip-guard / offline behavior):** When `import ragas` fails, or no judge key is set, or the fixture/graph deps are unavailable, `ragas_eval.py` exits cleanly with a clear "evaluation skipped — <reason>" message and **does not** write a bogus CSV; the corresponding pytest is `skipif`-guarded so the clean-checkout suite stays green (FR mirrors the 4.4 live-test skip pattern).
- **FR-9 (Shortcut — task 4.5.3):** `make eval` and `pwsh ./scripts/eval.ps1` both invoke `.venv\Scripts\python estc/tests/eval/ragas_eval.py` and surface its exit code/summary, reproducing 4.5.2 output. `eval.ps1` is the canonical Windows entry; `make eval` calls the same script.
- **FR-10 (No graph mutation):** Phase 4.5 adds tracing/eval *around* the graph; it imports and calls `run_ticket`/`graph` unchanged and introduces no node, edge, or `AgentState` change.

---

## 4. Detailed Component Specifications & API Contracts

### 4.1 Interface Code & Data Shapes

**`estc/services/orchestrator/graph/observability.py`:**

```python
from __future__ import annotations

import os
from estc.shared.config import Settings


def configure_tracing() -> bool:
    """Opt-in LangSmith bootstrap (task 4.5.1). Returns True iff tracing is active.

    Active requires LANGSMITH_TRACING=true AND a non-empty LANGSMITH_API_KEY. When
    inactive, tracing env is forced off so no run ever attempts network egress.
    Idempotent; never raises when keys are absent (offline clean-checkout safety).
    """
    s = Settings()
    active = bool(s.LANGSMITH_TRACING and s.LANGSMITH_API_KEY)
    if active:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"   # cross-version alias
        os.environ["LANGSMITH_PROJECT"] = s.LANGSMITH_PROJECT
        os.environ["LANGCHAIN_PROJECT"] = s.LANGSMITH_PROJECT
        os.environ["LANGSMITH_API_KEY"] = s.LANGSMITH_API_KEY
    else:
        for k in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
            os.environ[k] = "false"
    return active
```

**`estc/tests/eval/ragas_eval.py` (shape):**

```python
from __future__ import annotations

import asyncio, csv, json, sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "eval_tickets.jsonl"
RESULTS = Path(__file__).parent / "results.csv"
METRICS = ("faithfulness", "answer_relevancy", "context_recall")
THRESHOLD = 0.80


def _skip(reason: str) -> int:
    print(f"evaluation skipped — {reason}")
    return 0  # clean, non-failing exit for CI / clean checkout


async def _collect_samples() -> list[dict]:
    from estc.services.orchestrator.graph.build import run_ticket
    rows = [json.loads(l) for l in FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()]
    samples = []
    for i, r in enumerate(rows):
        st = await run_ticket(f"eval-{i}", r["question"], r["company_id"])
        samples.append({
            "ticket_id": f"eval-{i}", "intent": st.intent,
            "question": r["question"], "answer": st.agent_draft_response or "",
            "contexts": st.retrieved_context, "ground_truth": r["ground_truth"],
        })
    return samples


def main() -> int:
    try:
        from ragas import evaluate
        from ragas.metrics import Faithfulness, AnswerRelevancy, ContextRecall
    except Exception as e:                      # import-broken or missing (Risk 1)
        return _skip(f"ragas unavailable: {e}")
    # ... judge-key check -> _skip if absent; assemble HF/EvaluationDataset;
    # evaluate([...]); write per-row + mean to RESULTS; return 0 if all means >= THRESHOLD else 1.


if __name__ == "__main__":
    sys.exit(main())
```

**Gold fixture row (`eval_tickets.jsonl`):**

```json
{"question": "I am getting a 500 error when pulling the API, my company ID is 9422", "ground_truth": "A 500 on the pull endpoint is tracked in our issue list; retry with backoff and check the status page; engineering is investigating.", "company_id": "9422"}
```

### 4.2 Endpoint / Method Contracts

| Component | Signature | Reads | Produces | I/O |
|---|---|---|---|---|
| `configure_tracing` (4.5.1) | `() -> bool` | `Settings` (`LANGSMITH_*`) | sets/clears tracing env; returns active flag | none (env only) |
| `_collect_samples` (4.5.2) | `async () -> list[dict]` | fixture + `run_ticket` | per-ticket Ragas samples | drives graph (classifier/MCP/RAG/LLM) |
| `main` / eval (4.5.2) | `() -> int` (exit code) | samples + judge LLM | `results.csv` + stdout summary | Ragas judge LLM + embeddings |
| `eval.ps1` / `make eval` (4.5.3) | shell | — | runs `ragas_eval.py` | subprocess |

- **CSV schema:** `ticket_id, intent, faithfulness, answer_relevancy, context_recall` + a trailing `mean` row. Exit `0` when every mean ≥ `0.80`, `1` when below, `0`(skip) when deps/keys absent.
- **Tracing contract:** `run_ticket` is invoked *after* `configure_tracing()` returns `True`; the LangSmith project `estc-dev` then holds the root+child tree (FR-2).

---

## 5. Edge Cases & Error Handling

### 5.1 Anticipated Edge Cases

1. **No LangSmith key / tracing disabled (clean checkout).** `configure_tracing()` returns `False`, forces tracing env off, and `run_ticket` runs untraced. The 4.5.1 verify test asserts only the *bootstrap contract* offline (returns `False`, env off); the live child-run assertion is skip-guarded on key presence.
2. **`import ragas` fails (the current state — Risk 1).** `ragas==0.4.3` references `langchain_community.chat_models.vertexai`, removed in `langchain-community 0.4.2`. The harness catches the `ImportError`/`ModuleNotFoundError` and **skips** (FR-8); the plan's precondition step pins a compatible pair so the *live* path works.
3. **No judge LLM key but ragas importable.** Faithfulness/Relevance/Recall cannot be computed; harness skips with "no judge LLM key" rather than emitting zeros.
4. **Graph escalates with empty draft.** A `lockout`/low-confidence run may yield `agent_draft_response=""`; the row is kept (`answer=""`), scoring low on relevance — correct, not an error. Mean over 20 absorbs it.
5. **Empty `retrieved_context`.** Context Recall is undefined/low when `contexts=[]`; the fixture is authored so each intent's question retrieves ≥ 1 chunk, but the harness tolerates `[]` (passes an empty list, lets Ragas score it).
6. **Child-run count < 6 due to internal-step variance.** LangGraph span granularity can vary by version; FR-2 counts node spans + internal steps. If a version emits fewer wrapper spans, the live test asserts `>= (#executed nodes)` and the exit-gate documents the ≥ 6 expectation against the pinned LangGraph.
7. **Partial fixture / malformed JSONL line.** Blank lines are skipped; a malformed line fails fast with the line number (fixture is a versioned asset, so this is an authoring error surfaced loudly).
8. **`results.csv` locked/open (Windows).** Write to a temp file then `os.replace` to avoid a half-written CSV if the file is open in Excel.

### 5.2 Error Handling & State Recovery Matrix

| Trigger / Exception | Handled State / Action | Fallback Behavior / Mitigation |
|---|---|---|
| `LANGSMITH_TRACING`/key absent | `configure_tracing()` → `False`, env forced off | Untraced run; offline tests assert bootstrap contract only (edge 1) |
| `import ragas` fails (vertexai) | Harness catches, `_skip(...)`, exit 0 | Plan pins compatible `ragas`/`langchain-community`; live path then works (edge 2, Risk 1) |
| No judge LLM key | `_skip("no judge LLM key")`, exit 0, no CSV | Live eval requires a key; CI without keys stays green (edge 3) |
| Empty draft / empty contexts | Row kept with `""`/`[]` | Scores low, not crash; mean-of-20 bar absorbs it (edges 4, 5) |
| Child runs < 6 | Live test asserts ≥ executed-node count | Document ≥ 6 vs pinned LangGraph; revisit pin if span model changes (edge 6) |
| Malformed fixture line | Fail fast with line number | Versioned fixture; authoring-time error (edge 7) |
| `results.csv` write contention | temp-file + atomic `os.replace` | No partial CSV (edge 8) |
| LangSmith network error during flush | Tracing best-effort; run still returns state | Trace loss is non-fatal to `run_ticket`; logged, not raised |

---

## 6. Acceptance Criteria

### 6.1 Technical Acceptance Criteria

- **AC-T1 (Tracing bootstrap contract, offline — FR-1):** With `LANGSMITH_TRACING` unset/false or no key, `configure_tracing()` returns `False` and leaves `os.environ["LANGSMITH_TRACING"]` non-truthy. With both set (monkeypatched), it returns `True` and sets `LANGSMITH_PROJECT=estc-dev`. Runs with no network.
- **AC-T2 (Child-run tree, live — task 4.5.1 verify, skip-guarded):** With real keys, after one `run_ticket`, `langsmith.Client().list_runs(project_name="estc-dev", limit=10)` returns ≥ 1 root run whose descendants total ≥ 6 child runs. `skipif` no `LANGSMITH_API_KEY`.
- **AC-T3 (Fixture integrity — FR-4):** `eval_tickets.jsonl` has exactly 20 well-formed rows, each with non-empty `question`/`ground_truth`/`company_id`, collectively covering all four intents.
- **AC-T4 (Dataset assembly, offline-shape — FR-5):** A unit test with the graph monkeypatched (deterministic `run_ticket`) asserts `_collect_samples()` produces 20 samples each with keys `question/answer/contexts/ground_truth` of the right types — no judge LLM needed.
- **AC-T5 (Eval run + CSV, live — task 4.5.2 verify, skip-guarded):** With a judge key and a fixed `ragas`/`langchain-community` pair, `.venv\Scripts\python estc/tests/eval/ragas_eval.py` writes `estc/tests/eval/results.csv` containing the three metric columns and a `mean` row, with **each mean ≥ 0.80**; exit code `0`.
- **AC-T6 (Graceful skip — FR-8):** With ragas import-broken or no judge key, `ragas_eval.py` prints "evaluation skipped — …", writes no CSV, and exits `0`; the pytest wrapper reports `skipped`.
- **AC-T7 (Shortcut parity — task 4.5.3):** `pwsh ./scripts/eval.ps1` (and `make eval` where `make` exists) runs the same harness and returns its exit code; on a clean checkout both report the skip and exit `0`.
- **AC-T8 (No-regression / no graph mutation — FR-10):** The Phase 4.4 graph tests and the broader suite stay green; `git diff` shows no change under `graph/nodes/` or `agent_state.py`.

### 6.2 Business & Functional Alignment

- **AC-B1 (EDD framework realized, `design.md` § 4):** The three named metrics — Faithfulness, Answer Relevance, Context Recall — are computed by Ragas over real graph outputs and persisted to `results.csv`, exactly the design's "Core Metrics Tracked".
- **AC-B2 (Faithfulness/grounding gate, `design.md` § Component C):** Faithfulness is measured against `retrieved_context`, operationalizing the 4.3/4.4 grounding mandate (drafts built only from retrieved KB context); the ≥ 0.80 bar is the hallucination guardrail.
- **AC-B3 (Trace observability, `design.md` § 4 / Service 3):** Each ticket is a LangSmith trace tree with per-node child runs, giving the "orchestrator-app linked directly with LangSmith" capability the design calls for — the basis for Phase 4.6/5 debugging.
- **AC-B4 (Offline-first parity preserved):** Like every prior phase, the clean checkout stays green — tracing no-ops and the eval skips without keys; only the explicitly live, key-gated paths produce traces/scores.
- **AC-B5 (Phase 4.6 readiness):** `configure_tracing()` is the startup hook the Phase 4.6 FastAPI app calls; the eval harness + `results.csv` are the artifacts the Phase 5.6 exit gate re-runs against the live containerized orchestrator.

---

**Open items for the execution plan (Phase 4.5 plan):**
1. **Resolve the ragas/langchain-community import break (hard precondition).** Choose one: (a) pin `ragas` to a release compatible with `langchain-community 0.4.2`, (b) pin `langchain-community` to a version exposing `chat_models.vertexai`, or (c) ship a tiny import shim. The plan must verify `.venv\Scripts\python -c "import ragas"` exits 0 before 4.5.2 is runnable.
2. **Path adaptation:** the roadmap writes `tests/eval/...`; this repo nests packages under `estc/`, so the plan targets `estc/tests/eval/ragas_eval.py` + `estc/tests/eval/results.csv` (consistent with how prior phases mapped `services/` → `estc/services/`). Confirm the 4.5.3 shortcut and any CI invoke the `estc/`-prefixed path.
3. **Judge-LLM selection:** reuse `graph/llm.py`'s key ladder (Anthropic → OpenAI) to build the Ragas judge + embeddings; decide the embedding source for Answer Relevance (OpenAI embeddings vs. the local `bge` model already used by RAG) and pin it.
4. **Seeded `company_id`s in the fixture:** confirm the 20 fixture `company_id`s exist in the seeded Postgres (e.g. `9422`) so `billing`/`lockout` runs return real account facts; otherwise those rows score lower on faithfulness.
5. **`make` availability on Windows:** `make eval` may be unavailable on the dev box (no `make`); `eval.ps1` is the canonical entry and the plan notes `make` is best-effort / for CI Linux.
6. **LangGraph child-run count:** verify the pinned LangGraph emits ≥ 6 spans for a 3-node path; if it emits fewer wrapper spans, the live assertion uses ≥ executed-node-count and the gate documents the discrepancy.
```

