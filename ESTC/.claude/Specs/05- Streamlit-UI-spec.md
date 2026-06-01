# Architectural Specification: Phase 5 — Streamlit UI (Support Specialist Operations Center)

**Status:** DRAFT / PROPOSED
**Associated Tasks:** Tasks 5.1.1 – 5.6.3 (`docs/plan.md` § Phase 5: Streamlit UI), realizing FR-4 — the Support Specialist Operations Center. Consumes the Phase 4.6 orchestrator SSE feed (Exit Gate 4.7.1 ✓) and closes the project's Definition of Done (full `docker compose` E2E + live Ragas re-run 5.6.3).
**Target Files:**
- `estc/services/ui/app.py` (new — Streamlit entrypoint: 3-column ops center, sidebar inbound list + ingestion form; tasks 5.1.1–5.1.2, 5.2–5.4 wiring)
- `estc/services/ui/orchestrator_client.py` (new — thin HTTP/SSE client over `httpx` + `httpx-sse`: `create_ticket`, `stream_ticket`, `approve`, `modify`, `claim`, `get_state`)
- `estc/services/ui/components/` (new — `agent_map.py` real-time timeline, `draft_panel.py` draft + confidence badge, `escalation_queue.py`; pure render helpers)
- `estc/services/ui/state.py` (new — `st.session_state` schema + helpers: ticket registry view, operator identity, active/closed/escalation partitions)
- `estc/services/ui/Dockerfile` (new — `python:3.11-slim`, `requirements-ui.txt`, `CMD streamlit run ... --server.port 8501`; task 5.5.1)
- `docker-compose.yml` (edit — flesh out the `ui-client` block: build context, `8501:8501`, `ORCHESTRATOR_URL` env, healthcheck, `depends_on: orchestrator-app`; task 5.5.2)
- `estc/services/orchestrator/app/main.py` (**edit** — add the operator-action endpoints deferred from Phase 4.6: `POST /tickets/{id}/approve`, `PATCH /tickets/{id}`, `POST /tickets/{id}/claim`, `GET /tickets/{id}`)
- `estc/services/orchestrator/app/schemas.py` (**edit** — `ModifyDraftRequest`, `ApproveResponse`, `ClaimRequest`, `TicketStateResponse`)
- `estc/tests/test_orchestrator_actions.py` (new — offline `TestClient` tests for the four new endpoints)
- `estc/tests/test_ui_client.py` (new — offline tests for `orchestrator_client` SSE parsing + confidence-band logic, against a mock transport)

**Consumes (unchanged):** `estc/services/orchestrator/app/main.py`'s existing `POST /tickets` + `GET /tickets/{id}/stream` SSE contract (Phase 4.6); `estc/shared/schemas/agent_state.py::AgentState` (the `done`-frame payload shape). **No change to** `graph/nodes/`, `graph/build.py`, `rag/`, or the MCP servers (the Phase 4.x FR-11 no-drift rule still holds). The orchestrator *app* layer is extended only with non-graph operator actions over the existing in-process registry + `MemorySaver`.

---


## 1. Executive Summary & Problem Statement

### 1.1 Objective & Context

Phases 1–4 produced the full triage backend: a DistilBERT classifier (FR-1), two read-only MCP servers (FR-2), a LangGraph state machine + RAG (FR-3), and — at Phase 4.6 — an `orchestrator-app` that exposes that machine over HTTP and **streams one Server-Sent Event per node transition** (`open → classify → worker → supervisor_review → done`), validated end-to-end by Exit Gate 4.7.1. What is still missing is the **human surface**: the Support Specialist who actually reads the AI's draft, watches the agent reason in real time, approves or overrides it, and works the escalation queue. `design.md` § 1 names this surface *"Web UI Client (Streamlit)"* — Service 4 (`ui-client`) — sitting above the orchestration engine over a *REST / Event Stream* boundary.

Phase 5 delivers that surface as a **Streamlit "Support Specialist Operations Center"** (`estc/services/ui/app.py`) and containerizes it as the `ui-client` service on **port 8501**, completing the 5-service `docker-compose` topology. The dashboard is a three-column operations console:

1. **Sidebar — Inbound Tickets** (5.1.1) plus a **mock ingestion form** (5.1.2: text area + `company_id` + Submit) that `POST`s to the orchestrator and surfaces the returned `ticket_id`.
2. **Column 1 — AI Analysis / Real-Time Agent Map** (5.2): subscribes to `GET /tickets/{id}/stream` and renders a **vertical progress timeline** that advances per SSE event (`classify → bug_agent → supervisor_review`), each row showing a status icon (in-progress / done / failed) and a **millisecond duration**.
3. **Column 2 — Draft** (5.3): renders `agent_draft_response` in a code-fence box with a **"Confidence: NN%"** badge colored by band (green ≥ 80, amber 60–79, red < 60), an **Approve Draft** button (`POST /tickets/{id}/approve`) and a **Modify & Override** flow (`PATCH /tickets/{id}` → re-evaluate confidence).
4. **Column 3 — Escalation Queue** (5.4): lists tickets where `requires_escalation == True` under *"Requires Manual Verification,"* each showing the customer's tier + account status and a **Claim** button that assigns the ticket to the current operator (appending to `execution_logs`).

