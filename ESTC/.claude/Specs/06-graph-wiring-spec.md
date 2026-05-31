# Architectural Specification: Phase 4.4 — Graph Wiring

**Status:** DRAFT / PROPOSED
**Associated Tasks:** Tasks 4.4.1 – 4.4.2 (`docs/plan.md` § Phase 4.4 — Graph Wiring)
**Target Files:**
- `estc/services/orchestrator/graph/build.py` (new — `StateGraph(AgentState)` assembly + module-level compiled `graph`, task 4.4.1; `run_ticket` async streaming entrypoint, task 4.4.2)
- `estc/services/orchestrator/graph/` (existing namespace-package directory; no `__init__.py` added — PEP 420)
- `estc/tests/test_graph_build.py` (new — Mermaid-topology test + end-to-end `run_ticket` integration test)

**Consumes (unchanged):** the seven Phase 4.3 callables in `estc/services/orchestrator/graph/nodes/*` and `estc/shared/schemas/agent_state.py`.

---


## 1. Executive Summary & Problem Statement

### 1.1 Objective & Context

This sub-phase **compiles the seven Phase 4.3 callables into a single runnable LangGraph state machine** and exposes the one async entrypoint the rest of the system will call. Phase 4.3 deliberately stopped short of the graph: it shipped independently-importable, unit-tested node functions (`classify`, `route_by_intent`, the four worker agents, `supervisor_review`) but no edges. Phase 4.4 is where those callables become a topology:

```
START ─▶ classify ─▶ route_by_intent ─▶ { billing_agent | bug_agent | feature_agent | lockout_agent } ─▶ supervisor_review ─▶ END
```

Two concrete artifacts are produced, both in `estc/services/orchestrator/graph/build.py`:

1. **`graph`** (task 4.4.1) — a module-level *compiled* `StateGraph(AgentState)`. `classify` is the entry node; `route_by_intent` is registered as a **conditional edge** out of `classify` that dispatches to exactly one of the four worker nodes; every worker has a static edge into the single `supervisor_review` terminal node; `supervisor_review` flows to `END`. The graph is compiled with a **`MemorySaver` checkpointer** so a run is resumable and inspectable by `thread_id`.
2. **`run_ticket(ticket_id, text, company_id) -> AgentState`** (task 4.4.2) — an `async` entrypoint that builds the initial `AgentState`, drives it through the compiled graph **while streaming one event per node transition**, and returns the fully-populated terminal `AgentState`.

This is the integration capstone of Phase 4: after 4.4, a single `await run_ticket(...)` call turns a raw ticket into a grounded draft with a confidence score and an escalation decision. Phases 4.5 (LangSmith/Ragas observability), 4.6 (FastAPI/SSE wrapper), and 5 (Streamlit UI) all build directly on the `graph` object and the `run_ticket` / streaming surface defined here.

### 1.2 Core Problem Statement

The node functions exist but are inert — nothing routes a ticket from `classify` through the correct worker to `supervisor_review`, and nothing assembles the initial state, threads partial updates between nodes, or surfaces per-node progress. The technical challenge of this phase is **faithful, low-overhead orchestration**: wire the conditional fan-out and the fan-in to a single supervisor exactly as `design.md` § Component D specifies, with no behavioral drift from the topology the 4.3 tests already assume (`route_by_intent` returns the literal worker-node names; nodes return *full extended* `execution_logs` because `AgentState` has no channel reducer). The wiring must (a) compile to a graph whose rendered Mermaid shows **all six nodes** (the 4.4.1 verify), (b) run **end-to-end in < 10 s** against the seeded DB (the 4.4.2 verify), and (c) **stream node-transition events** so the Phase 4.6 SSE endpoint and Phase 5 real-time map have a per-node feed to consume — all while preserving the project's offline-deterministic property (no LLM keys, GitHub in mock mode) so the suite stays green on a clean checkout.

---

## 2. System Boundaries & Constraints

### 2.1 Architectural Boundaries

- **Upstream Trigger / Consumer:**
  - `run_ticket` (4.4.2) is the public entrypoint. Its first runtime caller is the Phase 4.6 orchestrator FastAPI app (`POST /tickets`, `GET /tickets/{id}/stream`), and ultimately the Phase 5 Streamlit UI. In this phase the only caller is the 4.4.2 end-to-end test.
  - The module-level `graph` object is consumed directly by Phase 4.5 (LangSmith tracing wraps `graph.astream`/`ainvoke`) and Phase 4.6 (the SSE endpoint iterates the same stream).
