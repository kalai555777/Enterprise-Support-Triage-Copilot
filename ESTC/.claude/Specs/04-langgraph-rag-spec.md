# Architectural Specification: Phase 4.1 — LangGraph Shared State Schema

**Status:** DRAFT / PROPOSED
**Associated Tasks:** Task 4.1.1 (`docs/plan.md` § Phase 4.1 — Shared Schema)
**Target Files:**
- `estc/shared/schemas/agent_state.py` (new — the `AgentState` Pydantic model)
- `estc/shared/schemas/` (existing empty package directory; namespace-package convention, no `__init__.py`)
- `estc/tests/test_agent_state.py` (new — schema contract tests)

---


## 1. Executive Summary & Problem Statement

### 1.1 Objective & Context
This sub-phase delivers the **single shared state object** that flows through every node of the LangGraph execution engine described in `docs/design.md` § Component D. Phase 4 builds a stateful machine implementing `classify → router → {billing_agent | bug_agent | feature_agent | lockout_agent} → supervisor_review` (`docs/plan.md` § Phase 4 goal). LangGraph's programming model is fundamentally a **reducer over a typed state container**: each node receives the current state, performs work (a classifier call, an MCP tool call, a RAG retrieval, an LLM draft), and returns a partial mutation that LangGraph merges back. That container is `AgentState`.

Before any node (4.3.1–4.3.7), any graph wiring (4.4.1 via `StateGraph(AgentState)`), or any observability hook (4.5) can be written, the state schema must exist and be importable as the canonical contract. It is the literal type parameter passed to `StateGraph(...)` in task 4.4.1 and the return type of the `run_ticket(...)` entrypoint in task 4.4.2. Every downstream task in Phase 4 reads or writes one of its fields by name:

- `classify` (4.3.1) writes `state.intent`.
- `route_by_intent` (4.3.2) reads `state.intent`.
- `billing_agent` / `bug_agent` / `feature_agent` / `lockout_agent` (4.3.3–4.3.6) write `state.retrieved_context`, `state.agent_draft_response`, `state.confidence_score`, `state.requires_escalation`, and append to `state.execution_logs`.
- `supervisor_review` (4.3.7) reads `state.confidence_score` and `state.requires_escalation`, and appends `"ESCALATE"` / `"AUTO_APPROVED"` to `state.execution_logs`.

This phase is small in code but **load-bearing for the entire orchestrator**: a wrong field name or type here propagates as a defect into all six nodes.

### 1.2 Core Problem Statement
A multi-node agent graph needs a **single, typed, serializable source of truth** for per-ticket state that is (a) safe to pass by reference across nodes running in sequence, (b) validated at construction so malformed inputs fail fast at the graph boundary rather than three nodes deep, (c) free of shared-mutable-default hazards (the classic Python `def f(x=[])` bug — fatal when one `AgentState` per ticket must have its **own** `execution_logs` list), and (d) an exact match to the contract published in `design.md` § 3 so that this independently-built schema and the nodes coded against it agree by construction. We need that object specified, implemented, and contract-tested before node development begins.

---

## 2. System Boundaries & Constraints

### 2.1 Architectural Boundaries
- **Upstream Trigger / Consumer:**
  - The orchestrator entrypoint `run_ticket(ticket_id, text, company_id)` (task 4.4.2) constructs the initial `AgentState(ticket_id=..., raw_issue_text=text, company_id=...)` with the three required fields and defaults for the rest.
  - `StateGraph(AgentState)` (task 4.4.1) consumes the class itself as its state-channel definition.
  - The FastAPI wrapper (task 4.6.1) serializes `AgentState` to JSON for the `GET /tickets/{id}/stream` SSE payloads.
- **Downstream Dependencies:** **None at runtime.** This module imports only `pydantic` and `typing`. It must not import `langgraph`, `langchain`, `psycopg`, MCP clients, or any service module — the schema is a leaf dependency that everything else points at, and a cyclic or heavyweight import here would poison every consumer. It sits beside the existing `estc/shared/config.py` (Phase 1.4.3) under the shared package.

