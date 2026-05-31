# Execution Plan: Phase 4.4 — Graph Wiring
**Source spec:** `.claude/specs/06-graph-wiring-spec.md`
**Source plan section:** `docs/plan.md` § Phase 4.4 (tasks 4.4.1 – 4.4.2)
**Status:** COMPLETE — all tasks executed and verified; exit gates EG-1/EG-2/EG-3 green (6 graph tests pass + 1 live test skip-guarded; 31 passed / 1 skipped across agent_state + rag + nodes + build). *Deviation: venv is Python 3.12.10, not 3.11 (PF-2) — the project's actual toolchain; non-blocking.*

---

## Context

This plan operationalizes Phase 4.4 of the ESTC roadmap: **compiling the seven Phase 4.3 callables into one runnable LangGraph state machine** (`StateGraph(AgentState)` with a conditional fan-out and a single supervisor fan-in, checkpointed by `MemorySaver`) and exposing the `run_ticket` async streaming entrypoint that turns a raw ticket into a populated `AgentState`. Its consumers are Phase 4.5 (LangSmith tracing wraps this graph), Phase 4.6 (the SSE endpoint iterates this stream), and Phase 5 (the Streamlit real-time map). The work has three threads that must be done in order:

1. **Graph assembly** (Python — task 4.4.1): author `estc/services/orchestrator/graph/build.py` with a `build_graph(checkpointer=None)` factory that registers the six nodes, wires `START→classify`, the `route_by_intent` conditional edge `classify→{worker}`, the four `worker→supervisor_review` fan-in edges, and `supervisor_review→END`, compiled with `MemorySaver`; plus a module-level compiled `graph` (the verify target).
2. **Streaming entrypoint** (Python — task 4.4.2): `astream_ticket(...)` (async generator yielding `(node_name, update)` per transition) and `run_ticket(ticket_id, text, company_id) -> AgentState` that drives the graph over that stream and returns the merged terminal state read back from the checkpointer.
3. **Test harness** (Python — plan-internal task 4.4.3): `estc/tests/test_graph_build.py` proving the topology offline (Mermaid shows 6 nodes; supervisor is the single terminal gate), an **offline** `run_ticket` integration test (deterministic, no live infra), and a **skip-guarded live** `run_ticket` test that is the literal 4.4.2 "against the seeded DB" assertion.

**Design notes — what this plan deliberately decides (and mirrors):**
- **Phase 4.4 adds only edges + the entrypoint** — no node body or signature changes (spec FR-9). It does **not** introduce an `Annotated[list, add]` reducer on `AgentState.execution_logs`: the per-run path is strictly linear (`classify → one worker → supervisor_review`), so the 4.3 "return full extended list" convention accumulates correctly; a reducer would *double-append*. The topology test locks the final log order in.
- **Offline-first parity (codebase convention).** The Mermaid/topology tests and one `run_ticket` integration test run with **no live infra** by leaning on Python's dynamic global lookup: the compiled graph stores the node *function objects*, but those functions resolve `httpx`, `get_subscription_status`, `aretrieve`, etc. from their own module globals **at call time** — so monkeypatching `nodes.classify.httpx.AsyncClient` (deterministic intent), stubbing `nodes.bug_agent.aretrieve`, and the conftest-forced GitHub mock make the *real module-level `graph`* run end-to-end with zero network/DB. This needs no graph rebuild.
- **Live e2e is skip-guarded.** The literal 4.4.2 verify ("against the seeded DB … < 10s") needs classifier-api + seeded Postgres + persisted Chroma reachable. That test runs only when a reachability probe passes (or `ESTC_E2E_LIVE=1`); otherwise it `pytest.skip`s, preserving the "tests pass on a clean checkout" property. This mirrors Phase 4.3's decision to defer live-DB assertions to this phase's e2e.
- **Import cost is real and excluded from the latency budget.** Importing `build.py` transitively pulls `chromadb`/`torch`/transformers via the RAG retriever (~50s cold import, measured). The 4.4.2 `< 10s` budget measures **`run_ticket` execution after a warm import**, not module import; the test asserts on a timer wrapped around the `await run_ticket(...)` call only.
- **Plan-internal task IDs:** `docs/plan.md` defines roadmap tasks **4.4.1–4.4.2** only. Task **4.4.3** (test harness) below is a plan-internal expansion to satisfy spec acceptance bars (AC-T1…AC-T8); it is not a new roadmap ID.

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**. A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** Confirm the source spec exists and is the version this plan targets.
  **Verify:** `Get-Content .claude/specs/06-graph-wiring-spec.md | Select-String "run_ticket"` returns ≥ 1 match.
- [ ] **PF-2** Confirm the venv is the active Python 3.11 toolchain.
  **Verify:** `.venv\Scripts\python --version` reports `Python 3.11.*`.