The phase ends with the project's **full E2E exit gate** (5.6): bring up all five services with `docker compose up -d`, drive the canonical bug ticket and the lockout ticket through the live UI, and re-run the Ragas suite against the containerized orchestrator.

A second, explicit deliverable rides along: the **operator-action endpoints deferred from Phase 4.6**. Phase 4.6 shipped only submit + stream; the Approve / Modify / Claim controls (5.3.2, 5.3.3, 5.4.2) require new orchestrator routes — `POST /tickets/{id}/approve`, `PATCH /tickets/{id}`, `POST /tickets/{id}/claim`, and a read-back `GET /tickets/{id}`. These are **app-layer** additions over the existing in-process ticket registry; they introduce no new graph logic and honor the read-only-MCP / no-graph-drift constraints.

### 1.2 Core Problem Statement

The backend is fully reachable over HTTP but has **no human-facing client**: a specialist cannot submit a ticket, watch the per-node triage advance, read the draft with its confidence, approve/override it, or work escalations — and there is no `ui-client` container, so the 5-service topology and the project's full E2E gate cannot be exercised. The challenge is fourfold: **(a)** consume an SSE stream from inside Streamlit's *rerun-the-whole-script* execution model and update a live timeline without flicker or a manual page refresh (< 2 s steps, 5.2.1); **(b)** translate the orchestrator's `AgentState` `done`-frame into operator-meaningful widgets (confidence band coloring, escalation routing) keyed correctly per ticket across reruns; **(c)** add the missing operator-action endpoints to the orchestrator app **without** touching any Phase 4.3/4.4 graph logic or the read-only MCP boundary; and **(d)** containerize Streamlit so all five services come up healthy under one `docker compose up -d`, preserving the project's offline-deterministic test property.

---

## 2. System Boundaries & Constraints

### 2.1 Architectural Boundaries

- **Upstream Trigger / Consumer:** the human Support Specialist operating a browser at `http://localhost:8501`. There is no programmatic upstream — the UI is the top of the stack (`design.md` § 1).
- **Downstream Dependencies (all over HTTP to the orchestrator, never to the graph/MCP directly):**
  - `POST {ORCHESTRATOR_URL}/tickets` — submit a ticket, get a `ticket_id` (Phase 4.6 FR-1).
  - `GET {ORCHESTRATOR_URL}/tickets/{id}/stream` — the SSE feed driving the agent map (Phase 4.6 FR-2). The UI is a **pure SSE consumer**; it never re-implements `graph.astream`.
  - `POST {ORCHESTRATOR_URL}/tickets/{id}/approve`, `PATCH …/{id}`, `POST …/{id}/claim`, `GET …/{id}` — the new operator-action endpoints (this phase).
  - `ORCHESTRATOR_URL` resolves to `http://orchestrator-app:8002` inside the compose network (service-name DNS) and `http://localhost:8002` for host-local `streamlit run`. The UI never talks to `classifier-api`, `postgres-db`, or the MCP servers — every customer fact reaches it transitively through the orchestrator's `AgentState`.
- **Network boundary:** Streamlit binds `0.0.0.0:8501` inside the container, published `8501:8501`, on `networks: [estc-net]`. `depends_on: orchestrator-app` (`service_healthy`).
- **State boundary:** two non-durable layers, both process-lifetime:
  - **Orchestrator-side** — the existing in-process `_TICKETS` registry + `MemorySaver` (keyed `thread_id == ticket_id`); the new actions mutate `TicketRecord` status / a stored override draft, not the graph checkpoint.
  - **UI-side** — `st.session_state` holds the operator's *view*: the inbound ticket list, per-ticket latest `AgentState`, the active/closed/escalation partitions, the selected ticket, and the operator name. It is **per-session** (per browser tab); a refresh re-hydrates from the orchestrator via `GET /tickets/{id}` for known ids. No database — consistent with the dev-scope, single-worker design called out in Phase 4.6 § 2.

### 2.2 Technical & Operational Constraints