- **Downstream Dependencies (what the *compiled graph* invokes at runtime):**
  - The seven Phase 4.3 callables imported from `estc.services.orchestrator.graph.nodes.*` — unchanged in signature. The graph is the *sole new code*; it adds edges, not logic.
  - Transitively (through the nodes): the classifier API (`classify`), the Postgres MCP (`billing_agent`, `lockout_agent`), the GitHub MCP (`bug_agent`), the Chroma RAG retriever (`billing/bug/feature`), and the LLM helper `graph/llm.py` (all workers). Phase 4.4 introduces **no new external dependency** — it only orchestrates the ones 4.3 already wired.
  - `langgraph`: `StateGraph`, `START`, `END` (from `langgraph.graph`) and `MemorySaver` (from `langgraph.checkpoint.memory`). Both import paths verified present in the project `.venv`.
- **State-channel boundary:** `AgentState` (Pydantic `BaseModel`) is the graph's single state schema. Nodes return `dict[str, object]` partial updates; LangGraph merges each returned key into the channel under **last-writer-wins** (no `Annotated[..., reducer]` is defined on `AgentState`). The graph topology is strictly linear per run — `classify` → exactly one worker → `supervisor_review` — so no two nodes write the same channel concurrently; the manual `state.execution_logs + [...]` append convention from 4.3 is therefore correct and is *not* changed here (see § 5.1 edge case 3).
- **Checkpointer boundary:** `MemorySaver` is an **in-process, non-durable** checkpointer. It satisfies the 4.4.1 "run-resume" requirement for a single process lifetime and keys runs by the `thread_id` in the run config. It is deliberately *not* a Postgres/SQLite-backed saver — cross-process durability is out of scope for Phase 4 and would couple the orchestrator to a checkpoint DB the design does not call for.

### 2.2 Technical & Operational Constraints

- **Async discipline (project rule, memory `feedback_mcp_async`):** `run_ticket` is `async def` and drives the graph via the **async** streaming API (`graph.astream(...)`), never the blocking `.invoke`/`.stream`. The four worker nodes are coroutines; LangGraph awaits them on the running loop. The two pure nodes (`route_by_intent`, `supervisor_review`) stay synchronous. On Windows the test loop uses `WindowsSelectorEventLoopPolicy` (already set in `conftest.py`).
- **Performance / Latency:** End-to-end `run_ticket` budget **< 10 s** (plan 4.4.2). Composed from the 4.3 per-node soft budgets (`classify` < 200 ms, each MCP call < 150 ms, each RAG retrieval < 1 s, LLM dominated by provider — offline template path is sub-ms). The graph adds only merge/dispatch overhead (negligible). The compiled `graph` is built **once at import** so per-call latency excludes graph construction and node-import cost.
- **Determinism / offline-first:** The 4.4.1 Mermaid verify and the 4.4.2 end-to-end test must pass with no LLM keys and GitHub in mock mode (`conftest.py`). The end-to-end test reaches the live seeded Postgres (the 4.4.2 verify says "against the seeded DB"); the topology/Mermaid test is fully offline and DB-independent.
- **Security & Compliance:** No new I/O surface — the graph cannot reach any dependency the 4.3 nodes can't already reach (read-only MCP tool schemas only; no raw SQL/shell/REST). `raw_issue_text` (potential PII) is carried in `AgentState` but **never** written into `execution_logs`; the streamed events expose only node names and PII-free breadcrumb updates, preserving the audit-trail rule for the Phase 4.6 SSE feed.
- **Resource Limits:** One process-wide `MemorySaver` instance backs every run; runs are isolated by `thread_id`. `MemorySaver` retains checkpoints in memory for the process lifetime — acceptable for the dev/single-worker orchestrator; a note is filed for Phase 4.6 to bound or evict if ticket volume grows.
- **Typing:** `build.py` lives under `estc/services/orchestrator/...`, outside the `shared.schemas.*` strict-mypy override; baseline `mypy` applies and all public signatures are annotated (`run_ticket(...) -> AgentState`; the streaming helper yields `tuple[str, dict[str, Any]]`).
- **Packaging:** No `__init__.py` is added under `graph/` (PEP 420 namespace packages, consistent with `nodes/`, `rag/`, `mcp_postgres`, `mcp_github`). The module is imported as `estc.services.orchestrator.graph.build`.

---

## 3. Functional Requirements

