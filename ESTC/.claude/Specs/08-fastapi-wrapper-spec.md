# Architectural Specification: Phase 4.6 — FastAPI Wrapper for Orchestrator

**Status:** DRAFT / PROPOSED
**Associated Tasks:** Tasks 4.6.1 – 4.6.2 (`docs/plan.md` § Phase 4.6 — FastAPI Wrapper for Orchestrator); unblocks Exit Gate 4.7.1
**Target Files:**
- `estc/services/orchestrator/app/main.py` (new — FastAPI app: `POST /tickets`, `GET /tickets/{id}/stream` SSE, `GET /healthz`; task 4.6.1)
- `estc/services/orchestrator/app/schemas.py` (new — `CreateTicketRequest` / `CreateTicketResponse` Pydantic models)
- `estc/services/orchestrator/requirements.txt` (new — web layer + orchestrator runtime deps, incl. `sse-starlette`)
- `estc/services/orchestrator/Dockerfile` (new — image for the `orchestrator-app` service; task 4.6.2)
- `docker-compose.yml` (edit — flesh out the `orchestrator-app` block: build context, env, `8002:8002`, healthcheck, `depends_on`)
- `estc/tests/test_orchestrator_api.py` (new — offline SSE + endpoint tests via FastAPI `TestClient`)

**Consumes (unchanged):** `estc/services/orchestrator/graph/build.py` (`astream_ticket`, `run_ticket`, the module-level `graph`), `estc/services/orchestrator/graph/observability.py::configure_tracing`, `estc/shared/schemas/agent_state.py::AgentState`, `estc/shared/config.py::Settings`. **No node body, edge, or graph change** — Phase 4.6 only adds an HTTP/SSE skin and its container.

---


## 1. Executive Summary & Problem Statement

### 1.1 Objective & Context

Phase 4.4 produced a runnable LangGraph state machine and two entrypoints: `run_ticket(...)` (drive a ticket to a terminal `AgentState`) and `astream_ticket(...)` (an async generator yielding one `(node_name, update)` per node transition). Phase 4.4's spec explicitly reserved the network surface for this phase: *"the SSE endpoint should reuse `astream_ticket` directly (single streaming code path) rather than re-implementing `graph.astream`."* Phase 4.6 delivers exactly that surface — the `orchestrator-app` service named in `design.md` § 4 ("LangGraph execution worker runtime linked directly with LangSmith tracking endpoints") — and containerizes it on **port 8002**.

Two concrete artifacts are produced:

1. **The orchestrator FastAPI app** (task 4.6.1) — `estc/services/orchestrator/app/main.py` exposing:
   - `POST /tickets` — accept a raw ticket, mint a `ticket_id`, register it, return the id immediately (the graph is **not** run yet).
   - `GET /tickets/{id}/stream` — a **Server-Sent Events** response that drives the registered ticket through `astream_ticket` and emits **one `data:` event per LangGraph node transition** (`classify` → one worker → `supervisor_review`), followed by a terminal `done` event carrying the fully-merged final `AgentState`.
   - `GET /healthz` — a fast, dependency-free liveness probe for the compose healthcheck.
2. **Containerization** (task 4.6.2) — a `Dockerfile` and the fleshed-out `docker-compose.yml` `orchestrator-app` block so `docker compose up -d orchestrator-app` reports **healthy** on port 8002, on the shared `estc-net`, able to reach `classifier-api` and `postgres-db`.

After 4.6, a client `POST`s a ticket and opens the stream to watch the triage run live — the exact feed the Phase 5 Streamlit real-time agent map (5.2) and draft panel (5.3) consume.

### 1.2 Core Problem Statement