- **Streamlit execution model (the central constraint):** Streamlit re-executes `app.py` top-to-bottom on every widget interaction. Live SSE rendering therefore uses **`st.fragment`** (stable in 1.37+, present in the pinned `streamlit==1.38.*`) for the agent-map region so the streaming loop reruns **in isolation** without re-POSTing the ticket or rebuilding the whole page; placeholders (`st.empty()` / `st.status`) are updated **in-loop** as events arrive. All per-ticket data lives in `st.session_state` keyed by `ticket_id` so it survives reruns.
- **SSE consumption discipline:** use **`httpx-sse`** (`httpx_sse.connect_sse`) with a **synchronous** `httpx.Client` — Streamlit's script thread is synchronous, so a sync iterator (`for sse in event_source.iter_sse()`) is the correct fit (no event-loop nesting inside the Streamlit runtime). Each `sse.event` ∈ {`open`, `node`, `done`, `error`}; `sse.data` is the JSON defined by the Phase 4.6 SSE contract. A read timeout (default 30 s, configurable) bounds a hung stream. *(The project's async rule — memory `feedback_mcp_async` — governs MCP servers specifically; it does not apply to the synchronous Streamlit UI, which owns no MCP tools.)*
- **Per-node duration (5.2.2):** the SSE frames do **not** carry server-side timing (adding it would change the graph — disallowed). The UI therefore measures **wall-clock between consecutively received events** client-side (`time.perf_counter()` deltas, attributed to the node that just completed). This is honest end-to-end latency as the operator perceives it and needs no backend change.
- **Performance / Latency:** the timeline must advance in **< 2 s steps without a page refresh** (5.2.1) — met by the in-fragment in-loop placeholder updates (each `sse` event triggers an immediate widget write). The canonical run completes inside the Phase 4.4 **< 10 s** budget.
- **Confidence band (5.3.1):** `pct = round(state.confidence_score * 100)`; **green ≥ 80**, **amber 60–79**, **red < 60**. Rendered via a colored badge (`streamlit-extras` `badges`/custom HTML). The same `confidence_score` feeds the escalation logic (mirroring `supervisor_review`'s 0.70 threshold; the UI does not re-derive escalation — it trusts `state.requires_escalation`).
- **Security & Compliance:** the UI adds **no** new I/O path to any backend the orchestrator can't already reach (read-only MCP boundary preserved — `design.md` § 2). It renders only `AgentState` fields already exposed by the Phase 4.6 `done` frame plus the new action endpoints. **PII rule (carried from Phase 4.4/4.6):** `raw_issue_text` is shown only in the operator's own ticket view, never written into `execution_logs`. Operator identity (the Claim name) is a free-text dev field, not authenticated — flagged as dev-scope.
- **Resource Limits:** `st.session_state` grows with tickets handled in a session — acceptable at dev volume; the inbound list is the working set, not an archive. Single Streamlit server process.
- **Packaging / imports:** the UI is a leaf service — its modules import each other by **package-relative** paths under `estc.services.ui.*` (consistent with repo convention) and import **nothing** from `estc.services.orchestrator` or `estc.shared` at runtime (the contract is the HTTP/JSON wire shape, not Python types) — this keeps the `ui-client` image lean (no torch/chromadb/langgraph). The shared `AgentState` shape is mirrored as a thin local TypedDict/parse helper, not imported.
- **Container constraint:** unlike `orchestrator-app`, the UI builds from its **own sub-directory** (`context: ./estc/services/ui`) and installs only `requirements-ui.txt` (`streamlit`, `streamlit-extras`, `httpx-sse`) — a small image. Healthcheck probes Streamlit's built-in `GET /_stcore/health` with a `curl`-free urllib one-liner (same pattern as `classifier-api`/`orchestrator-app`, since `python:3.11-slim` has no `curl`).

---

## 3. Functional Requirements

- **FR-1 (Three-column skeleton — task 5.1.1):** `app.py` renders a sidebar titled **"Inbound Tickets"** and a main area of three columns headed **"AI Analysis"**, **"Draft"**, **"Escalation Queue"**. All three headers render on first load (`st.set_page_config(layout="wide")`).
- **FR-2 (Ingestion form — task 5.1.2):** the sidebar holds a form (`st.form`): a `text_area` for the issue, a `text_input` for `company_id`, and a **Submit** button. On submit it calls `POST /tickets`, receives `{ticket_id, status}`, appends the ticket to the session inbound list, and surfaces the `ticket_id` in an `st.toast`.
- **FR-3 (Real-time agent map — task 5.2.1):** selecting a ticket starts an SSE subscription to `GET /tickets/{id}/stream`; a **vertical timeline** in column 1 adds/updates a row per `node` event in order (`classify → worker → supervisor_review`). Updates appear in **< 2 s steps with no manual refresh** (driven inside an `st.fragment` streaming loop).
- **FR-4 (Node row detail — task 5.2.2):** each timeline row shows a **status icon** — in-progress (spinner) → done (✓) → failed (✗ on an `error` frame) — and the node's **duration in milliseconds** (client-measured wall-clock delta), shown once the node completes.
- **FR-5 (Draft panel — task 5.3.1):** column 2 renders the terminal `agent_draft_response` (from the `done` frame) in a code-fence-style box, with a **"Confidence: NN%"** badge whose color follows the band rule (green ≥ 80 / amber 60–79 / red < 60).
- **FR-6 (Approve — task 5.3.2):** an **Approve Draft** button calls `POST /tickets/{id}/approve`; on success the ticket moves from the **Active** sidebar list to **Closed** with a green check. (Backend: a stub that flips the registry record to `closed`/`approved`.)
- **FR-7 (Modify & Override — task 5.3.3):** a **Modify & Override** control opens an editable `text_area` pre-filled with the current draft; **Save** calls `PATCH /tickets/{id}` with the new text and the UI **re-renders the confidence** from the endpoint's response (see FR-13 for the re-evaluation contract).
- **FR-8 (Escalation queue — task 5.4.1):** column 3 lists every session ticket whose latest state has `requires_escalation == True` under **"Requires Manual Verification."** A ticket in this queue is **not** presented through the auto-approval (Approve) flow.
- **FR-9 (Escalation row + Claim — task 5.4.2):** each escalation row shows the customer's **subscription tier** and **account status** plus a **Claim** button; Claim calls `POST /tickets/{id}/claim {operator}`, which appends an operator marker to `execution_logs`, and the UI then removes the row from the queue.
- **FR-10 (UI containerization — task 5.5.1):** `estc/services/ui/Dockerfile` on `python:3.11-slim` installs `requirements-ui.txt`, copies the `ui/` tree, `EXPOSE 8501`, `CMD ["streamlit","run","app.py","--server.address","0.0.0.0","--server.port","8501"]`. `docker build -t estc-ui ./estc/services/ui` exits 0.
- **FR-11 (Compose wiring — task 5.5.2):** the `ui-client` block sets `build.context: ./estc/services/ui`, `ports: 8501:8501`, `environment: ORCHESTRATOR_URL=http://orchestrator-app:8002`, a `/_stcore/health` healthcheck, `depends_on: { orchestrator-app: service_healthy }`, on `estc-net`. `docker compose up -d` brings **all 5 services healthy**.
- **FR-12 (Approve / Claim / read-back endpoints — orchestrator app, deferred from 4.6):** add to `app/main.py`, operating on the existing registry only:
  - `POST /tickets/{id}/approve` → set `status="closed"`, `approved=True`; return `{ticket_id, status:"closed"}`. `404` if unknown.
  - `POST /tickets/{id}/claim {operator}` → append `f"CLAIMED_BY:{operator}"` to the run's `execution_logs` (via `graph.update_state` on the ticket's thread, an app-level state edit — **not** a graph/node change); return the updated logs. `404` if unknown.
  - `GET /tickets/{id}` → return the current merged `AgentState` (+ registry `status`) for UI re-hydration after a refresh. `404` if unknown.
