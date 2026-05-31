# Execution Plan: Phase 4.5 — Observability & Evaluation
**Source spec:** `.claude/specs/07-Observability-Evaluation-spec.md`
**Source plan section:** `docs/plan.md` § Phase 4.5 (tasks 4.5.1 – 4.5.3)
**Status:** COMPLETE — 4.5.0–4.5.4 executed and verified; exit gates EG-1/EG-2/EG-3/EG-4 green (7 passed / 1 skipped in test_observability; 25 passed / 1 skipped no-regression; ragas import repaired via shim; eval.ps1 + make eval exit 0). **EG-5 (live ≥ 0.80 metrics) and AC-T2 (live LangSmith child runs) DEFERRED** — this box has an `HF_TOKEN` judge and a `LANGSMITH_API_KEY`, but no `OPENAI`/`ANTHROPIC` judge and the orchestrator services (classifier-api/Postgres) are not running, so the live paths skip cleanly; they run against a keyed/containerized orchestrator at Phase 5.6.

---

## Context

This plan operationalizes Phase 4.5 of the ESTC roadmap: making the Phase 4.4 triage graph **observable (LangSmith traces) and measurable (Ragas metrics)** per `design.md` § 4's Evaluation-Driven-Development framework, then exposing a one-command eval. Its consumers are Phase 4.6 (the FastAPI app calls the tracing bootstrap on startup) and Phase 5.6 (re-runs the eval against the live containerized orchestrator). The work has five threads, the first of which is a hard precondition:

1. **Dependency repair** (tooling — plan-internal 4.5.0): `import ragas` currently **fails** (`ragas 0.4.3` imports `langchain_community.chat_models.vertexai`, removed in `langchain-community 0.4.2`). Fix this before any eval code can run.
2. **Tracing bootstrap** (Python — task 4.5.1): `observability.py::configure_tracing()` + a `LANGSMITH_TRACING` setting + `.env.example` doc, so a traced `run_ticket` produces a root run with ≥ 6 child runs in project `estc-dev`.
3. **Eval harness** (Python — task 4.5.2): a 20-ticket gold fixture + `estc/tests/eval/ragas_eval.py` computing Faithfulness / Answer Relevance / Context Recall → `results.csv` (mean ≥ 0.80).
4. **Shortcut** (tooling — task 4.5.3): `scripts/eval.ps1` (canonical Windows entry) + a `Makefile` `eval` target, both reproducing 4.5.2.
5. **Test harness** (Python — plan-internal 4.5.4): `estc/tests/test_observability.py` proving the *wiring* offline (bootstrap contract, fixture integrity, dataset-assembly shape, graceful skip) — the live trace/score paths stay `skipif`-gated.