### 2.2 Technical & Operational Constraints
- **Performance / Latency:** Negligible. Model construction and validation are sub-millisecond. The only performance-relevant rule is **no shared mutable defaults** (use `Field(default_factory=list)`), which is correctness rather than speed.
- **Security & Compliance:** `raw_issue_text` may contain customer PII; `AgentState` is not logged in full at INFO level by any consumer — only `execution_logs` (which holds node-status breadcrumbs, never raw PII) is surfaced to the SSE stream and operator UI. This is a layering rule for consumers, but it is stated here because the schema is where the PII-bearing field is introduced.
- **Resource Limits:** None beyond the process. No connections, no files, no network.
- **Typing strictness:** `pyproject.toml` declares `[[tool.mypy.overrides]] module = "shared.schemas.*"` with `strict = true`. This module **must** pass `mypy --strict`: fully annotated fields, no implicit `Any`, no untyped defaults. (Note: the override is keyed on `shared.schemas.*`; the runtime import path is `estc.shared.schemas.*` via the PEP 420 namespace package — see § 5.1 edge case 4.)

---

## 3. Functional Requirements

- **FR-1 (Module location & import path, task 4.1.1):** The model lives at `estc/shared/schemas/agent_state.py` and is importable as `from estc.shared.schemas.agent_state import AgentState`. The project uses **PEP 420 namespace packages** (there are no `__init__.py` files anywhere under `estc/`), and pytest is configured with `pythonpath = ["."]`; the bare `python -c` verify command resolves `estc` because the project root is on `sys.path` when invoked from the repository root.
- **FR-2 (Exact field set from design.md § 3):** `AgentState` subclasses `pydantic.BaseModel` and declares **exactly these nine fields**, in this order, with these types and defaults:

  | # | Field | Type | Default | Written by |
  |---|---|---|---|---|
  | 1 | `ticket_id` | `str` | *(required)* | caller / `run_ticket` |
  | 2 | `raw_issue_text` | `str` | *(required)* | caller / `run_ticket` |
  | 3 | `company_id` | `str` | *(required)* | caller / `run_ticket` |
  | 4 | `intent` | `Optional[str]` | `None` | `classify` (4.3.1) |
  | 5 | `retrieved_context` | `List[str]` | `[]` (via `default_factory`) | agent nodes (4.3.3–4.3.6) |
  | 6 | `agent_draft_response` | `Optional[str]` | `None` | agent nodes (4.3.3–4.3.6) |
  | 7 | `confidence_score` | `float` | `0.0` | agent nodes / `supervisor_review` |
  | 8 | `requires_escalation` | `bool` | `False` | `lockout_agent`, `supervisor_review` |
  | 9 | `execution_logs` | `List[str]` | `[]` (via `default_factory`) | every node (append-only) |

- **FR-3 (Required-field enforcement):** Constructing `AgentState()` without `ticket_id`, `raw_issue_text`, **and** `company_id` raises `pydantic.ValidationError`. These three have no default and are the mandatory ticket identity.
- **FR-4 (Per-instance list isolation):** `retrieved_context` and `execution_logs` **must** use `Field(default_factory=list)` (not the literal `= []` shown verbatim in the design doc). Two independently constructed `AgentState` instances must not share the same list object — appending to one ticket's `execution_logs` must never leak into another's. This is the single most important correctness property of the file.
- **FR-5 (mypy --strict cleanliness):** The module passes `mypy --strict` under the `shared.schemas.*` override already present in `pyproject.toml`. All fields are explicitly typed; `default_factory=list` is acceptable to the strict checker for `List[str]`.

---

## 4. Detailed Component Specifications & API Contracts

### 4.1 Interface Code & Data Shapes

**`estc/shared/schemas/agent_state.py` (target shape):**

```python
from typing import List, Optional

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Shared LangGraph state threaded through every node of the triage graph.

    Mirrors docs/design.md § 3 exactly. The first three fields are the required
    ticket identity supplied at graph entry (run_ticket, task 4.4.2); the rest
    are populated as the graph advances through
    classify -> router -> {billing | bug | feature | lockout} -> supervisor_review.
    """

    # --- Required ticket identity (graph inputs) ---
    ticket_id: str
    raw_issue_text: str
    company_id: str

    # --- Populated by classify / router (4.3.1-4.3.2) ---
    intent: Optional[str] = None

    # --- Populated by the worker agent nodes (4.3.3-4.3.6) ---
    retrieved_context: List[str] = Field(default_factory=list)
    agent_draft_response: Optional[str] = None
    confidence_score: float = 0.0
    requires_escalation: bool = False

    # --- Append-only audit trail, written by every node ---
    execution_logs: List[str] = Field(default_factory=list)
```