The graph is only reachable in-process (`await run_ticket(...)`); there is no HTTP boundary, no way for the UI (a separate container) to submit a ticket or observe per-node progress, and no orchestrator image in the compose network. The challenge is a **faithful, low-overhead HTTP/SSE adapter**: translate one HTTP request into one graph run, **serialize each node transition as exactly one SSE event in node order** (the 4.6.1 verify wants **≥ 4 `data:` events**; 4.7.1 wants the literal node markers `classify`, `bug_agent`, `supervisor_review` plus a terminal draft + non-zero confidence), and package the cross-package orchestrator (which imports `estc.shared`, `estc.services.orchestrator.graph/rag`, and the in-process MCP tool modules `estc.services.mcp_postgres/mcp_github`) into a single container — **without** changing any Phase 4.3/4.4 logic and **without** breaking the project's offline-deterministic test property (no LLM keys, GitHub in mock mode).

---

## 2. System Boundaries & Constraints

### 2.1 Architectural Boundaries

- **Upstream Trigger / Consumer:**
  - External HTTP clients: the Phase 5 Streamlit UI (`POST /tickets` from the ingestion form 5.1.2; `GET /tickets/{id}/stream` for the real-time map 5.2). In this phase the callers are `Invoke-RestMethod` / `curl -N` (4.6.1 verify) and the new `TestClient` suite.
- **Downstream Dependencies (what a ticket run touches, all via the unchanged graph):**
  - `astream_ticket` / `graph` from `build.py` — the single streaming code path (FR-3). The app **must not** re-implement `graph.astream`.
  - Transitively through the nodes: the `classifier-api` over HTTP (`CLASSIFIER_API_URL`), the in-process Postgres MCP tools (`estc.services.mcp_postgres.server`, connecting to `postgres-db`), the in-process GitHub MCP tools (`estc.services.mcp_github.server`, mock mode when `GITHUB_PAT` is empty), the Chroma RAG store (`./chroma_db`), and the `graph/llm.py` drafting helper (offline template path when no key). **The orchestrator calls the MCP tool functions in-process** (e.g. `bug_agent` does `from estc.services.mcp_github.server import search_issues`); the standalone `mcp-*-server` containers are **not** runtime dependencies of `orchestrator-app`.
  - `configure_tracing()` (observability.py) is called once at app startup; tracing stays **off** unless a `LANGSMITH_API_KEY` is present (clean-checkout invariant preserved).
- **Network boundary:** the service binds `0.0.0.0:8002` inside the container, published as `8002:8002`, on `networks: [estc-net]`. It resolves `classifier-api` and `postgres-db` by compose service name.
- **State boundary:** an **in-process ticket registry** (a module-level `dict[str, TicketRecord]`) holds the submitted `text`/`company_id`/status between `POST /tickets` and the stream `GET`. It is non-durable (process-lifetime only) — the same scope as the graph's `MemorySaver`. Run resumption/inspection remains keyed by `thread_id == ticket_id` in `MemorySaver`.

### 2.2 Technical & Operational Constraints