**Design notes — what this plan decides (and mirrors):**
- **First non-offline-first phase, but the clean-checkout invariant is preserved.** Real traces need `LANGSMITH_API_KEY` + network; real Ragas scores need a judge LLM. So `configure_tracing()` **no-ops** without keys and `ragas_eval.py` **skips cleanly (exit 0, no CSV)** when deps/keys are absent. The live verifies (AC-T2, AC-T5) are `skipif`-guarded — same pattern as the 4.4 live e2e test.
- **Dependency fix = an import shim, not version churn (Decision, Risk 1).** The RAG stack already depends on `langchain-community 0.4.2` (`ingest.py` imports `HuggingFaceBgeEmbeddings`); downgrading it to restore `chat_models.vertexai` risks breaking RAG. Instead, register a stub `langchain_community.chat_models.vertexai` module in `sys.modules` **before** `import ragas`. ChatVertexAI is never instantiated (we judge with the Anthropic/OpenAI ladder), so the stub only satisfies the import. Version-pin is the documented fallback if the shim proves insufficient.
- **Ragas 0.4.x API.** The installed ragas is 0.4.3 (not the `>=0.2.0` floor in requirements). Its API is `EvaluationDataset.from_list([{user_input, response, retrieved_contexts, reference}])` + `evaluate(dataset, metrics=[Faithfulness(), ResponseRelevancy(), ContextRecall()], llm=<wrapped>, embeddings=<wrapped>)`. The harness adapts to the **installed** API at execution; verifies are outcome-based (CSV with the three metric columns), so a minor API rename doesn't invalidate the plan.
- **Path adaptation:** roadmap says `tests/eval/...`; this repo nests packages under `estc/`, so targets are `estc/tests/eval/ragas_eval.py` + `estc/tests/eval/results.csv` (same mapping prior phases used for `services/` → `estc/services/`).
- **Judge LLM/embeddings:** reuse the `graph/llm.py` key ladder (Anthropic Sonnet 4.6 → OpenAI gpt-4o-mini); embeddings for Answer Relevance use OpenAI embeddings when an OpenAI key is present, else the local `bge` model already loaded by RAG. Decided at 4.5.2-b against what's importable.
- **Plan-internal task IDs:** `docs/plan.md` defines roadmap tasks **4.5.1–4.5.3** only. **4.5.0** (dependency repair) and **4.5.4** (test harness) are plan-internal expansions to satisfy spec acceptance bars (AC-T1/T3/T4/T6/T8); not new roadmap IDs.

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**. A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** Confirm the source spec exists and is the version this plan targets.
  **Verify:** `Get-Content .claude/specs/07-Observability-Evaluation-spec.md | Select-String "configure_tracing"` returns ≥ 1 match.
- [ ] **PF-2** Confirm the Phase 4.4 entrypoints this phase wraps are importable (the thing being traced/evaluated).
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.graph.build import run_ticket, astream_ticket, graph; print('ok')"` prints `ok`.
- [ ] **PF-3** Reproduce the ragas import break (the 4.5.0 problem statement) and confirm langsmith imports cleanly.
  **Verify:** `.venv\Scripts\python -c "import langsmith; print('langsmith', langsmith.__version__)"` prints a version; `.venv\Scripts\python -c "import ragas"` **fails** with `ModuleNotFoundError: ... vertexai` (documents the break 4.5.0 fixes).
- [ ] **PF-4** Confirm the installed ragas / langchain-community versions (drives the 4.5.0 fix choice).
  **Verify:** `.venv\Scripts\pip show ragas langchain-community | Select-String "^Name|^Version"` reports `ragas 0.4.3`, `langchain-community 0.4.2` (or records the actual pair).
- [ ] **PF-5** Confirm the `LANGSMITH_*` config surface exists (so 4.5.1 only adds the toggle).
  **Verify:** `.venv\Scripts\python -c "from estc.shared.config import Settings; s=Settings(); print(s.LANGSMITH_PROJECT, s.LANGSMITH_API_KEY is None)"` prints `estc-dev True` (key unset on a clean box).
- [ ] **PF-6** Confirm the knowledge base (ground-truth source for the fixture) is present.
  **Verify:** `(Get-ChildItem estc/data/knowledge_base/*.md | Measure-Object).Count` ≥ 10 (currently 18).
- [ ] **PF-7** Record whether a judge LLM key is present (determines whether the live eval runs or skips here).
  **Verify:** `.venv\Scripts\python -c "import os; print('ANTHROPIC', bool(os.getenv('ANTHROPIC_API_KEY')), 'OPENAI', bool(os.getenv('OPENAI_API_KEY')))"` — either result is acceptable; `False/False` means AC-T5/AC-T2 will `skip` on this box.

---

## Task 4.5.0 — Dependency Repair *(plan-internal; hard precondition for 4.5.2, satisfies Risk 1)*