- **FR-1 (Graph assembly — task 4.4.1):** `build.py` constructs `StateGraph(AgentState)` and registers exactly **six nodes** under the names `"classify"`, `"billing_agent"`, `"bug_agent"`, `"feature_agent"`, `"lockout_agent"`, `"supervisor_review"`, bound to the corresponding Phase 4.3 callables. `route_by_intent` is **not** registered as a node — it is the conditional-edge function (FR-3).
- **FR-2 (Entry & terminal edges):** A static edge `START → "classify"` sets the entry point. A static edge `"supervisor_review" → END` terminates the graph. Each of the four worker nodes has a static edge `worker → "supervisor_review"` (fan-in to the single compliance gate).
- **FR-3 (Conditional fan-out — task 4.4.1):** `add_conditional_edges("classify", route_by_intent, mapping)` registers the dispatch out of `classify`. Because `route_by_intent` already returns the literal target-node names (`"billing_agent" | "bug_agent" | "feature_agent" | "lockout_agent"`), the mapping is the identity over those four names `{name: name}` — passed explicitly so the rendered Mermaid lists exactly the four legal targets and no spurious edges.
- **FR-4 (Checkpointer — task 4.4.1):** The graph is compiled with a `MemorySaver` checkpointer (`builder.compile(checkpointer=MemorySaver())`) to satisfy "run-resume". A `build_graph(checkpointer=None)` factory allows a test/caller to inject an alternative saver; when `None`, a fresh `MemorySaver` is used.
- **FR-5 (Module-level compiled graph — task 4.4.1 verify target):** `build.py` exposes a module-level `graph` (the compiled `StateGraph`) such that `from estc.services.orchestrator.graph.build import graph` succeeds and `graph.get_graph().draw_mermaid()` renders a diagram containing **all six node names**. `graph` is built once at import via the `build_graph()` factory.
- **FR-6 (`run_ticket` entrypoint — task 4.4.2):** `async def run_ticket(ticket_id: str, text: str, company_id: str) -> AgentState` (a) builds `AgentState(ticket_id=ticket_id, raw_issue_text=text, company_id=company_id)`, (b) runs it through `graph` under a run config carrying `configurable.thread_id = ticket_id`, (c) **streams node-transition events** while running, and (d) returns the final merged `AgentState` (intent set, a non-empty `agent_draft_response`, a `confidence_score > 0`, and an `execution_logs` trail ending in `"AUTO_APPROVED"` or `"ESCALATE"`).
- **FR-7 (Streaming surface — task 4.4.2):** Streaming is exposed as an `async` generator `astream_ticket(ticket_id, text, company_id, *, config=None) -> AsyncIterator[tuple[str, dict[str, Any]]]` that yields `(node_name, state_update)` **once per node transition** (built on `graph.astream(..., stream_mode="updates")`). `run_ticket` consumes this same generator, so the result path and the event path are identical code (no divergence between "what the test sees" and "what the SSE endpoint sees" in Phase 4.6).
- **FR-8 (Final-state assembly):** After the stream is exhausted, `run_ticket` obtains the terminal state from the checkpointer (`graph.get_state(config).values`) keyed by the run's `thread_id` and returns it as a validated `AgentState(**values)`. (Reading from the checkpointer rather than the last streamed delta guarantees the *fully merged* state, independent of `stream_mode`.)
- **FR-9 (Topology fidelity / no logic drift):** Phase 4.4 adds **only** edges and the entrypoint. It changes no node body, no node signature, and does **not** introduce an `execution_logs` reducer — the 4.3 "return full extended list" convention remains correct under the strictly-linear per-run path (§ 5.1 edge case 3). Any future reducer change is explicitly deferred.
- **FR-10 (Namespace-package convention):** No `__init__.py` is added under `graph/`; the module is importable as `estc.services.orchestrator.graph.build` via the repo's `pythonpath = ["."]` (PEP 420), consistent with every other `estc` package.

---

## 4. Detailed Component Specifications & API Contracts

### 4.1 Interface Code & Data Shapes

**`estc/services/orchestrator/graph/build.py`:**