**Design-doc fidelity note (intentional deviation):** `design.md` § 3 writes the list fields as `retrieved_context: List[str] = []` and `execution_logs: List[str] = []`. This spec mandates `Field(default_factory=list)` instead. The *observable shape* is identical (an empty list default), but the literal `= []` form is a shared-mutable-default hazard: in LangGraph every ticket gets its own `AgentState`, and a shared backing list would cross-contaminate ticket logs. Pydantic v2 happens to deep-copy literal defaults, so both forms are safe **today**, but `default_factory` makes the per-instance guarantee explicit and is the form that survives a future refactor to a plain dataclass or `TypedDict` reducer. This is the only deviation from the verbatim design-doc snippet.

**Field-count reconciliation:** `docs/plan.md` task 4.1.1 parenthetically says *"the exact `AgentState` Pydantic model from `design.md` section 3 (8 fields)"*. The authoritative source — `design.md` § 3 — actually defines **nine** fields, and all nine are referenced by downstream Phase 4 tasks (see § 1.1). The "(8 fields)" note is a miscount in the plan annotation; this spec follows `design.md` and implements all nine. No field is dropped, because dropping any one would break a named verify step in 4.3.x.

### 4.2 Endpoint / Method Contracts

This module exposes no network surface — it is a pure data contract. The "contract" is the class constructor and its serialization behavior:

- **Constructor `AgentState(**fields)`**
  - **Required input:** `ticket_id: str`, `raw_issue_text: str`, `company_id: str`.
  - **Optional input:** `intent`, `retrieved_context`, `agent_draft_response`, `confidence_score`, `requires_escalation`, `execution_logs` (all defaulted per FR-2).
  - **Output:** a validated `AgentState` instance. Raises `pydantic.ValidationError` on missing required fields or type-incoercible values.
- **Serialization (consumed by task 4.6.1 SSE):** `state.model_dump()` → `dict` and `state.model_dump_json()` → `str` produce a JSON object with all nine keys. This is the wire shape the orchestrator FastAPI app emits per node transition.
- **Mutation pattern (consumed by LangGraph nodes 4.3.x):** nodes return partial updates (e.g. `{"intent": "billing"}`) or mutate-and-return; the canonical field names above are the only valid keys.

---

## 5. Edge Cases & Error Handling

### 5.1 Anticipated Edge Cases
1. **Missing required field at graph entry:** `AgentState(ticket_id="t1", raw_issue_text="x")` (no `company_id`) raises `ValidationError`. This is desired — it fails at the `run_ticket` boundary, not three nodes later inside `billing_agent` when it tries to call the Postgres MCP with a missing company id.
2. **`confidence_score` supplied as `int` or coercible string:** Pydantic coerces `1` → `1.0` and `"0.8"` → `0.8` by default. This is acceptable: classifier and agent nodes may hand back an int-typed `1`; we do not want a `ValidationError` for `1` vs `1.0`. (If strict numeric typing is later desired, a `model_config = ConfigDict(strict=True)` can be added, but Phase 4.1 does **not** impose it, to keep node code ergonomic.)
3. **Concurrent tickets / list isolation:** Two `AgentState` instances created back-to-back must own distinct `execution_logs` lists. Verified directly by FR-4's test (`a.execution_logs is not b.execution_logs`). This is the regression guard against the mutable-default bug.
4. **Import-path / mypy-override skew:** The runtime import is `estc.shared.schemas.agent_state` (namespace package rooted at the repo), while the mypy override in `pyproject.toml` is keyed `shared.schemas.*`. On Windows + the configured `pythonpath = ["."]`, both the `python -c` verify command and pytest resolve the module. If a future contributor moves to installed-package mode (`pip install -e .`), the namespace package still resolves; no `__init__.py` is required and adding one to only part of the tree would *break* the namespace resolution — so none is added (matching the existing convention used by `mcp_postgres`, `mcp_github`, and `classifier_api`).
5. **Extra/unknown field passed by a node (e.g. a typo `state.intnet`):** Default Pydantic v2 behavior **ignores** unknown kwargs at construction but a typo'd *attribute write* (`state.intnet = "x"`) silently sets a non-field attribute and is **not** persisted in `model_dump()`. Consumers must use the exact field names in § 4.1; this is why the field table is the normative contract.

### 5.2 Error Handling & State Recovery Matrix