- **FR-13 (Modify + re-evaluate — `PATCH /tickets/{id}`):** accept `{draft_text}`; persist it as the ticket's override draft and **re-evaluate confidence on the new text** by calling the existing `classifier-api` `/classify` on the edited draft and writing the returned probability into `confidence_score` (a transparent, dependency-only re-scoring — no graph re-run, no new model). Return the updated `{agent_draft_response, confidence_score, requires_escalation}`. *(Rationale + alternatives in the Open Items.)*
- **FR-14 (No graph/logic drift — carries Phase 4.x FR-11):** Phase 5 changes **no** file under `graph/nodes/`, `graph/build.py`, `rag/`, or the MCP servers. Orchestrator edits are confined to `app/main.py` + `app/schemas.py` (HTTP layer over the existing registry/checkpointer).
- **FR-15 (Offline-deterministic tests — carries the codebase invariant):** the new endpoint tests run via FastAPI `TestClient` with no live infra (reuse the Phase 4.6 monkeypatch fixtures); the UI-client tests parse a canned SSE byte stream via a mock transport. Both stay green on a clean checkout; the live browser behavior is validated only by the 5.6 exit-gate verifies.
- **FR-16 (Full E2E exit gate — tasks 5.6.1–5.6.3):** with all five services up, the canonical bug ticket shows `classify → bug_agent → supervisor_review`, a draft citing ≥ 1 GitHub issue, confidence ≥ 80 %, and Approve closes it; the lockout ticket lands in **Requires Manual Verification** with auto-approval blocked (`requires_escalation == True`); and the Ragas suite (4.5.2) re-runs against the live orchestrator with all three metrics ≥ 0.80 mean.