- **Async discipline (project rule, memory `feedback_mcp_async`):** the SSE generator is an `async def` generator that `async for`s over `astream_ticket`; route handlers are `async def`. No blocking `.invoke`/`.stream`. On Windows the test loop uses `WindowsSelectorEventLoopPolicy` (already in `conftest.py`).
- **SSE contract:** `Content-Type: text/event-stream`; each message is a single `data: <json>\n\n` frame (plus an `event:` label for `done`/`error`). Implemented with **`sse-starlette`'s `EventSourceResponse`** (handles client-disconnect, flushing, and keep-alive pings) — added to the orchestrator requirements. Response headers include `Cache-Control: no-cache` and `X-Accel-Buffering: no` so intermediaries do not buffer the stream.
- **Performance / Latency:** the stream begins emitting as soon as `classify` returns; total run inherits the Phase 4.4 **< 10 s** budget for the canonical ticket. `GET /healthz` returns in **< 50 ms** and loads **no** model (Chroma collection + bge embeddings are lazy via `lru_cache`, materialized only on the first retrieval, never at import or on the health path).
- **Determinism / offline-first:** the endpoint test runs fully offline using the Phase 4.4 monkeypatch trick (mock classifier transport → deterministic `bug` route; stub `aretrieve`; no LLM key → template draft; GitHub mock per `conftest.py`). The suite stays green on a clean checkout; the live container behavior is validated by the 4.6.2 / 4.7.1 verifies, not the unit suite.
- **Security & Compliance:** read-only MCP tool schemas only — the HTTP layer adds no new I/O path to any dependency the graph can't already reach (no raw SQL/shell). The SSE feed is **operator-facing** (the same tenant's ticket); per-node events carry the node name + that node's partial update, and the terminal event carries the `AgentState` (incl. `agent_draft_response`, `confidence_score`, `requires_escalation`, `execution_logs`). The PII rule from Phase 4.4 still holds: `raw_issue_text` is never written into `execution_logs`.
- **Resource Limits:** the in-process registry and `MemorySaver` grow with ticket volume for the process lifetime — acceptable for the single-worker dev orchestrator; flagged (as in 4.4 § 5.1 ec.7) for bounding/eviction or a durable store if throughput grows. Single Uvicorn worker (in-process `MemorySaver`/registry are not shared across workers).
- **Packaging / imports:** the app uses **absolute** `estc.*` imports (the dominant codebase convention used by every node/graph module), not relative imports. No `__init__.py` is added under `app/` (PEP 420 namespace packages, consistent with the repo). Importable as `estc.services.orchestrator.app.main`.
- **Container constraint:** the orchestrator imports across `estc/*` (shared, graph, rag, mcp_postgres, mcp_github), so the **Docker build context is the repo root** with `dockerfile: estc/services/orchestrator/Dockerfile`; `WORKDIR /app`, `ENV PYTHONPATH=/app`, and the whole `estc/` tree is copied so `estc.*` resolves (mirroring the repo's `pythonpath = ["."]`). The persisted `./chroma_db` is mounted read-only at `/app/chroma_db` (the relative `CHROMA_PATH` resolves against `WORKDIR`).

---

## 3. Functional Requirements

- **FR-1 (`POST /tickets` — task 4.6.1):** Accept JSON `{"text": str, "company_id": str | null}`; mint a unique `ticket_id` (`uuid4().hex`); store a `TicketRecord(text, company_id, status="pending")` in the in-process registry; return `201` with `{"ticket_id": str, "status": "pending"}`. The graph is **not** executed here (lazy-on-stream model). `company_id` is **optional**; when omitted it defaults to `"unknown"` (the canonical 4.7.1 ticket POSTs `text` only, and its `bug` path uses GitHub, not Postgres — so a missing company id never blocks the run).
- **FR-2 (`GET /tickets/{id}/stream` — task 4.6.1):** Return an `EventSourceResponse` (`text/event-stream`). Look up the `ticket_id`; **404** if unknown. Drive the ticket through the graph via `astream_ticket(id, text, company_id)` and emit **one `data:` event per node transition**, in node order, then a terminal `done` event. Set the record `status` to `running` while streaming and `done`/`error` on completion.
- **FR-3 (Single streaming code path):** The generator consumes `astream_ticket` directly (FR per 4.4 hand-off) — it does **not** call `graph.astream` itself. Per node it emits `data: {"event":"node","node":<name>,"ticket_id":<id>,"update":<partial-update-dict>}`. This guarantees the SSE feed and `run_ticket` observe identical events.
- **FR-4 (Terminal full-state event):** After the node stream is exhausted, the generator reads the fully-merged terminal state from the checkpointer (`graph.get_state({"configurable":{"thread_id":id}}).values`, normalized to `AgentState`) and emits `event: done` / `data: {"event":"done","ticket_id":<id>,"state": <AgentState.model_dump()>}`. The `state` carries `intent`, `agent_draft_response`, `confidence_score`, `requires_escalation`, and `execution_logs` — satisfying 4.7.1's "ending with a draft response and a non-zero confidence."
- **FR-5 (Event-count floor):** A normal run yields **3 node events** (`classify`, one worker, `supervisor_review`) **plus** the `done` event = **≥ 4 `data:` frames**, meeting the 4.6.1 verify. (An optional leading `event: open` frame may be emitted with `{ "ticket_id", "status":"running" }` for UI bootstrap, making 5; the floor is met without it.)
- **FR-6 (`GET /healthz` — task 4.6.2 healthcheck):** Return `200 {"status":"ok"}` with no dependency calls and no model load, so the container reports healthy before any ticket is processed.
- **FR-7 (Startup tracing hook):** On app startup (lifespan/startup event) call `configure_tracing()` exactly once; it is idempotent and never raises. No manual span instrumentation (LangGraph emits the child-run tree automatically when keyed).
- **FR-8 (Request/response schemas):** `app/schemas.py` defines `CreateTicketRequest(text: str, company_id: str | None = None)` and `CreateTicketResponse(ticket_id: str, status: str)`. `POST /tickets` uses `response_model=CreateTicketResponse`.
- **FR-9 (Dockerfile — task 4.6.2):** `estc/services/orchestrator/Dockerfile` on `python:3.11-slim`: install `estc/services/orchestrator/requirements.txt`, `COPY estc/ ./estc/`, `ENV PYTHONPATH=/app`, `EXPOSE 8002`, `CMD ["python","-m","uvicorn","estc.services.orchestrator.app.main:app","--host","0.0.0.0","--port","8002"]`.
- **FR-10 (Compose wiring — task 4.6.2):** The `orchestrator-app` block uses `build: { context: ., dockerfile: estc/services/orchestrator/Dockerfile }`, publishes `8002:8002`, joins `estc-net`, sets the runtime env (classifier URL, Postgres reader creds with `POSTGRES_HOST=postgres-db`, GitHub mock path, optional LLM/LangSmith keys), mounts `./chroma_db:/app/chroma_db:ro`, declares a `/healthz` healthcheck (with a generous `start_period` for import warmup), and `depends_on` `classifier-api` + `postgres-db` (both `service_healthy`).
- **FR-11 (No graph/logic drift):** Phase 4.6 changes **no** file under `graph/nodes/`, `graph/build.py`, `rag/`, or the MCP servers. It only adds `app/`, the requirements/Dockerfile, the compose edit, and the test.
- **FR-12 (Offline-deterministic test):** `estc/tests/test_orchestrator_api.py` exercises `/healthz`, `POST /tickets`, and the SSE stream with **no live infra**, asserting ≥ 4 `data:` events, the node order `classify → bug_agent → supervisor_review`, and a `done` event whose `state.agent_draft_response` is non-empty with `confidence_score > 0`.

---

## 4. Detailed Component Specifications & API Contracts

### 4.1 Interface Code & Data Shapes

**`estc/services/orchestrator/app/schemas.py`:**
```python
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel

class CreateTicketRequest(BaseModel):
    text: str
    company_id: Optional[str] = None   # optional; defaults to "unknown" at the handler

class CreateTicketResponse(BaseModel):
    ticket_id: str
    status: str                        # "pending"
```

**`estc/services/orchestrator/app/main.py` (shape — not final code):**
```python
from __future__ import annotations
import json, uuid
from dataclasses import dataclass
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from estc.services.orchestrator.graph.build import astream_ticket, graph
from estc.services.orchestrator.graph.observability import configure_tracing
from estc.shared.schemas.agent_state import AgentState
from estc.services.orchestrator.app.schemas import CreateTicketRequest, CreateTicketResponse

@dataclass
class TicketRecord:
    text: str
    company_id: str
    status: str = "pending"            # pending -> running -> done | error

_TICKETS: dict[str, TicketRecord] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_tracing()                # idempotent; off without a LANGSMITH key
    yield

app = FastAPI(title="ESTC Orchestrator", lifespan=lifespan)

@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}            # no deps, no model load

@app.post("/tickets", response_model=CreateTicketResponse, status_code=201)
async def create_ticket(req: CreateTicketRequest) -> CreateTicketResponse:
    ticket_id = uuid.uuid4().hex
    _TICKETS[ticket_id] = TicketRecord(text=req.text, company_id=req.company_id or "unknown")
    return CreateTicketResponse(ticket_id=ticket_id, status="pending")

@app.get("/tickets/{ticket_id}/stream")
async def stream_ticket(ticket_id: str) -> EventSourceResponse:
    rec = _TICKETS.get(ticket_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="unknown ticket_id")

    async def event_gen():
        rec.status = "running"
        yield {"event": "open", "data": json.dumps({"ticket_id": ticket_id, "status": "running"})}
        try:
            async for node_name, update in astream_ticket(ticket_id, rec.text, rec.company_id):
                yield {"event": "node",
                       "data": json.dumps({"event": "node", "node": node_name,
                                           "ticket_id": ticket_id, "update": _jsonable(update)})}
            values = graph.get_state({"configurable": {"thread_id": ticket_id}}).values
            final = values if isinstance(values, AgentState) else AgentState(**values)
            rec.status = "done"
            yield {"event": "done",
                   "data": json.dumps({"event": "done", "ticket_id": ticket_id,
                                       "state": final.model_dump()})}
        except Exception as exc:                      # node raised (classifier 5xx, DB error)
            rec.status = "error"
            yield {"event": "error", "data": json.dumps({"ticket_id": ticket_id, "error": str(exc)})}

    return EventSourceResponse(event_gen())
```
*(`_jsonable` coerces any non-JSON-native values in a node update — e.g. enum/`RetrievedChunk` — to a serializable form; in practice node updates are already lists/str/float/bool.)*

### 4.2 Endpoint / Method Contracts

| Route | Method | Request | Success | Errors |
|---|---|---|---|---|
| `/healthz` | `GET` | — | `200 {"status":"ok"}` | — |
| `/tickets` | `POST` | `CreateTicketRequest` (`text` required, `company_id` optional) | `201 CreateTicketResponse {ticket_id,status:"pending"}` | `422` (missing/invalid `text`) |
| `/tickets/{id}/stream` | `GET` | path `id` | `200 text/event-stream`: `[open?] → node×N (classify, worker, supervisor_review) → done` | `404` (unknown id); mid-stream node failure → `event: error` then close |

- **SSE frame contract:** every frame is `data: <json>`; `node` frames carry `{event:"node", node, ticket_id, update}`; the terminal frame is `event: done` + `data: {event:"done", ticket_id, state:<AgentState>}`. **Event-count floor: ≥ 4** `data:` frames per normal run (3 nodes + `done`).
- **thread_id contract:** the stream uses `thread_id == ticket_id`; `astream_ticket` already defaults to this. Re-`GET`ting a finished stream resumes the completed `MemorySaver` thread and yields no new node events (see § 5.1 ec.1).
- **Stream-mode contract:** inherited from `astream_ticket` (`stream_mode="updates"` → one `{node:update}` chunk per transition).

---

## 5. Edge Cases & Error Handling

### 5.1 Anticipated Edge Cases

1. **Re-opening a finished stream (same `ticket_id`).** `MemorySaver` keys by `thread_id == ticket_id`; the second `GET` resumes a thread already at `END`, so `astream_ticket` yields no node frames. Mitigation: the registry `status` is tracked; if `status == "done"`, the generator skips re-running and emits a single `done` frame rebuilt from `graph.get_state(...).values` (idempotent replay) — the consumer always sees a terminal state. A fresh run requires a fresh `POST` (new `uuid`).
2. **Missing / empty `company_id`.** Defaulted to `"unknown"`; the `bug` path (4.7.1) uses GitHub (mock), not Postgres, so the run completes. A `billing`/`lockout` ticket with `company_id="unknown"` will simply find no Postgres row and degrade to a no-facts draft (lower confidence → escalation) — correct, not an error.
3. **Unknown `ticket_id` on `GET /stream`.** Returns `404` before opening any stream (the registry has no record).
4. **Node raises mid-stream (classifier 5xx via `raise_for_status`, Postgres/MCP error).** The exception surfaces out of `astream_ticket`; the generator catches it, sets `status="error"`, emits an `event: error` frame, and closes the stream cleanly (HTTP 200 already committed — SSE error is in-band, not an HTTP status). No half-state is silently returned.
5. **Client disconnects mid-stream.** `EventSourceResponse` detects the closed connection and stops iterating the generator; the graph task is abandoned, the `MemorySaver` checkpoint persists, and the registry record stays `running` (a subsequent reconnect replays via ec.1). No server crash.
6. **Offline / no keys (CI default).** No LLM key → template draft; GitHub mock per `conftest.py`; `configure_tracing()` returns `False`. The endpoint test runs fully offline; only the live 4.6.2/4.7.1 verifies need the seeded Postgres + Chroma + classifier reachable.
7. **`/healthz` must not block on heavy imports.** The Chroma collection and bge embeddings are lazy (`lru_cache`), so importing the app and serving `/healthz` never loads the model; the compose healthcheck uses a generous `start_period` to absorb one-time import cost (langgraph/langchain/chromadb/torch import at module load).
8. **`graph.get_state().values` shape (Pydantic state).** May surface an `AgentState` instance or a field-keyed dict depending on the installed LangGraph; the generator normalizes with `values if isinstance(values, AgentState) else AgentState(**values)` (same guard `run_ticket` uses).

### 5.2 Error Handling & State Recovery Matrix

| Trigger / Exception | Handled State / Action | Fallback Behavior / Mitigation |
|---|---|---|
| Unknown `ticket_id` on `/stream` | `HTTPException(404)` before stream opens | Client re-`POST`s to obtain a valid id (ec.3) |
| `text` missing/invalid on `POST` | FastAPI `422` from `CreateTicketRequest` | Standard validation error body |
| `company_id` omitted | Defaulted to `"unknown"` | Run proceeds; billing/lockout degrade to no-facts draft (ec.2) |
| Node raises mid-run | Generator catches → `event: error`, `status="error"`, stream closes | In-band SSE error (HTTP already 200); no half-state returned (ec.4) |
| Client disconnects | `EventSourceResponse` stops the generator | Checkpoint persists; reconnect replays terminal state (ec.1/ec.5) |
| Re-open finished stream | `status=="done"` → replay single `done` frame | No double-run; new run needs a new `POST` (ec.1) |
| `/healthz` during warmup | Returns `200` immediately (no deps) | `start_period` covers import cost so the probe doesn't flap (ec.7) |
| LangSmith key absent | `configure_tracing()` → `False`, tracing off | Graph runs untraced; clean-checkout invariant (ec.6) |
| `get_state().values` is a dict | Normalized to `AgentState(**values)` | Terminal `done` event always well-typed (ec.8) |

---

## 6. Acceptance Criteria

### 6.1 Technical Acceptance Criteria

- **AC-T1 (`POST /tickets`):** A `POST /tickets {"text":"...","company_id":"9422"}` returns `201` with a non-empty `ticket_id` and `status == "pending"`; the id is subsequently resolvable by `GET /tickets/{id}/stream` (no 404).
- **AC-T2 (SSE event count — 4.6.1 verify):** `curl -N http://localhost:8002/tickets/<id>/stream` (or `TestClient` reading the stream body) yields **≥ 4** `data:` frames for the canonical ticket.
- **AC-T3 (Node order & markers — 4.7.1):** The `node` frames appear in order `classify` → `bug_agent` → `supervisor_review` for the canonical bug ticket; the terminal `done` frame's `state.agent_draft_response` is non-empty and `state.confidence_score > 0`.
- **AC-T4 (One event per transition — FR-3):** Exactly one `node` frame is emitted per LangGraph node transition (no duplicates, no skips), proven by the offline test counting frames against the known 3-node path.
- **AC-T5 (`/healthz`):** `GET /healthz` returns `200 {"status":"ok"}` in < 50 ms with no model load.
- **AC-T6 (404 on unknown id):** `GET /tickets/does-not-exist/stream` returns `404`.
- **AC-T7 (Offline determinism — FR-12):** `.venv\Scripts\pytest estc/tests/test_orchestrator_api.py -v` is all green with no LLM keys and GitHub in mock mode; no live classifier/Postgres/Chroma required.
- **AC-T8 (Container healthy — 4.6.2 verify):** `docker compose up -d orchestrator-app` reports **healthy**; `docker compose ps orchestrator-app` shows `healthy` within the `start_period`; the app listens on `8002`.
- **AC-T9 (No-regression):** No file under `graph/`, `rag/`, or the MCP servers changes; `.venv\Scripts\pytest estc/tests/test_graph_build.py estc/tests/test_graph_nodes.py -q` stays green (FR-11).

### 6.2 Business & Functional Alignment

- **AC-B1 (Service realized — `design.md` § 4 Service 3):** `orchestrator-app` exists as a containerized FastAPI service on `estc-net:8002`, reaching `classifier-api` and `postgres-db` by service name, with the LangSmith hook wired (`configure_tracing()` at startup).
- **AC-B2 (Real-time map feed — Phase 5.2 readiness):** The per-node SSE feed advances `classify → worker → supervisor_review` as discrete events, ready for the Streamlit vertical timeline; the `done` event delivers the draft + confidence the draft panel (5.3) renders.
- **AC-B3 (Single streaming code path — 4.4 hand-off):** The endpoint reuses `astream_ticket` verbatim (FR-3), so "what the SSE client sees" equals "what `run_ticket` sees" — no divergence.
- **AC-B4 (Read-only security — `design.md` § 2):** The HTTP layer adds no new I/O path; the graph still reaches only the read-only MCP tool schemas. `raw_issue_text` is never written into `execution_logs`; SSE is operator-facing for the same ticket.
- **AC-B5 (Offline-first parity — codebase convention):** Like every prior phase, the wrapper degrades deterministically with no external keys, preserving "tests pass on a clean checkout"; only the explicit 4.6.2/4.7.1 verifies require the live multi-container stack.

---

**Open items for the execution plan (Phase 4.6 plan):**
1. **`sse-starlette` vs. manual `StreamingResponse`.** Spec assumes `sse-starlette` (disconnect handling + pings). If a new dependency is undesirable, fall back to `StreamingResponse(media_type="text/event-stream")` hand-formatting `data: ...\n\n` — pin the choice in the plan and reflect it in `requirements.txt`.
2. **Docker image weight.** The orchestrator pulls `chromadb` + `sentence-transformers` (→ `torch`) for RAG; the image is large and the first import is slow. Plan should set a generous compose `start_period` and may pre-bake/ingest or mount `./chroma_db` (read-only mount assumed here). Confirm `CHROMA_PATH` in `rag/ingest.py` is the relative `./chroma_db` so the `/app/chroma_db` mount resolves.
3. **Healthcheck tool.** `python:3.11-slim` ships no `curl`; the existing `classifier-api` healthcheck uses `curl`. Plan should use a Python-based probe (`python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8002/healthz').status==200 else 1)"`) or `apt-get install curl` — decide and apply consistently.
4. **Build context change.** `orchestrator-app` must build from the **repo root** (`context: .`, `dockerfile: estc/services/orchestrator/Dockerfile`) to copy the whole `estc/` tree — a deviation from the sub-directory contexts the other services use; call it out in the plan.
5. **Re-open semantics (ec.1).** Confirm the desired behavior for a second `GET` on a finished ticket — replay terminal state (spec default) vs. 409/disallow — and whether Phase 5's "Modify & re-evaluate" (5.3.3) needs a fresh run id.
6. **`POST` body for 4.7.1.** The canonical exit-gate ticket POSTs `text` only; verify the optional-`company_id` default (`"unknown"`) is acceptable, or have the UI always supply `company_id` (the 5.1.2 form has the field).