```python
from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from estc.services.orchestrator.graph.nodes.billing_agent import billing_agent
from estc.services.orchestrator.graph.nodes.bug_agent import bug_agent
from estc.services.orchestrator.graph.nodes.classify import classify
from estc.services.orchestrator.graph.nodes.feature_agent import feature_agent
from estc.services.orchestrator.graph.nodes.lockout_agent import lockout_agent
from estc.services.orchestrator.graph.nodes.router import route_by_intent
from estc.services.orchestrator.graph.nodes.supervisor import supervisor_review
from estc.shared.schemas.agent_state import AgentState

# Worker nodes reachable from classify; each routes back into the single supervisor gate.
_WORKERS = ("billing_agent", "bug_agent", "feature_agent", "lockout_agent")


def build_graph(checkpointer: Optional[BaseCheckpointSaver] = None) -> Any:
    """Wire and compile the triage state machine.

    route_by_intent already returns the worker node name verbatim, so the conditional
    mapping is the identity over _WORKERS — passing it explicitly keeps the rendered
    Mermaid readable and documents the only legal dispatch targets.
    """
    builder = StateGraph(AgentState)

    builder.add_node("classify", classify)
    builder.add_node("billing_agent", billing_agent)
    builder.add_node("bug_agent", bug_agent)
    builder.add_node("feature_agent", feature_agent)
    builder.add_node("lockout_agent", lockout_agent)
    builder.add_node("supervisor_review", supervisor_review)

    builder.add_edge(START, "classify")
    builder.add_conditional_edges("classify", route_by_intent, {n: n for n in _WORKERS})
    for worker in _WORKERS:
        builder.add_edge(worker, "supervisor_review")
    builder.add_edge("supervisor_review", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())


# Module-level compiled graph (the 4.4.1 verify target). One MemorySaver backs every
# run; runs are isolated by the thread_id supplied in the run config.
graph = build_graph()


async def astream_ticket(
    ticket_id: str,
    text: str,
    company_id: str,
    *,
    config: Optional[dict[str, Any]] = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Drive one ticket through the graph, yielding (node_name, state_update) per node
    transition. This is the per-event feed the Phase 4.6 SSE endpoint will consume."""
    initial = AgentState(ticket_id=ticket_id, raw_issue_text=text, company_id=company_id)
    cfg = config or {"configurable": {"thread_id": ticket_id}}
    async for chunk in graph.astream(initial, config=cfg, stream_mode="updates"):
        for node_name, update in chunk.items():
            yield node_name, (update or {})


async def run_ticket(ticket_id: str, text: str, company_id: str) -> AgentState:
    """Async entrypoint (task 4.4.2): run a ticket end-to-end and return the final
    AgentState. Consumes the node-event stream (so result and SSE paths share code),
    then reads the fully-merged state back from the checkpointer by thread_id."""
    cfg = {"configurable": {"thread_id": ticket_id}}
    async for _node, _update in astream_ticket(ticket_id, text, company_id, config=cfg):
        pass
    snapshot = graph.get_state(cfg)
    return AgentState(**snapshot.values)
```

### 4.2 Endpoint / Method Contracts

| Function / Object | Signature | Reads | Produces | I/O |
|---|---|---|---|---|
| `build_graph` (4.4.1) | `(checkpointer=None) -> CompiledStateGraph` | the 7 node callables | compiled graph w/ `MemorySaver` | none (pure assembly) |
| `graph` (4.4.1) | module-level `CompiledStateGraph` | — | `.get_graph().draw_mermaid()` → 6-node diagram | none at import |
| `astream_ticket` (4.4.2) | `async (ticket_id, text, company_id, *, config=None) -> AsyncIterator[tuple[str, dict]]` | the three inputs | per-node `(name, update)` events | drives graph (classifier/MCP/RAG/LLM transitively) |
| `run_ticket` (4.4.2) | `async (ticket_id, text, company_id) -> AgentState` | the three inputs | terminal `AgentState` | same as above + `get_state` |

- **Run config contract:** every invocation passes `{"configurable": {"thread_id": <ticket_id>}}`. The `thread_id` is the checkpoint partition key — reusing a `ticket_id` resumes/extends that thread's checkpoint; distinct ids are isolated runs.
- **Stream-mode contract:** `astream_ticket` uses `stream_mode="updates"` → each yielded chunk is `{node_name: partial_update_dict}`; the helper flattens it to `(node_name, update)`. (Phase 4.6 may additionally request `"values"` for full-state snapshots; the helper signature leaves room via `config`.)
- **Return contract:** a populated `AgentState` where `intent ∈ {billing,bug,feature,lockout}`, `agent_draft_response` is non-empty, `confidence_score > 0`, and `execution_logs[-1] ∈ {"AUTO_APPROVED","ESCALATE"}`.

---

## 5. Edge Cases & Error Handling

### 5.1 Anticipated Edge Cases