---

## 4. Detailed Component Specifications & API Contracts

### 4.1 Interface Code & Data Shapes

**New orchestrator request/response models — `estc/services/orchestrator/app/schemas.py` (additions):**
```python
from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel

class ModifyDraftRequest(BaseModel):
    draft_text: str                       # operator-edited draft (5.3.3)

class ClaimRequest(BaseModel):
    operator: str                         # free-text operator id (dev-scope, 5.4.2)

class ApproveResponse(BaseModel):
    ticket_id: str
    status: str                           # "closed"

class TicketStateResponse(BaseModel):
    ticket_id: str
    status: str                           # pending | running | done | closed | error
    state: dict[str, Any]                 # AgentState.model_dump() (intent, draft, confidence, ...)
```

**New orchestrator routes — `estc/services/orchestrator/app/main.py` (shape, not final code):**
```python
from estc.services.orchestrator.graph.build import graph
from estc.shared.schemas.agent_state import AgentState

def _state(ticket_id: str) -> AgentState:                       # reuse the 4.6 normalizer
    values = graph.get_state({"configurable": {"thread_id": ticket_id}}).values
    return values if isinstance(values, AgentState) else AgentState(**values)

@app.post("/tickets/{ticket_id}/approve", response_model=ApproveResponse)
async def approve(ticket_id: str) -> ApproveResponse:
    rec = _require(ticket_id)                                   # 404 if unknown
    rec.status = "closed"; rec.approved = True
    return ApproveResponse(ticket_id=ticket_id, status="closed")

@app.patch("/tickets/{ticket_id}", response_model=TicketStateResponse)
async def modify(ticket_id: str, req: ModifyDraftRequest) -> TicketStateResponse:
    rec = _require(ticket_id)
    # re-score the edited text on the existing classifier (no graph re-run)
    conf = await _classify_confidence(req.draft_text)          # httpx -> CLASSIFIER_API_URL/classify
    graph.update_state({"configurable": {"thread_id": ticket_id}},
                       {"agent_draft_response": req.draft_text, "confidence_score": conf})
    st = _state(ticket_id)
    return TicketStateResponse(ticket_id=ticket_id, status=rec.status, state=st.model_dump())

@app.post("/tickets/{ticket_id}/claim", response_model=TicketStateResponse)
async def claim(ticket_id: str, req: ClaimRequest) -> TicketStateResponse:
    rec = _require(ticket_id)
    logs = list(_state(ticket_id).execution_logs) + [f"CLAIMED_BY:{req.operator}"]
    graph.update_state({"configurable": {"thread_id": ticket_id}}, {"execution_logs": logs})
    return TicketStateResponse(ticket_id=ticket_id, status=rec.status, state=_state(ticket_id).model_dump())

@app.get("/tickets/{ticket_id}", response_model=TicketStateResponse)
async def get_ticket(ticket_id: str) -> TicketStateResponse:
    rec = _require(ticket_id)
    return TicketStateResponse(ticket_id=ticket_id, status=rec.status, state=_state(ticket_id).model_dump())
```

**UI orchestrator client — `estc/services/ui/orchestrator_client.py` (shape):**
```python
import os, json, httpx
from httpx_sse import connect_sse
from typing import Iterator, Any

BASE = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8002")

def create_ticket(text: str, company_id: str | None) -> dict[str, Any]:
    r = httpx.post(f"{BASE}/tickets", json={"text": text, "company_id": company_id}, timeout=10)
    r.raise_for_status(); return r.json()                       # {ticket_id, status}

def stream_ticket(ticket_id: str, read_timeout: float = 30.0) -> Iterator[dict[str, Any]]:
    """Yield parsed SSE frames {event, ...} in order: open -> node* -> done|error."""
    with httpx.Client(timeout=httpx.Timeout(5.0, read=read_timeout)) as c:
        with connect_sse(c, "GET", f"{BASE}/tickets/{ticket_id}/stream") as es:
            for sse in es.iter_sse():
                yield {"event": sse.event, **json.loads(sse.data)}

def approve(ticket_id: str) -> dict[str, Any]: ...
def modify(ticket_id: str, draft_text: str) -> dict[str, Any]: ...
def claim(ticket_id: str, operator: str) -> dict[str, Any]: ...
def get_state(ticket_id: str) -> dict[str, Any]: ...
```