- [ ] **PF-3** Confirm the seven Phase 4.3 callables import with the agreed shapes (the inputs this graph wires; Phase 4.4 must not modify them).
  **Verify:** `.venv\Scripts\python -c "import inspect; from estc.services.orchestrator.graph.nodes.classify import classify; from estc.services.orchestrator.graph.nodes.router import route_by_intent; from estc.services.orchestrator.graph.nodes.billing_agent import billing_agent; from estc.services.orchestrator.graph.nodes.bug_agent import bug_agent; from estc.services.orchestrator.graph.nodes.feature_agent import feature_agent; from estc.services.orchestrator.graph.nodes.lockout_agent import lockout_agent; from estc.services.orchestrator.graph.nodes.supervisor import supervisor_review; assert all(inspect.iscoroutinefunction(f) for f in [classify,billing_agent,bug_agent,feature_agent,lockout_agent]); assert not inspect.iscoroutinefunction(route_by_intent) and not inspect.iscoroutinefunction(supervisor_review); print('ok')"` prints `ok`.
- [ ] **PF-4** Confirm the LangGraph import paths this plan uses are present.
  **Verify:** `.venv\Scripts\python -c "from langgraph.graph import StateGraph, START, END; from langgraph.checkpoint.memory import MemorySaver; print('ok')"` prints `ok`.
- [ ] **PF-5** Confirm `draw_mermaid()` produces node-bearing **text** offline (the 4.4.1 verify mechanism; spec open item 5).
  **Verify:** `.venv\Scripts\python -c "from langgraph.graph import StateGraph, START, END; from pydantic import BaseModel
class S(BaseModel):
    x:int=0
def a(s): return {'x':1}
b=StateGraph(S); b.add_node('a',a); b.add_edge(START,'a'); b.add_edge('a',END)
print('ok' if 'a(' in b.compile().get_graph().draw_mermaid() else 'fail')"` prints `ok`. **Fallback if `draw_mermaid` is unavailable:** assert on `graph.get_graph().nodes` instead (AC-T1).
- [ ] **PF-6** Confirm the `AgentState` channel has **no** `execution_logs` reducer (so the linear-path append convention is correct and a reducer must NOT be added — FR-9).
  **Verify:** `Get-Content estc/shared/schemas/agent_state.py | Select-String "Annotated|add_messages|operator.add"` returns **0** matches.
- [ ] **PF-7** Confirm the GitHub mock fixture is in force for offline tests (the bug-path e2e relies on it; conftest forces mock mode).
  **Verify:** `Get-Content estc/tests/conftest.py | Select-String "GITHUB_MOCK_PATH"` returns ≥ 1 match.
- [ ] **PF-8** Record the live-e2e prerequisites (informational; the live test skips when these are absent). Probe whether the classifier API and seeded Postgres are reachable.
  **Verify:** `.venv\Scripts\python -c "import socket,os; from estc.shared.config import Settings; print('classifier_url', Settings().CLASSIFIER_API_URL)"` prints the URL — used by the live test's reachability probe (no failure expected here regardless of reachability).

---

## Task 4.4.1 — Graph Assembly (`build.py`)

### 4.4.1-a `build_graph` factory + six nodes + edges
- [ ] Create `estc/services/orchestrator/graph/build.py` (no `__init__.py`; PEP 420 — FR-10). Import the seven 4.3 callables and `AgentState`. Define `_WORKERS = ("billing_agent","bug_agent","feature_agent","lockout_agent")` and `def build_graph(checkpointer: Optional[BaseCheckpointSaver] = None) -> Any`: instantiate `StateGraph(AgentState)`, `add_node` the **six** nodes under names `"classify"`, the four workers, and `"supervisor_review"` (spec FR-1); add `START→"classify"`, the four `worker→"supervisor_review"` edges, and `"supervisor_review"→END` (FR-2). `route_by_intent` is **not** a node.
  **Verify:** *(after 4.4.1-c)* covered by the topology test (AC-T1/AC-T2); structural import checked in 4.4.1-c.

### 4.4.1-b Conditional fan-out + `MemorySaver` checkpointer
- [ ] In `build_graph`, add `builder.add_conditional_edges("classify", route_by_intent, {n: n for n in _WORKERS})` — identity mapping because `route_by_intent` already returns the literal worker-node names (FR-3; spec § 5.1 edge case 2). Compile with `builder.compile(checkpointer=checkpointer or MemorySaver())` (FR-4).
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.graph.build import build_graph; g=build_graph(); assert g.checkpointer is not None; print('ok')"` prints `ok`.