1. **`thread_id` reuse across runs.** Two `run_ticket` calls with the same `ticket_id` share a `MemorySaver` thread, so the second call resumes the first's checkpoint instead of starting clean. Mitigation: `ticket_id` is the natural unique key per ticket; the end-to-end test uses a unique id per run. Documented because it is the most likely surprise once Phase 4.6 retries a ticket — a retry should either reuse the id intentionally (resume) or mint a new one (fresh run).
2. **Unknown / `None` intent reaching the conditional edge.** `route_by_intent` already falls back to `"billing_agent"` for an unrecognized/`None` intent (Phase 4.3 FR-2). Because the conditional mapping only lists the four worker names, the fallback return value is always a registered target — the graph never dead-ends or raises `KeyError` at dispatch.
3. **Log append vs. overwrite under the linear path.** `AgentState` defines **no channel reducer**, so a returned `execution_logs` value *replaces* the channel. The 4.3 nodes each return `state.execution_logs + [breadcrumb]`, i.e. the full extended list read from the *current* state. Because the per-run path is strictly linear (`classify` → one worker → `supervisor_review`, never two writers at once), each node observes the prior node's merged logs and extends them — accumulation is correct **without** an `add` reducer. Phase 4.4 must **not** "fix" this by adding a reducer (that would double-append); the end-to-end test asserts the final log ordering to lock this in.
4. **Empty / partial final state from the stream.** With `stream_mode="updates"` the last streamed chunk is only the *delta* from the last node, not the full state. `run_ticket` therefore reconstructs the terminal state from `graph.get_state(cfg).values` (the fully merged checkpoint), not from the last delta — avoiding a truncated `AgentState`.
5. **Classifier / MCP failure mid-run.** A node may raise (e.g. classifier 5xx via `raise_for_status`, or a Postgres connection error). The exception propagates out of `graph.astream`, so `run_ticket` raises rather than returning a half-populated state. Phase 4.4 fails fast and visibly; Phase 4.6 owns the HTTP error mapping and any retry policy. The 5 s classifier client timeout keeps a hung dependency inside the < 10 s budget.
6. **Offline LLM / mock GitHub (the CI default).** The worker nodes already degrade deterministically (template draft, mock issues). The graph adds no key requirement, so the Mermaid test runs fully offline and the end-to-end test runs with only the seeded Postgres live.
7. **`MemorySaver` growth over a long-lived process.** Checkpoints accumulate in memory keyed by `thread_id`. Acceptable for Phase 4's single-worker dev orchestrator; flagged for Phase 4.6 to bound/evict (or swap a durable saver) if ticket throughput grows. Out of scope here.

### 5.2 Error Handling & State Recovery Matrix

| Trigger / Exception | Handled State / Action | Fallback Behavior / Mitigation |
|---|---|---|
| Unknown/`None` `intent` at conditional edge | `route_by_intent` → `"billing_agent"` (a registered target) | No dispatch `KeyError`; never an unroutable dead-end (edge case 2) |
| Same `ticket_id` reused | Run resumes the existing `MemorySaver` thread | Use a unique `ticket_id` per ticket; retries choose resume-vs-fresh deliberately (edge case 1) |
| `stream_mode="updates"` last chunk is a delta | `run_ticket` reads `graph.get_state(cfg).values` | Returns the fully-merged `AgentState`, not a truncated delta (edge case 4) |
| Node raises (classifier 5xx, MCP/DB error) | Exception propagates out of `astream` | `run_ticket` raises; Phase 4.6 maps to HTTP 5xx; no silent half-state (edge case 5) |
| No LLM key / GitHub mock mode | Workers use template draft + mock issues | Topology + e2e tests stay green offline (edge case 6) |
| Accidental `execution_logs` reducer added | Breadcrumbs double-appended | FR-9 forbids a reducer in 4.4; e2e log-order assertion catches regression (edge case 3) |
| Long-lived process, many runs | `MemorySaver` memory grows | Bounded/evicted or swapped for a durable saver in Phase 4.6 (edge case 7) |

---

## 6. Acceptance Criteria

### 6.1 Technical Acceptance Criteria