**UI confidence-band helper + session schema (shape):**
```python
def confidence_band(score: float) -> tuple[int, str]:          # 5.3.1
    pct = round(score * 100)
    return pct, ("green" if pct >= 80 else "orange" if pct >= 60 else "red")

# st.session_state layout (UI-side view, keyed by ticket_id)
# {
#   "operator": str,
#   "tickets": { ticket_id: {"company_id","text","status","state": <AgentState dict>|None,
#                            "timeline": [{"node","status","ms"}], } },
#   "selected": ticket_id | None,
# }
```

**Customer tier / account status for the escalation row (5.4.2):** the `AgentState` shape has no dedicated `tier`/`account_status` fields (the design's 8-field model). The escalation row therefore renders these from the facts the worker node already surfaced: the `billing_agent`/`lockout_agent` draft and `retrieved_context` carry the Postgres record (the 4.3.3 verify requires the billing draft to *mention the customer's tier*). The UI extracts them with a small, defensive parser over `state.agent_draft_response` + `retrieved_context`, falling back to "—" when absent. *(A cleaner long-term path — a dedicated `customer_facts` field or a `/customers/{id}` read — is noted in Open Items; it is out of scope here because it would touch the graph/MCP layer FR-14 forbids.)*

### 4.2 Endpoint / Method Contracts

**Orchestrator (consumed by the UI):**

| Route | Method | Request | Success | Errors |
|---|---|---|---|---|
| `/tickets` | `POST` | `{text, company_id?}` | `201 {ticket_id, status:"pending"}` | `422` |
| `/tickets/{id}/stream` | `GET` | path `id` | `200 text/event-stream`: `open → node* → done` | `404`; node failure → `event: error` |
| `/tickets/{id}/approve` | `POST` | — | `200 {ticket_id, status:"closed"}` | `404` |
| `/tickets/{id}` | `PATCH` | `{draft_text}` | `200 {ticket_id,status,state}` (re-scored confidence) | `404`, `422`, `502` (classifier down) |
| `/tickets/{id}/claim` | `POST` | `{operator}` | `200 {ticket_id,status,state}` (logs ← `CLAIMED_BY:…`) | `404`, `422` |
| `/tickets/{id}` | `GET` | path `id` | `200 {ticket_id,status,state}` | `404` |

**UI → human (Streamlit regions):**

| Region | Source | Render |
|---|---|---|
| Sidebar inbound list | `session.tickets` | per-ticket button; Active vs Closed grouping |
| Ingestion form | `st.form` | text area + `company_id` + Submit → `create_ticket` (FR-2) |
| Col 1 Agent Map | `stream_ticket` frames | `st.fragment` vertical timeline; per-row icon + ms (FR-3/FR-4) |
| Col 2 Draft | `done` frame `state` | code-fence box + colored confidence badge; Approve / Modify (FR-5–7) |
| Col 3 Escalation Queue | `state.requires_escalation` | "Requires Manual Verification" rows; tier/status + Claim (FR-8/9) |

- **SSE consumption contract:** the UI treats the Phase 4.6 frame schema as fixed — `open {ticket_id,status}`, `node {event:"node",node,ticket_id,update}`, `done {event:"done",ticket_id,state:<AgentState>}`, `error {event:"error",ticket_id,error}`. The timeline is built from `node` frames; the draft/confidence/escalation come from the `done` frame's `state`.
- **Idempotent re-open:** re-selecting a finished ticket re-opens the stream; per Phase 4.6 ec.1 the server replays a single `done` frame (no re-run). The UI must render correctly from a `done`-only stream (hydrate timeline from `state.execution_logs` when no `node` frames arrive).

---

## 5. Edge Cases & Error Handling

### 5.1 Anticipated Edge Cases