### 4.4.1-c Module-level compiled `graph` + Mermaid shows 6 nodes
- [ ] Add module-level `graph = build_graph()` (built once at import; FR-5). This is the literal 4.4.1 verify target.
  **Verify (the roadmap 4.4.1 command):** `.venv\Scripts\python -c "from estc.services.orchestrator.graph.build import graph; m=graph.get_graph().draw_mermaid(); print(m); assert all(n in m for n in ['classify','billing_agent','bug_agent','feature_agent','lockout_agent','supervisor_review']); print('ALL_6_NODES_OK')"` prints the Mermaid diagram and `ALL_6_NODES_OK`. *(Note: first run pays ~50s cold import of torch/chroma via the RAG retriever — expected, not a failure.)* Matches **AC-T1**.

---

## Task 4.4.2 — Streaming Entrypoint (`astream_ticket` + `run_ticket`)

### 4.4.2-a `astream_ticket` async generator
- [ ] In `build.py`, add `async def astream_ticket(ticket_id, text, company_id, *, config=None) -> AsyncIterator[tuple[str, dict[str, Any]]]`: build the initial `AgentState`, default `config` to `{"configurable": {"thread_id": ticket_id}}`, and `async for chunk in graph.astream(initial, config=cfg, stream_mode="updates"): for node_name, update in chunk.items(): yield node_name, (update or {})` (FR-7). Uses the **async** stream (project async rule).
  **Verify:** `.venv\Scripts\python -c "import inspect; from estc.services.orchestrator.graph.build import astream_ticket; assert inspect.isasyncgenfunction(astream_ticket); print('ok')"` prints `ok`.

### 4.4.2-b `run_ticket` entrypoint returning merged terminal state
- [ ] Add `async def run_ticket(ticket_id, text, company_id) -> AgentState`: drive the graph by exhausting `astream_ticket(..., config=cfg)` (same `thread_id` cfg), then read the fully-merged state via `graph.get_state(cfg)` and return `AgentState(**snapshot.values)` (FR-6, FR-8; spec § 5.1 edge case 4 — do NOT return the last streamed delta).
  **Verify:** `.venv\Scripts\python -c "import inspect; from estc.services.orchestrator.graph.build import run_ticket; assert inspect.iscoroutinefunction(run_ticket); print('ok')"` prints `ok`. Full behavior covered by AC-T4/AC-T5/AC-T6 in Task 4.4.3.

---

## Task 4.4.3 — Graph Test Harness *(plan-internal; satisfies AC-T1…AC-T8)*

### 4.4.3-a Test file & offline guards
- [ ] Create `estc/tests/test_graph_build.py`. Rely on the existing `conftest.py` (GitHub forced to mock; Windows selector loop). For the offline `run_ticket` test, monkeypatch so the real module-level `graph` runs with no infra (dynamic-global-lookup trick from the Context design note):
  - `monkeypatch.setattr(classify_mod.httpx, "AsyncClient", <factory returning an httpx.AsyncClient(transport=httpx.MockTransport(handler))>)` where `handler` returns `{"intent":"bug","confidence":0.85,"latency_ms":3.0}` — gives a deterministic `bug` route with no network.
  - `monkeypatch.setattr(bug_mod, "aretrieve", <async () -> []>)` — skip the bge/Chroma load.
  - Ensure no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` (template draft path), and `llm._chat_model.cache_clear()`.
  **Verify:** `.venv\Scripts\pytest --collect-only estc/tests/test_graph_build.py` collects ≥ 5 items with no import errors.

### 4.4.3-b Test cases — must include at minimum (AC-T1 … AC-T8 bar)
- [ ] `test_mermaid_shows_six_nodes` → `graph.get_graph().draw_mermaid()` contains all six node names (**AC-T1**).
- [ ] `test_edges_route_through_supervisor` → the rendered graph / `get_graph().edges` shows entry into `classify`, four `classify→{worker}` conditional edges, four `{worker}→supervisor_review` edges, and `supervisor_review→END`; assert **no** worker edges directly to `__end__` (**AC-T2**, AC-B2).
- [ ] `test_graph_has_memory_checkpointer` → `graph.checkpointer is not None`; after an offline `run_ticket`, `graph.get_state({"configurable":{"thread_id": <id>}}).values` is non-empty (run-resume; **AC-T3**).
- [ ] `test_run_ticket_offline_streams` → with the 4.4.3-a monkeypatches, collect `[(n,_) async for n,_ in astream_ticket("e2e-bug-<uniq>", "I am getting a 500 error when pulling the API, my company ID is 9422", "9422")]`; assert the node order begins `classify`, then `bug_agent`, then `supervisor_review` and length ≥ 3 (**AC-T5**).
- [ ] `test_run_ticket_offline_returns_populated_state` → `await run_ticket(...)` (same monkeypatches) returns an `AgentState` with `intent=="bug"`, non-empty `agent_draft_response`, `confidence_score > 0`, `execution_logs[0].startswith("classified:")`, `"bug_drafted" in execution_logs`, and `execution_logs[-1] in {"AUTO_APPROVED","ESCALATE"}` — proving log **accumulation** end-to-end with no reducer (**AC-T4 offline + AC-T6**, FR-9).
- [ ] `test_run_ticket_under_10s` → wrap only the `await run_ticket(...)` call in a `time.perf_counter()` timer (warm import); assert `elapsed < 10.0` (**AC-T4 latency**; import cost excluded per Context note).
- [ ] `test_run_ticket_live_seeded_db` *(skip-guarded)* → `pytest.skip` unless `os.getenv("ESTC_E2E_LIVE")=="1"` **and** a TCP probe to the classifier API + Postgres succeeds; when enabled, run the canonical ticket against live infra and assert the same populated-state shape (the literal **4.4.2 "against the seeded DB"** assertion).
  **Verify:** `.venv\Scripts\pytest estc/tests/test_graph_build.py -v` reports **all green** with at least 5 passed (the live test shows `skipped` on a clean checkout).

---

## Phase 4.4 Exit Gate

- [ ] **EG-1 (topology fidelity, AC-T1/AC-T2/AC-B1/AC-B2)** — The compiled graph renders all six nodes and funnels every path through `supervisor_review` before `END`.
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.graph.build import graph; m=graph.get_graph().draw_mermaid(); assert all(n in m for n in ['classify','billing_agent','bug_agent','feature_agent','lockout_agent','supervisor_review']); print('ok')"` prints `ok`. **Fallback if `draw_mermaid` is unavailable:** assert membership against `set(graph.get_graph().nodes)` (PF-5 fallback).
- [ ] **EG-2 (graph test sweep, AC-T1…AC-T8)** — Wiring + offline e2e all green.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_graph_build.py -v --tb=short` reports **0 failed**, ≥ 5 passed (live test `skipped`).
- [ ] **EG-3 (no-regression on the broader suite)** — Phase 4.4 (edges + entrypoint only) breaks no prior phase.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_agent_state.py estc/tests/test_rag.py estc/tests/test_graph_nodes.py estc/tests/test_graph_build.py -q` reports **0 failed**. (Live Postgres/GitHub MCP suites run separately.)

