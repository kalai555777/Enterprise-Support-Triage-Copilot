# Execution Plan: Phase 4.1 — LangGraph Shared State Schema
**Source spec:** `.claude/Specs/04-langgraph-rag-spec.md`
**Source plan section:** `docs/plan.md` § Phase 4.1 (task 4.1.1)
**Status:** AWAITING APPROVAL — no code to be executed until user replies `Proceed`.

---

## Context

This plan operationalizes Phase 4.1 of the ESTC roadmap: the single shared `AgentState` Pydantic model that threads through every node of the LangGraph orchestrator (Phase 4.3–4.6) and is the type parameter passed to `StateGraph(AgentState)` (task 4.4.1). The work has three threads that must be done in order:

1. **Schema module** (Python): create `estc/shared/schemas/agent_state.py` with the exact nine-field `AgentState` model from `design.md` § 3, using `Field(default_factory=list)` for the two list fields.
2. **Contract test harness** (Python): `estc/tests/test_agent_state.py` proving field set, defaults, required-field enforcement, list isolation, and serialization round-trip.
3. **Strict typing audit** (tooling): confirm the module passes `mypy --strict` under the existing `shared.schemas.*` override.

**Design note — plan-internal task numbering:** `docs/plan.md` defines only **task 4.1.1** for this phase. Tasks **4.1.2** (test harness) and **4.1.3** (mypy audit) below are *plan-internal expansions* introduced solely to satisfy the spec's acceptance-criteria bars (AC-T7, AC-T8); they are not new roadmap task IDs. The spec's documented deviation — `Field(default_factory=list)` instead of the literal `= []` shown in `design.md` § 3 — is carried through here (spec § 4.1, FR-4). The plan annotation's "(8 fields)" is a miscount; this plan implements all **nine** fields per `design.md`.

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**. A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** Confirm the source spec exists and is the version this plan targets.
  **Verify:** `Get-Content .claude/Specs/04-langgraph-rag-spec.md | Select-String "default_factory"` returns ≥ 1 match.
- [ ] **PF-2** Confirm the venv is the active Python 3.11 toolchain.
  **Verify:** `.venv\Scripts\python --version` reports `Python 3.11.*`.
- [ ] **PF-3** Confirm Pydantic v2 is installed (the spec relies on `Field(default_factory=...)`, `model_fields`, `model_dump`).
  **Verify:** `.venv\Scripts\python -c "import pydantic; print(pydantic.VERSION)"` prints `2.*`.
- [ ] **PF-4** Confirm the target package directory exists and the namespace-package convention holds (no `__init__.py` anywhere under `estc/`).
  **Verify:** `Test-Path estc/shared/schemas` returns `True`; `Get-ChildItem -Recurse estc -Filter __init__.py | Measure-Object | Select-Object -ExpandProperty Count` returns `0`.
- [ ] **PF-5** Confirm no `agent_state.py` already exists (avoid clobbering prior work) and the schemas dir is empty.
  **Verify:** `Test-Path estc/shared/schemas/agent_state.py` returns `False`.
- [ ] **PF-6** Confirm `mypy` is available for the AC-T7 audit (record the gap if absent; 4.1.3 installs it).
  **Verify:** `.venv\Scripts\python -c "import mypy; print(mypy.__version__)"` exits 0, OR record the gap.
- [ ] **PF-7** Confirm the bare-import path resolves from repo root (the plan's AC-T1 verify is invoked without pytest's `pythonpath`).
  **Verify:** `.venv\Scripts\python -c "import estc.shared.config; print('ok')"` prints `ok` (proves the `estc` namespace package is importable from cwd).

---

## Task 4.1.1 — `AgentState` Schema Module

### 4.1.1-a Author the module
- [ ] Create `estc/shared/schemas/agent_state.py` containing exactly the model from spec § 4.1: subclass `pydantic.BaseModel`, nine fields in the order given, `Optional[str]`/`float`/`bool` scalars with their literal defaults, and the two `List[str]` fields using `Field(default_factory=list)`. Import only from `typing` and `pydantic` (leaf-dependency rule, spec § 2.1 / AC-B5). Do **not** add an `__init__.py` (namespace-package convention, spec § 5.1 edge case 4).
  **Verify:** `Get-Content estc/shared/schemas/agent_state.py | Select-String "default_factory=list"` returns exactly 2 matches.

### 4.1.1-b Import & construct (the plan's verbatim verify — AC-T1)
- [ ] Confirm the module imports and a minimal instance constructs with the three required fields.
  **Verify:** `.venv\Scripts\python -c "from estc.shared.schemas.agent_state import AgentState; AgentState(ticket_id='t1', raw_issue_text='x', company_id='9422')"` exits 0. Matches AC-T1 and `docs/plan.md` task 4.1.1 verify.

### 4.1.1-c Exact field set (AC-T2)
- [ ] Confirm the model declares exactly the nine named fields.
  **Verify:** `.venv\Scripts\python -c "from estc.shared.schemas.agent_state import AgentState; assert set(AgentState.model_fields)=={'ticket_id','raw_issue_text','company_id','intent','retrieved_context','agent_draft_response','confidence_score','requires_escalation','execution_logs'}; print('ok')"` prints `ok`.