1. **Re-selecting a completed ticket.** The server replays only a `done` frame (Phase 4.6 ec.1) — no `node` frames. The UI hydrates the timeline retrospectively from `state.execution_logs` (`classified:* → *_drafted → AUTO_APPROVED|ESCALATE`) so the agent map still shows the three completed nodes (icons all ✓, durations shown as "—" since they weren't measured live).
2. **Streamlit rerun mid-stream.** A widget click while a stream is open would, in the naive model, restart the script and re-open the stream. Mitigation: the streaming loop lives in an `st.fragment` and writes terminal `state` into `session_state` as soon as the `done` frame arrives; a rerun reads the cached `state` instead of re-streaming. A fresh run requires a fresh Submit (new `ticket_id`).
3. **Orchestrator unreachable / slow.** `create_ticket`/`stream_ticket` wrap `httpx` with timeouts; on `ConnectError`/`ReadTimeout` the UI shows `st.error("Orchestrator unavailable")` and keeps the rest of the dashboard interactive (no crash). The compose `depends_on: service_healthy` makes this rare at boot but not impossible if the orchestrator dies mid-session.
4. **`error` frame mid-stream** (a node raised — classifier 5xx, MCP/DB error). The timeline marks the in-progress node ✗ (failed icon), the draft column shows the error text, and no Approve action is offered. Matches Phase 4.6 ec.4 (in-band SSE error, HTTP already 200).
5. **Escalation ticket vs. Approve flow (5.4.1).** A ticket with `requires_escalation == True` (e.g. lockout) must appear **only** in the escalation queue and must **not** show the Approve button — the UI gates the Approve control on `not state.requires_escalation`, so auto-approval is structurally blocked (5.6.2 hinges on this).
6. **Missing tier/account status (5.4.2).** When the parser can't recover tier/status from the draft/context (e.g. `company_id="unknown"` found no Postgres row), the row shows "—" rather than failing; Claim still works (it edits logs, not customer facts).
7. **Modify re-scoring degenerates (FR-13).** If the edited draft is short/ambiguous, the classifier may return a low confidence and flip the band to red — that is *correct, observable* behavior (the operator sees their edit lowered confidence), not an error. If the classifier is down, `PATCH` returns `502` and the UI keeps the prior draft + a non-blocking warning.
8. **Two operators, one queue (dev-scope).** Sessions are independent (`session_state` per tab); a Claim in one tab edits the shared orchestrator `execution_logs` but the other tab's queue only reflects it after its next `GET /tickets/{id}` refresh. Documented as a single-operator dev limitation (no realtime fan-out), not a bug.
9. **Streamlit healthcheck during warmup.** `GET /_stcore/health` returns `ok` once the server is listening (before any user script run), so the compose probe doesn't flap; a modest `start_period` covers Python import.

### 5.2 Error Handling & State Recovery Matrix

| Trigger / Exception | Handled State / Action | Fallback Behavior / Mitigation |
|---|---|---|
| `POST /tickets` fails (orchestrator down) | `httpx` error caught in form handler | `st.error`; form stays usable; no ticket added (ec.3) |
| SSE `ReadTimeout` mid-stream | Stream iterator stops; last node left in-progress | `st.warning`; timeline keeps completed rows; re-select replays (ec.1/2) |
| SSE `error` frame | Failed node ✗; draft col shows error; no Approve | In-band per Phase 4.6 ec.4 (ec.4) |
| Unknown `ticket_id` on action endpoint | Orchestrator `404` | UI removes stale ticket from session list + `st.warning` |
| `PATCH` while classifier down | Orchestrator `502` | Keep prior draft/confidence; non-blocking warning (ec.7) |
| Approve on an escalation ticket | Prevented in UI (button gated on `requires_escalation`) | Structurally impossible → 5.6.2 holds (ec.5) |
| Tier/status unparseable | Render "—" | Claim still functions (ec.6) |
| Browser refresh mid-session | `session_state` cleared | Re-hydrate known ids via `GET /tickets/{id}` (FR-12) |
| Streamlit rerun mid-stream | Cached terminal `state` read from `session_state` | No double-stream; fragment isolation (ec.2) |

---

## 6. Acceptance Criteria

### 6.1 Technical Acceptance Criteria

- **AC-T1 (Skeleton — 5.1.1):** `streamlit run estc/services/ui/app.py` renders the **"AI Analysis"**, **"Draft"**, and **"Escalation Queue"** column headers and the **"Inbound Tickets"** sidebar on first load.
- **AC-T2 (Ingestion — 5.1.2):** submitting the form `POST`s `/tickets` and shows the returned `ticket_id` in a toast; the ticket appears in the sidebar list.
- **AC-T3 (Live timeline — 5.2.1):** while a ticket runs, the agent map advances `classify → worker → supervisor_review` in **< 2 s steps with no manual refresh**.
- **AC-T4 (Row detail — 5.2.2):** after completion every node row shows a status icon **and** a millisecond duration.
- **AC-T5 (Confidence band — 5.3.1):** the badge is **green for ≥ 80**, **amber for 60–79**, **red for < 60** (unit-tested over `confidence_band`).
- **AC-T6 (Approve — 5.3.2):** clicking **Approve Draft** calls `POST /tickets/{id}/approve` and moves the ticket from **Active** to **Closed** with a green check.
- **AC-T7 (Modify — 5.3.3):** editing the draft and saving `PATCH`es `/tickets/{id}` and updates the displayed confidence from the response.
- **AC-T8 (Escalation routing — 5.4.1):** a simulated `lockout` ticket appears under **Requires Manual Verification** and **not** in the auto-approval (Approve) flow.
- **AC-T9 (Claim — 5.4.2):** each escalation row shows tier + account status; clicking **Claim** removes it from the queue and adds the operator name to `execution_logs` (verified via `GET /tickets/{id}`).
- **AC-T10 (New endpoints offline — FR-12/13):** `pytest estc/tests/test_orchestrator_actions.py -v` is green via `TestClient` with no live infra — `approve` closes, `claim` appends `CLAIMED_BY:`, `PATCH` re-scores via a mocked classifier transport, all return `404` for unknown ids.
- **AC-T11 (UI client offline — FR-15):** `pytest estc/tests/test_ui_client.py -v` parses a canned `open→node→node→node→done` byte stream into ordered frames and asserts the confidence-band thresholds — no orchestrator required.
- **AC-T12 (UI image — 5.5.1):** `docker build -t estc-ui ./estc/services/ui` exits 0.
- **AC-T13 (Full stack healthy — 5.5.2):** `docker compose up -d` brings **all 5 services** to `healthy` (`docker compose ps` shows 5 healthy); the UI is reachable on `8501`.
- **AC-T14 (No-regression — FR-14):** no file under `graph/`, `rag/`, or the MCP servers changes; the Phase 4 suites (`test_graph_build`, `test_graph_nodes`, `test_orchestrator_api`) stay green.

### 6.2 Business & Functional Alignment

- **AC-B1 (Service realized — `design.md` § 1 / § 4 Service 4):** `ui-client` exists as a containerized Streamlit dashboard on `estc-net:8501`, consuming the orchestrator over REST + Event Stream — the human surface the architecture diagram names.
- **AC-B2 (Operations Center — speculation/FR-4):** the dashboard presents inbound tickets, a real-time agent execution map, the draft + confidence, Approve/Modify controls, and a human escalation queue — the full Support Specialist workflow.
- **AC-B3 (Human-in-the-loop honored):** low-confidence / escalation tickets are routed to manual verification and cannot be auto-approved (AC-T8), realizing the supervisor's 0.70-threshold intent at the human boundary.
- **AC-B4 (Read-only security preserved — `design.md` § 2):** the UI adds no new backend I/O path; every customer fact reaches it through the orchestrator's `AgentState`, and the MCP boundary stays read-only.
- **AC-B5 (Full E2E exit gate — 5.6.1–5.6.3):** on a fresh `docker compose up -d`, the canonical bug ticket shows `classify → bug_agent → supervisor_review`, a draft citing a GitHub issue, confidence ≥ 80 %, Approve closes it; the lockout ticket lands in Requires Manual Verification with auto-approval blocked; and the Ragas suite re-run against the live orchestrator keeps all three metrics ≥ 0.80 mean — satisfying the project Definition of Done.
- **AC-B6 (Offline-first parity — codebase convention):** the new endpoint + UI-client tests pass on a clean checkout with no keys; only the explicit 5.6 verifies need the live multi-container stack + a judge LLM key (5.6.3).

---

**Open items for the execution plan (Phase 5 plan):**
1. **`PATCH` re-evaluation semantics (FR-13).** Spec proposes re-scoring the edited draft on the existing `classifier-api` (intent-confidence proxy, no graph re-run). Alternatives to pin in the plan: (a) re-run only `supervisor_review` over the edited draft; (b) a lightweight heuristic (length/keyword) with no backend call; (c) leave confidence unchanged and only persist the edit. Decide before coding 5.3.3.
2. **Tier / account-status surfacing (5.4.2).** Spec parses them from the worker draft/`retrieved_context` to respect FR-14 (no graph/MCP change). Confirm acceptable, or schedule a separate (out-of-scope here) backend task adding a `customer_facts` field / `GET /customers/{id}` read-through.
3. **`claim`/`modify` via `graph.update_state`.** Editing `execution_logs`/`confidence_score`/`agent_draft_response` through the checkpointer is an *app-level* state edit, not a node change — confirm this reading of FR-14 (it touches `MemorySaver` values, not graph topology or node bodies).
4. **Live-stream rendering pattern.** Spec commits to `st.fragment` + in-loop placeholder writes with a synchronous `httpx-sse` iterator. If fragment behavior on the pinned `streamlit==1.38.*` proves flaky, fall back to `st.write_stream` over a generator or a manual `st.empty()` poll loop — pin the choice in the plan.
5. **Operator identity.** Currently a free-text dev field (no auth). Confirm dev-scope is acceptable for 5.4.2, or add a minimal name prompt at session start.
6. **Streamlit healthcheck endpoint.** Spec uses `GET /_stcore/health` with a urllib probe (no `curl` in slim). Confirm the path on the pinned Streamlit version during the plan's pre-flight.
7. **`ui-client` build context.** Unlike `orchestrator-app` (repo-root context), the UI builds from `./estc/services/ui` and imports nothing from `estc.*` at runtime (wire-shape contract only) — confirm no shared-schema import sneaks in (keeps the image torch-free).