---

## Risks & Open Questions

1. **Cold-import latency (~50s) vs. the < 10s budget.** Importing `build.py` loads torch/chromadb via the RAG retriever (measured ~52s). Mitigation: the 4.4.2 budget is asserted around the **`run_ticket` call only** (warm import); `test_run_ticket_under_10s` times just that call. If even warm execution risks the budget on a slow box, the offline path (template draft, stubbed retrieval) keeps it sub-second; the live test is exempt from the unit-suite timer.
2. **`MemorySaver` `thread_id` reuse.** Reusing a `ticket_id` resumes that thread's checkpoint instead of a fresh run (spec § 5.1 edge case 1). Mitigation: tests mint a unique `ticket_id` per run; flagged for Phase 4.6 to decide retry = resume vs. new id.
3. **Pydantic state + `get_state().values` shape.** `AgentState(**snapshot.values)` assumes `.values` is a field-keyed dict. If the installed LangGraph returns the model instance (or a differently-keyed mapping) for Pydantic state, `run_ticket` adapts (`return values if isinstance(values, AgentState) else AgentState(**values)`). Verified shape during execution at 4.4.2-b.
4. **`stream_mode="updates"` chunk shape.** Assumes `{node_name: update_dict}` per chunk. If a node returns `None`/empty, the helper yields `(name, {})` (guarded with `update or {}`). Confirmed against the installed LangGraph at 4.4.2-a.
5. **Live e2e dependency surface.** `test_run_ticket_live_seeded_db` needs classifier-api + seeded Postgres (company `9422`) + persisted `./chroma_db`. Spec open item 2: confirm `9422` is seeded (or have Phase 5 seed it); until then the test stays skip-guarded so the suite is green on a clean checkout.

---

## Out of Scope (explicitly deferred)

- LangSmith child-run tracing wrapping `graph.astream` (≥ 6 child runs per ticket) — Phase 4.5.1.
- Ragas Faithfulness / Answer-Relevance / Context-Recall evaluation — Phase 4.5.2.
- FastAPI `POST /tickets` + `GET /tickets/{id}/stream` SSE serialization built on `astream_ticket` — Phase 4.6.
- A durable (Postgres/SQLite) checkpointer replacing `MemorySaver`, and checkpoint eviction/bounding — Phase 4.6 if needed (spec § 2.1, § 5.1 edge case 7).
- Containerization of `orchestrator-app` (port 8002) — Phase 4.6.2.
- The full multi-service E2E smoke (UI → SSE → draft → approve) — Phase 5.6.

---

**Awaiting `Proceed` to begin execution at PF-1.**