- **AC-T1 (Mermaid topology — task 4.4.1 verify):** `.venv\Scripts\python -c "from estc.services.orchestrator.graph.build import graph; print(graph.get_graph().draw_mermaid())"` exits 0 and prints a Mermaid diagram whose node set contains all six: `classify`, `billing_agent`, `bug_agent`, `feature_agent`, `lockout_agent`, `supervisor_review`.
- **AC-T2 (Edge structure):** A test asserts the rendered graph contains the entry edge into `classify`, the four conditional edges `classify → {worker}`, the four fan-in edges `{worker} → supervisor_review`, and the terminal edge `supervisor_review → END` — no edge bypasses the supervisor.
- **AC-T3 (Checkpointer present — task 4.4.1):** `graph` is compiled with a `MemorySaver`; a test confirms a run invoked with a `thread_id` is retrievable via `graph.get_state({"configurable": {"thread_id": ...}})` (run-resume capability).
- **AC-T4 (`run_ticket` end-to-end < 10 s — task 4.4.2 verify):** Against the seeded DB (company `9422`) with the canonical bug ticket, `await run_ticket("e2e-<uniq>", "I am getting a 500 error when pulling the API, my company ID is 9422", "9422")` returns within 10 s a populated `AgentState`: `intent` set, `agent_draft_response` non-empty, `confidence_score > 0`, and `execution_logs[-1] ∈ {"AUTO_APPROVED","ESCALATE"}`.
- **AC-T5 (Streaming surface — task 4.4.2):** Iterating `astream_ticket(...)` yields **≥ 3** `(node_name, update)` events for a single ticket — at minimum `classify`, one worker, and `supervisor_review` — in that node order.
- **AC-T6 (Log accumulation across the wired graph):** The final `execution_logs` begins with `classified:<intent>`, contains the worker breadcrumb (e.g. `bug_drafted`), and ends with the supervisor verdict — proving accumulation (not overwrite) holds end-to-end with no reducer (FR-9 / edge case 3).
- **AC-T7 (Async + offline determinism):** `run_ticket` is `async def` and drives the graph via `graph.astream`; the Mermaid/topology tests pass fully offline (no LLM keys, GitHub mock per `conftest.py`).
- **AC-T8 (Suite green):** `.venv\Scripts\pytest estc/tests/test_graph_build.py -v` is all green; the broader `estc/tests/` suite (including `test_graph_nodes.py`) is unaffected — Phase 4.4 changes no node behavior.

### 6.2 Business & Functional Alignment

- **AC-B1 (Topology fidelity, `design.md` § Component D):** The compiled graph realizes exactly `classify → router → {billing|bug|feature|lockout} → supervisor_review`, with `route_by_intent` as the conditional edge and a single terminal compliance gate — no node body changed from Phase 4.3 (FR-9).
- **AC-B2 (Single chokepoint, plan 4.3.7/4.4):** Every path funnels through `supervisor_review` before `END`; there is no edge from a worker straight to `END`, so the auto-approve-vs-escalate verdict is always recorded.
- **AC-B3 (Read-only security, `design.md` § 2):** Wiring introduces no new I/O; the graph can only reach the read-only MCP tool schemas the 4.3 nodes use. `execution_logs` and streamed events stay PII-free (no `raw_issue_text` in the audit trail).
- **AC-B4 (Run-resume & observability readiness):** The `MemorySaver` + `thread_id` design gives Phase 4.5 a stable per-run handle for LangSmith child-run correlation, and `astream_ticket` gives Phase 4.6 a ready per-node event feed for the SSE endpoint and the Phase 5 real-time agent map.
- **AC-B5 (Offline-first parity, codebase convention):** Like every prior phase, the graph layer degrades deterministically with no external keys — preserving the "tests pass on a clean checkout" property; only the explicit end-to-end test requires the live seeded Postgres, as the 4.4.2 verify mandates.

---

**Open items for the execution plan (Phase 4.4 plan):**
1. Confirm the installed `langgraph` exposes `MemorySaver` at `langgraph.checkpoint.memory` and `START`/`END`/`StateGraph` at `langgraph.graph` (both import paths verified in `.venv` during spec authoring); pin the version in `requirements-orchestrator.txt` if not already.
2. The 4.4.2 end-to-end test needs the seeded Postgres reachable (`POSTGRES_HOST=localhost` per `conftest.py`) and company `9422` seeded; confirm the seed fixture contains `9422`, or have the plan add it, so AC-T4 is reproducible.
3. Decide whether `graph.get_state` is called sync (as drafted) or via an async variant if a future async checkpointer is adopted; for `MemorySaver` the sync call is correct and used here.
4. Phase 4.6 hand-off: the SSE endpoint should reuse `astream_ticket` directly (single streaming code path) rather than re-implementing `graph.astream`; note this in the 4.6 plan to avoid divergence.
5. Confirm `draw_mermaid()` is available on the installed `langgraph` build (it is the 4.4.1 verify command); if a headless/rendering dependency is missing, the plan falls back to asserting on `graph.get_graph().nodes` for AC-T1/AC-T2.
```