| Trigger / Exception | Handled State / Action | Fallback Behavior / Mitigation |
|---|---|---|
| Missing `ticket_id` / `raw_issue_text` / `company_id` | `pydantic.ValidationError` raised at construction | `run_ticket` (4.4.2) lets it propagate to the FastAPI layer (4.6.1) → `422`/`400`; ticket never enters the graph in a half-formed state |
| `confidence_score` as `int`/numeric-string | Coerced to `float` silently | No error; node code may emit `1` or `"0.9"` without ceremony |
| Type-incoercible value (e.g. `confidence_score="high"`) | `pydantic.ValidationError` raised | Surfaces as a defect in the offending node's test (4.3.x); fail-fast, not silent |
| Literal `= []` mutable default (anti-pattern) | **Prevented by construction** — FR-4 mandates `default_factory` | Test `a.execution_logs is not b.execution_logs` is the CI tripwire |
| Node writes a misspelled attribute name | Silently set as non-field attr; dropped by `model_dump()` | Caught in node integration tests (4.3.x verify steps that assert on specific fields); § 4.1 table is the canonical name source |
| Consumer accidentally imports a heavyweight dep via this module | **Prevented by construction** — module imports only `pydantic`/`typing` | Static review: any import beyond `pydantic`/`typing` in this file is a review-blocking defect (§ 2.1) |

---

## 6. Acceptance Criteria

### 6.1 Technical Acceptance Criteria
- **AC-T1 (Import & construct — the plan's verbatim verify, task 4.1.1):**
  `.venv\Scripts\python -c "from estc.shared.schemas.agent_state import AgentState; AgentState(ticket_id='t1', raw_issue_text='x', company_id='9422')"` exits `0`.
- **AC-T2 (Exact field set):** `set(AgentState.model_fields.keys())` equals
  `{"ticket_id", "raw_issue_text", "company_id", "intent", "retrieved_context", "agent_draft_response", "confidence_score", "requires_escalation", "execution_logs"}` — nine fields, no more, no fewer.
- **AC-T3 (Defaults):** A minimally-constructed instance has `intent is None`, `agent_draft_response is None`, `confidence_score == 0.0`, `requires_escalation is False`, `retrieved_context == []`, `execution_logs == []`.
- **AC-T4 (Required-field enforcement):** `AgentState(ticket_id="t1")` raises `pydantic.ValidationError` (missing `raw_issue_text`, `company_id`).
- **AC-T5 (List isolation — the FR-4 tripwire):** For `a = AgentState(ticket_id="a", raw_issue_text="x", company_id="1")` and `b = AgentState(ticket_id="b", raw_issue_text="y", company_id="2")`, after `a.execution_logs.append("classified")`, `b.execution_logs == []` and `a.execution_logs is not b.execution_logs`.
- **AC-T6 (Serialization round-trip):** `AgentState(**a.model_dump()) == a`, and `model_dump()` emits all nine keys.
- **AC-T7 (Strict typing):** `mypy --strict estc/shared/schemas/agent_state.py` reports no errors (honoring the existing `shared.schemas.*` strict override).
- **AC-T8 (Test suite):** `.venv\Scripts\pytest tests/test_agent_state.py -v` is all green, covering AC-T2 through AC-T6 at minimum.

### 6.2 Business & Functional Alignment
- **AC-B1 (Design fidelity):** The nine fields and their types map 1:1 to the `AgentState` snippet in `design.md` § 3, with the single documented `default_factory` deviation (§ 4.1) that changes no observable shape.
- **AC-B2 (Downstream readiness — Phase 4.3):** Every field that tasks 4.3.1–4.3.7 mutate or read (`intent`, `retrieved_context`, `agent_draft_response`, `confidence_score`, `requires_escalation`, `execution_logs`) exists with the name and type those node verify-steps assume — e.g. `supervisor_review` (4.3.7) can evaluate `state.confidence_score < 0.70` and append to `state.execution_logs` with no schema change.
- **AC-B3 (Graph-wiring readiness — Phase 4.4):** `StateGraph(AgentState)` (4.4.1) accepts the class as its state channel, and `run_ticket(...) -> AgentState` (4.4.2) has a concrete return type. No further schema work is required to compile the graph.
- **AC-B4 (Observability readiness — Phase 4.6):** `model_dump_json()` yields the SSE payload shape the orchestrator FastAPI app (4.6.1) streams per node transition, so the schema unblocks the streaming contract without modification.
- **AC-B5 (Leaf-dependency discipline):** The module imports nothing beyond `pydantic`/`typing`, guaranteeing every Phase 4 component can depend on it without risking an import cycle through `langgraph`, MCP clients, or service code — honoring the decoupled-microservices intent of `design.md` § 1.