### 4.5.0-a ragas import shim
- [ ] Create `estc/tests/eval/_ragas_compat.py`: before ragas is imported, register a stub module so the dead `langchain_community.chat_models.vertexai` import resolves. Use `sys.modules.setdefault("langchain_community.chat_models.vertexai", <ModuleType with a placeholder ChatVertexAI = object>)`. Expose a `ensure_ragas_importable()` function that does this and returns nothing. `ragas_eval.py` imports/calls this **before** `from ragas import ...`. ChatVertexAI is never instantiated (judge uses the Anthropic/OpenAI ladder), so the stub is inert.
  **Verify:** `.venv\Scripts\python -c "from estc.tests.eval._ragas_compat import ensure_ragas_importable; ensure_ragas_importable(); import ragas; print('ragas_ok', getattr(ragas,'__version__','?'))"` prints `ragas_ok 0.4.3`.

### 4.5.0-b Record the compatible pairing (fallback documentation)
- [ ] Append a comment block to `requirements-orchestrator.txt` noting the shim and the version-pin fallback (pin `langchain-community` to a `chat_models.vertexai`-bearing release, or pin `ragas` to a version that doesn't import it) in case a future ragas drops the shim's effectiveness. Do **not** change the working `langchain-community 0.4.2` pin (RAG depends on it).
  **Verify:** `Get-Content requirements-orchestrator.txt | Select-String "vertexai|ragas import shim"` returns ≥ 1 match.

---

## Task 4.5.1 — LangSmith Tracing Bootstrap

### 4.5.1-a `LANGSMITH_TRACING` setting + `.env.example`
- [ ] Add `LANGSMITH_TRACING: bool = False` to `estc/shared/config.Settings` (default off so all existing verifies stay green; `extra="ignore"` already tolerates the env key). Add `LANGSMITH_TRACING=true` to `.env.example`.
  **Verify:** `.venv\Scripts\python -c "from estc.shared.config import Settings; print(Settings().LANGSMITH_TRACING)"` prints `False`; `Get-Content .env.example | Select-String "LANGSMITH_TRACING"` returns 1 match.

### 4.5.1-b `configure_tracing()`
- [ ] Create `estc/services/orchestrator/graph/observability.py` per spec § 4.1: `configure_tracing() -> bool` returns `True` iff `LANGSMITH_TRACING and LANGSMITH_API_KEY`, setting `LANGSMITH_TRACING/LANGCHAIN_TRACING_V2=true`, `LANGSMITH_PROJECT/LANGCHAIN_PROJECT`, and `LANGSMITH_API_KEY` in `os.environ`; otherwise forces those tracing flags to `"false"` and returns `False`. Idempotent; never raises (spec FR-1).
  **Verify:** `.venv\Scripts\python -c "import os; os.environ.pop('LANGSMITH_API_KEY',None); from estc.services.orchestrator.graph.observability import configure_tracing as c; assert c() is False and os.environ.get('LANGSMITH_TRACING')=='false'; print('ok')"` prints `ok`. Live child-run behavior is AC-T2 (skip-guarded, Task 4.5.4).

---

## Task 4.5.2 — Ragas Evaluation Harness

### 4.5.2-a 20-ticket gold fixture
- [ ] Create `estc/tests/eval/fixtures/eval_tickets.jsonl` with **exactly 20** rows `{"question","ground_truth","company_id"}` spanning all four intents (≥ 4 each across billing/bug/feature/lockout), with `ground_truth` authored from `estc/data/knowledge_base/*.md` and `company_id`s that exist in the seed (e.g. `9422`) for billing/lockout rows (spec FR-4, open item 4).
  **Verify:** `.venv\Scripts\python -c "import json,pathlib; rows=[json.loads(l) for l in pathlib.Path('estc/tests/eval/fixtures/eval_tickets.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]; assert len(rows)==20 and all(r['question'] and r['ground_truth'] and r['company_id'] for r in rows); print('fixture_ok', len(rows))"` prints `fixture_ok 20`.

### 4.5.2-b `ragas_eval.py`
- [ ] Create `estc/tests/eval/ragas_eval.py` per spec § 4.1: `ensure_ragas_importable()` then guarded `from ragas import ...` (→ `_skip` on failure); `_collect_samples()` runs `run_ticket` over the 20 fixtures building samples (`question/answer/contexts/ground_truth`); a judge-key check (`_skip("no judge LLM key")` if absent); build the **installed-API** Ragas `EvaluationDataset` (map to `user_input/response/retrieved_contexts/reference`), `evaluate(...)` with Faithfulness / Answer-Relevancy / Context-Recall using the `graph/llm.py` judge ladder; write `estc/tests/eval/results.csv` (per-row + `mean` row, columns `ticket_id,intent,faithfulness,answer_relevancy,context_recall`) via temp-file + atomic `os.replace`; `main() -> int` returns 0 (skip or all means ≥ 0.80), 1 (below). Adapt metric/column names to the installed ragas 0.4.3 API (spec FR-5..FR-8).
  **Verify (offline-safe):** `.venv\Scripts\python estc/tests/eval/ragas_eval.py; echo "exit=$LASTEXITCODE"` exits `0` — on a keyless box it prints `evaluation skipped — …` and writes no CSV; **with a judge key** it writes `estc/tests/eval/results.csv` with the three metric columns (the literal 4.5.2 verify, AC-T5).

---

## Task 4.5.3 — One-Command Shortcut

- [ ] Create `scripts/eval.ps1` invoking `.venv\Scripts\python estc/tests/eval/ragas_eval.py` and exiting with its code; create a root `Makefile` with an `eval:` target running the same command (best-effort on Windows where `make` may be absent — `eval.ps1` is canonical; spec FR-9, open item 5).
  **Verify:** `pwsh ./scripts/eval.ps1; echo "exit=$LASTEXITCODE"` reproduces 4.5.2 output and exits `0` (skip on a clean box); `Get-Content Makefile | Select-String "ragas_eval.py"` returns 1 match.

---

## Task 4.5.4 — Observability Test Harness *(plan-internal; satisfies AC-T1/T3/T4/T6/T8)*

### 4.5.4-a Test file & offline guards
- [ ] Create `estc/tests/test_observability.py`. Offline by default (no network); live cases `skipif` on `LANGSMITH_API_KEY` / judge keys. Monkeypatch `run_ticket` for the dataset-assembly test so no real graph/LLM runs.
  **Verify:** `.venv\Scripts\pytest --collect-only estc/tests/test_observability.py` collects ≥ 5 items, no import errors.

### 4.5.4-b Test cases — must include at minimum (AC bar)
- [ ] `test_configure_tracing_off_without_key` → no key ⇒ `configure_tracing() is False` and `LANGSMITH_TRACING=="false"` (**AC-T1**).
- [ ] `test_configure_tracing_on_with_key` → monkeypatch `LANGSMITH_TRACING=true` + a dummy `LANGSMITH_API_KEY` ⇒ returns `True`, sets `LANGSMITH_PROJECT=estc-dev` (**AC-T1**).
- [ ] `test_fixture_integrity` → 20 well-formed rows covering all four intents (**AC-T3**). *(intent coverage asserted from a fixture `intent` hint or by mapping known questions.)*
- [ ] `test_collect_samples_shape` → `run_ticket` monkeypatched to a canned `AgentState`; `_collect_samples()` returns 20 dicts with `question/answer/contexts/ground_truth` of correct types, no judge LLM (**AC-T4**).
- [ ] `test_eval_skips_without_judge` → with judge keys stripped, `ragas_eval.main()` returns `0`, writes no `results.csv` (**AC-T6**).
- [ ] `test_ragas_importable_via_shim` → `ensure_ragas_importable(); import ragas` succeeds (**AC-T6 / 4.5.0**).
- [ ] *(skip-guarded, live)* `test_langsmith_child_runs` → with real key, one `run_ticket` ⇒ `list_runs(project_name='estc-dev')` has ≥ 1 root + ≥ 6 children (**AC-T2**).
  **Verify:** `.venv\Scripts\pytest estc/tests/test_observability.py -v` reports **all green** with ≥ 5 passed (live cases `skipped` on a clean box).

---

## Phase 4.5 Exit Gate

- [ ] **EG-1 (ragas importable, Risk 1 / AC-T6)** — the eval stack imports.
  **Verify:** `.venv\Scripts\python -c "from estc.tests.eval._ragas_compat import ensure_ragas_importable as e; e(); import ragas; print('ok')"` prints `ok`.
- [ ] **EG-2 (observability suite green, AC-T1/T3/T4/T6/T8)** — wiring proven offline.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_observability.py -v --tb=short` reports **0 failed**, ≥ 5 passed (live `skipped`).
- [ ] **EG-3 (no-regression / no graph mutation, AC-T8)** — Phase 4.5 changed no node/graph code.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_graph_build.py estc/tests/test_graph_nodes.py estc/tests/test_rag.py -q` reports **0 failed**; `git diff --name-only HEAD -- estc/services/orchestrator/graph/nodes estc/shared/schemas/agent_state.py` is **empty**.
- [ ] **EG-4 (shortcut parity, AC-T7)** — the one-command entry reproduces the eval.
  **Verify:** `pwsh ./scripts/eval.ps1; echo "exit=$LASTEXITCODE"` exits `0`.
- [ ] **EG-5 (live eval, AC-T5 — run only when a judge key is present)** — real metrics meet the bar.
  **Verify:** with a judge key set, `.venv\Scripts\python estc/tests/eval/ragas_eval.py` writes `estc/tests/eval/results.csv` with all three metric means ≥ 0.80. **Fallback if no key on this box:** mark deferred to Phase 5.6 live run and record the skip.

---

## Risks & Open Questions

1. **ragas/langchain-community import break (the precondition).** Primary fix = `sys.modules` shim for `langchain_community.chat_models.vertexai` (4.5.0-a), chosen over downgrading `langchain-community` because RAG (`ingest.py`) depends on 0.4.2. Risk: a future ragas touches more removed symbols; fallback = pin a compatible pair (4.5.0-b). EG-1 guards it.
2. **Ragas 0.4.x API drift.** Installed ragas (0.4.3) uses `EvaluationDataset`/`SingleTurnSample` and `user_input/response/retrieved_contexts/reference` field names (not the 0.2.x `question/answer/contexts/ground_truth`). The harness maps to the installed API at 4.5.2-b; verifies are outcome-based so a rename doesn't break the plan, but the metric *class* names (`ResponseRelevancy` vs `AnswerRelevancy`) must be resolved against the installed package.
3. **No judge key on the dev box ⇒ AC-T5/AC-T2 skip.** PF-7 detects this. The wiring is fully proven offline (EG-1/2/3/4); the live metric/trace bars (EG-5, AC-T2) defer to a keyed run or Phase 5.6. This is by design (spec § 1.2), not a gap.
4. **Score variance vs the 0.80 bar.** Ragas scores are LLM-judged and non-deterministic; the bar is a **mean over 20**. If a real run lands just under 0.80, mitigation is fixture/ground-truth tightening or judge-model choice — a tuning loop, not a code defect.
5. **Seeded `company_id`s.** Billing/lockout fixtures need ids present in the seed (e.g. `9422`) for faithful account facts; if the seed lacks some, those rows score lower. The fixture sticks to confirmed-seeded ids (open item 4).
6. **LangGraph child-run count.** ≥ 6 assumes the pinned LangGraph emits node spans + internal steps for a 3-node path; if fewer, AC-T2 asserts ≥ executed-node-count and the gate notes it (spec edge case 6).

---

## Out of Scope (explicitly deferred)

- FastAPI `/tickets` + SSE wrapper calling `configure_tracing()` on startup — Phase 4.6.
- Containerizing `orchestrator-app` with LangSmith env wired — Phase 4.6.2.
- Re-running the eval against the **live containerized** orchestrator — Phase 5.6.3.
- A LangSmith *dataset/experiment* upload (vs. local `results.csv`) and regression dashboards — future/observability hardening.
- Any change to node logic, graph topology, or `AgentState` (FR-10) — not in this phase.

---

**Awaiting `Proceed` to begin execution at PF-1.**