### 4.1.1-d Defaults (AC-T3)
- [ ] Confirm a minimally-constructed instance has the correct defaults.
  **Verify:** `.venv\Scripts\python -c "from estc.shared.schemas.agent_state import AgentState; s=AgentState(ticket_id='t',raw_issue_text='x',company_id='1'); assert s.intent is None and s.agent_draft_response is None and s.confidence_score==0.0 and s.requires_escalation is False and s.retrieved_context==[] and s.execution_logs==[]; print('ok')"` prints `ok`.

---

## Task 4.1.2 — Schema Contract Test Harness *(plan-internal; satisfies AC-T8)*

### 4.1.2-a Test file
- [ ] Create `estc/tests/test_agent_state.py` importing `AgentState` from `estc.shared.schemas.agent_state`. No fixtures or conftest changes needed — this is a pure-Python unit test with no DB/network dependency.
  **Verify:** `.venv\Scripts\pytest --collect-only estc/tests/test_agent_state.py` collects ≥ 6 items with no import errors.

### 4.1.2-b Test cases — must include at minimum (AC-T2–AC-T6 bar)
- [ ] `test_exact_field_set` → `set(AgentState.model_fields)` equals the nine-name set (AC-T2).
- [ ] `test_defaults` → minimal instance has `intent is None`, `agent_draft_response is None`, `confidence_score == 0.0`, `requires_escalation is False`, empty `retrieved_context` / `execution_logs` (AC-T3).
- [ ] `test_required_fields_enforced` → `AgentState(ticket_id="t1")` raises `pydantic.ValidationError` (AC-T4).
- [ ] `test_list_isolation` → two instances; appending to one's `execution_logs` leaves the other's `== []` and `a.execution_logs is not b.execution_logs` (AC-T5; the FR-4 tripwire).
- [ ] `test_serialization_round_trip` → `AgentState(**a.model_dump()) == a` and `model_dump()` emits all nine keys (AC-T6).
- [ ] `test_confidence_score_coercion` → `confidence_score=1` coerces to `1.0`; `confidence_score="high"` raises `ValidationError` (spec § 5.1 edge case 2).

  **Verify:** `.venv\Scripts\pytest estc/tests/test_agent_state.py -v` reports **all green** with at least 6 passed.

---

## Task 4.1.3 — Strict Typing Audit *(plan-internal; satisfies AC-T7)*

- [ ] Run `mypy --strict` against the schema module, honoring the existing `[[tool.mypy.overrides]] module = "shared.schemas.*"` strict block in `pyproject.toml`. Fix any annotation gaps (there should be none if 4.1.1-a was followed). If `mypy` is absent (PF-6 gap), install via `.venv\Scripts\pip install mypy` first.
  **Verify:** `.venv\Scripts\mypy --strict estc/shared/schemas/agent_state.py` reports `Success: no issues found in 1 source file`.

---

## Phase 4.1 Exit Gate

- [ ] **EG-1 (import & construct, AC-T1 bar)** — Proves the canonical contract is importable at the path every Phase 4 consumer will use.
  **Verify:** `.venv\Scripts\python -c "from estc.shared.schemas.agent_state import AgentState; AgentState(ticket_id='t1', raw_issue_text='x', company_id='9422')"` exits 0.

- [ ] **EG-2 (full contract test sweep)** — Tests all green.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_agent_state.py -v --tb=short` reports **0 failed**.

- [ ] **EG-3 (strict typing clean)** — Schema satisfies the `shared.schemas.*` strict override.
  **Verify:** `.venv\Scripts\mypy --strict estc/shared/schemas/agent_state.py` reports `Success`. **Fallback if `mypy` is unavailable:** re-run EG-2 (runtime validation covers field types via Pydantic), and record the mypy gap as an open item.

---

## Risks & Open Questions

1. **`= []` vs `default_factory` deviation** — The spec deliberately diverges from the verbatim `design.md` snippet (FR-4, § 4.1). Pydantic v2 deep-copies literal `= []` defaults too, so both are safe today; `default_factory` is mandated for explicit per-instance isolation. If the reviewer insists on byte-for-byte fidelity to `design.md`, swap to `= []` — AC-T5 (list isolation) still passes under Pydantic v2. Flagged for sign-off.
2. **Field count (9 vs plan's "8")** — `design.md` § 3 is authoritative and defines nine fields, all consumed downstream. AC-T2 locks the count at nine. No field dropped.
3. **mypy override path skew** — The override key is `shared.schemas.*` while the runtime import is `estc.shared.schemas.*`. EG-3 invokes mypy on the file path directly, so the audit runs regardless of module-name resolution; the override governs strictness when mypy is run package-wide. No action needed, noted for awareness.
4. **Pydantic strict-mode for numerics** — Phase 4.1 deliberately leaves default (coercive) numeric handling so node code can return `1` or `"0.9"` without a `ValidationError` (spec § 5.1 edge case 2). If a future phase wants strict numerics, add `model_config = ConfigDict(strict=True)` then — out of scope here.

---

## Out of Scope (explicitly deferred)

- RAG pipeline (`ingest.py`, `retriever.py`) — Phase 4.2.
- LangGraph nodes (`classify`, `route_by_intent`, the four agents, `supervisor_review`) — Phase 4.3.
- Graph wiring `StateGraph(AgentState)` + `run_ticket` — Phase 4.4.
- LangSmith tracing / Ragas eval — Phase 4.5.
- FastAPI `/tickets` + SSE serialization of `AgentState` — Phase 4.6.

---

**Awaiting `Proceed` to begin execution at PF-1.**
