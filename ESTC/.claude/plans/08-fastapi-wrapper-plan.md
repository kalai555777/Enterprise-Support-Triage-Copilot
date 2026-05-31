# Execution Plan: Phase 4.6 — FastAPI Wrapper for Orchestrator
**Source spec:** `.claude/Specs/08-fastapi-wrapper-spec.md`
**Source plan section:** `docs/plan.md` § Phase 4.6 (tasks 4.6.1 – 4.6.2); unblocks Exit Gate 4.7.1
**Status:** COMPLETE — all gates green, **including the live containerized stack**. EG-1 (6/6 API tests), EG-2 (5 SSE `data:` frames), EG-3 (29 passed / 1 skipped; no graph/rag/mcp source change), **EG-4** (`docker compose up -d orchestrator-app` → healthy in ~15s; `/healthz` ok on 8002), and the **4.7.1 exit gate** (live canonical bug ticket streamed `classify → bug_agent → supervisor_review → done`, 5 frames, draft cites #42/#37, confidence 0.85, `AUTO_APPROVED`) all pass.

*Notable fixes made during execution (beyond the original plan, all necessary):*
1. *The venv had `starlette 1.1.0` (pulled by `sse-starlette 3.4.4`), incompatible with `fastapi 0.115` — it broke **every** FastAPI app incl. the classifier. Pinned `starlette<0.47` + `sse-starlette<3`.*
2. *The orchestrator `requirements.txt` was re-pinned to **exact** working-venv versions (chromadb 1.5.9, langchain 1.3.2, langgraph 1.2.2, etc.) — loose `>=` ranges caused a pip "resolution-too-deep" backtracking failure in the image build.*
3. *The `classifier-api` healthcheck used `curl` (absent in `python:3.11-slim`) — switched to a urllib probe so it reports healthy and unblocks `orchestrator-app`'s `depends_on`.*
4. *The `./chroma_db` mount was changed from `:ro` to read-write — chromadb 1.5.9 opens the SQLite store in write mode (WAL) even for reads, so `:ro` failed with "attempt to write a readonly database".*
5. *`orchestrator-app` `GITHUB_PAT` defaulted to empty (deterministic mock mode, per spec AC-B5). With the host `.env`'s real PAT, the **Phase 3.2** GitHub server's real-API path raises `IndexError` on an empty issue search — a separate out-of-scope bug to harden in the GitHub MCP server.*

---

## Context

This plan operationalizes Phase 4.6 of the ESTC roadmap: **wrapping the Phase 4.4 LangGraph engine in an HTTP/SSE service (`orchestrator-app`, port 8002) so a client can `POST` a ticket and stream one event per node transition**, and containerizing it into the compose network. Its consumers are the 4.6.1/4.7.1 verifies (`Invoke-RestMethod` + `curl -N`) and the Phase 5 Streamlit UI (real-time agent map 5.2, draft panel 5.3). The work has three threads that must be done in order:

1. **FastAPI app** (Python — task 4.6.1): author `estc/services/orchestrator/app/schemas.py` (request/response models) and `estc/services/orchestrator/app/main.py` exposing `GET /healthz`, `POST /tickets` (register + return `ticket_id`), and `GET /tickets/{id}/stream` (SSE that drives `astream_ticket` and emits one `data:` frame per node transition + a terminal `done` frame).
2. **Containerization** (Docker/compose — task 4.6.2): `estc/services/orchestrator/requirements.txt`, `estc/services/orchestrator/Dockerfile`, and the fleshed-out `docker-compose.yml` `orchestrator-app` block (repo-root build context, `8002:8002`, env, `chroma_db` mount, `/healthz` healthcheck, `depends_on`).
3. **Test harness** (Python — plan-internal task 4.6.3): `estc/tests/test_orchestrator_api.py` proving `/healthz`, `POST /tickets`, the ≥ 4-event SSE stream, node order, and 404 — fully offline by reusing the Phase 4.4 monkeypatch fixtures.

**Design notes — what this plan deliberately decides (and mirrors):**
- **Lazy-on-stream execution.** `POST /tickets` only registers the ticket (in-process `dict`) and returns a `uuid` id; the graph runs when `GET /tickets/{id}/stream` is opened. This maps 1:1 to `astream_ticket` and matches the 4.6.1 verify (POST, then `curl -N` the stream shows the events). Spec § 1.1 / FR-1/FR-2.
- **Single streaming code path (4.4 hand-off).** The SSE generator consumes `astream_ticket` verbatim — it does **not** re-implement `graph.astream` — so "what the SSE client sees" equals "what `run_ticket` sees" (spec FR-3, AC-B3).
- **`sse-starlette` (already in the venv, v3.4.4)** provides `EventSourceResponse` (disconnect handling, framing). Added to the orchestrator requirements for the container.
- **Edges/logic frozen.** Phase 4.6 changes **no** file under `graph/`, `rag/`, or the MCP servers — it only adds `app/`, the packaging files, the compose edit, and the test (spec FR-11).
- **Offline-first parity (codebase convention).** The endpoint test reuses the exact `offline_bug_run` + `_force_template_path` fixtures from `estc/tests/test_graph_build.py` (mock classifier transport → deterministic `bug` route; stub `bug_agent.aretrieve`; no LLM key → template draft; GitHub forced to mock by `conftest.py`), so the suite stays green on a clean checkout with no live infra. Live container behavior is proven by the Docker-gated EG-4 and the Phase 4.7 exit gate.

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**; the venv interpreter is `.venv\Scripts\python.exe` (**Python 3.12.10** — the project's actual toolchain; `plan.md` names 3.11 and the container uses `python:3.11-slim`. Non-blocking, same deviation recorded in the Phase 4.4 plan PF-2). A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** Confirm the source spec exists and is the version this plan targets.
  **Verify:** `Get-Content .claude/Specs/08-fastapi-wrapper-spec.md | Select-String "GET /tickets/\{id\}/stream"` returns ≥ 1 match.
- [ ] **PF-2** Confirm the venv interpreter is usable.
  **Verify:** `.venv\Scripts\python --version` reports `Python 3.12.*` (3.11 per plan.md is acceptable; record deviation).
- [ ] **PF-3** Confirm `build.py` exposes the streaming surface this app wraps, with the expected shapes (Phase 4.6 must not modify it).
  **Verify:** `.venv\Scripts\python -c "import inspect; from estc.services.orchestrator.graph.build import astream_ticket, run_ticket, graph; assert inspect.isasyncgenfunction(astream_ticket); assert graph.checkpointer is not None; print('ok')"` prints `ok`.
- [ ] **PF-4** Confirm `sse-starlette` is importable (the chosen SSE library).
  **Verify:** `.venv\Scripts\python -c "from sse_starlette.sse import EventSourceResponse; print('ok')"` prints `ok`.
- [ ] **PF-5** Confirm the FastAPI + TestClient stack is available (the test transport).
  **Verify:** `.venv\Scripts\python -c "from fastapi import FastAPI; from fastapi.testclient import TestClient; print('ok')"` prints `ok`.
- [ ] **PF-6** Confirm `CHROMA_PATH` is the relative `./chroma_db` (so the container's `/app/chroma_db` read-only mount resolves against `WORKDIR`).
  **Verify:** `Get-Content estc/services/orchestrator/rag/ingest.py | Select-String 'CHROMA_PATH = "./chroma_db"'` returns 1 match.
- [ ] **PF-7** Confirm `conftest.py` forces GitHub MCP into mock mode (the offline bug path relies on it).
  **Verify:** `Get-Content estc/tests/conftest.py | Select-String "GITHUB_MOCK_PATH"` returns ≥ 1 match.
- [ ] **PF-8** Confirm the current `orchestrator-app` compose block is the stub to be replaced (build-context-only).
  **Verify:** `docker compose config --services` lists `orchestrator-app` (or `Get-Content docker-compose.yml | Select-String "orchestrator-app"` returns 1 match).
- [ ] **PF-9** Confirm `astream_ticket` defaults `thread_id` to the `ticket_id` (so the app passes no extra config).
  **Verify:** `Get-Content estc/services/orchestrator/graph/build.py | Select-String 'thread_id'` shows the `{"configurable": {"thread_id": ticket_id}}` default.

---

## Task 4.6.1 — Orchestrator FastAPI App

### 4.6.1-a Request/response schemas (`app/schemas.py`)
- [ ] Create `estc/services/orchestrator/app/schemas.py` (no `__init__.py`; PEP 420). Define `CreateTicketRequest(text: str, company_id: Optional[str] = None)` and `CreateTicketResponse(ticket_id: str, status: str)` (spec § 4.1, FR-8). Use absolute-import-friendly module (importable as `estc.services.orchestrator.app.schemas`).
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.app.schemas import CreateTicketRequest, CreateTicketResponse; CreateTicketRequest(text='hi'); print('ok')"` prints `ok`. (AC-T1)

### 4.6.1-b App skeleton: `/healthz` + `POST /tickets` + in-process registry + tracing lifespan
- [ ] Create `estc/services/orchestrator/app/main.py`. Add the module-level registry `_TICKETS: dict[str, TicketRecord]` (a small `@dataclass TicketRecord(text, company_id, status="pending")`). Build `app = FastAPI(title="ESTC Orchestrator", lifespan=lifespan)` where `lifespan` calls `configure_tracing()` once (FR-7). Add `GET /healthz -> {"status":"ok"}` with **no** dependency/model load (FR-6). Add `POST /tickets` (`response_model=CreateTicketResponse, status_code=201`): mint `uuid.uuid4().hex`, store `TicketRecord(text, company_id or "unknown")`, return the id with `status="pending"` (FR-1). Use **absolute** `estc.*` imports throughout (codebase convention; spec § 2.2).
  **Verify:** `.venv\Scripts\python -c "from fastapi.testclient import TestClient; from estc.services.orchestrator.app.main import app; c=TestClient(app); assert c.get('/healthz').json()['status']=='ok'; r=c.post('/tickets', json={'text':'x','company_id':'9422'}); assert r.status_code==201 and r.json()['ticket_id']; print('ok')"` prints `ok`. (AC-T1, AC-T5)

### 4.6.1-c SSE stream endpoint (`GET /tickets/{id}/stream`)
- [ ] Add `GET /tickets/{ticket_id}/stream`: look up the record (raise `HTTPException(404)` if unknown — FR-2/ec.3); return `EventSourceResponse(event_gen())`. `event_gen` is an `async def` generator that (1) sets `status="running"` and yields an `open` frame; (2) `async for node_name, update in astream_ticket(ticket_id, rec.text, rec.company_id)` yields one `node` frame per transition carrying `{event:"node", node, ticket_id, update}` (coerce the update to JSON-native via a small `_jsonable` helper — FR-3); (3) reads `graph.get_state({"configurable":{"thread_id":ticket_id}}).values`, normalizes via `values if isinstance(values, AgentState) else AgentState(**values)` (ec.8), sets `status="done"`, and yields a terminal `done` frame with `{event:"done", ticket_id, state: final.model_dump()}` (FR-4); (4) wraps (2)–(3) in `try/except Exception` that sets `status="error"` and yields an `error` frame (ec.4). Set response headers `Cache-Control: no-cache`, `X-Accel-Buffering: no` (spec § 2.2). **Reuse `astream_ticket`; do not call `graph.astream` directly.**
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.app.main import app; assert any(getattr(r,'path','')=='/tickets/{ticket_id}/stream' for r in app.routes); print('ok')"` prints `ok`. Full streaming behavior covered by AC-T2/AC-T3/AC-T4 in Task 4.6.3.

---

## Task 4.6.2 — Containerization (orchestrator-app, port 8002)

### 4.6.2-a Orchestrator runtime requirements (`requirements.txt`)
- [ ] Create `estc/services/orchestrator/requirements.txt`: the web layer (`fastapi==0.115.*`, `uvicorn[standard]==0.30.*`, `sse-starlette>=2.1`, `httpx==0.27.*`, `pydantic==2.9.*`, `pydantic-settings>=2.4`, `python-dotenv==1.0.*`) plus the orchestrator runtime deps used in-process (`langgraph>=0.2.0`, `langchain>=0.3.0`, `langchain-community>=0.3.0`, `chromadb>=0.5.0`, `sentence-transformers>=3.1.0`, `langsmith>=0.1.0`, `fastmcp>=3.3.0`, `psycopg[binary]>=3.2.0`, `psycopg-pool>=3.2.0`, `PyGithub>=2.4.0`). The optional cloud LLM providers (`langchain-openai`/`-anthropic`/`-huggingface`) are intentionally omitted — `graph/llm.py` imports them only inside a key-gated branch, so the offline template path needs none of them (keeps the image leaner).
  **Verify:** `Get-Content estc/services/orchestrator/requirements.txt | Select-String "sse-starlette","fastapi","langgraph","chromadb"` returns 4 matches.

### 4.6.2-b Dockerfile (repo-root build context)
- [ ] Create `estc/services/orchestrator/Dockerfile` on `python:3.11-slim`, `WORKDIR /app`: `COPY estc/services/orchestrator/requirements.txt ./requirements.txt`; `RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt`; `COPY estc/ ./estc/`; `ENV PYTHONPATH=/app`; `EXPOSE 8002`; `CMD ["python","-m","uvicorn","estc.services.orchestrator.app.main:app","--host","0.0.0.0","--port","8002"]` (FR-9). The build context is the **repo root** (set in compose, 4.6.2-c) so `COPY estc/` captures `shared`, `graph`, `rag`, and the in-process MCP modules.
  **Verify (build is Docker-gated, see EG-4):** `Get-Content estc/services/orchestrator/Dockerfile | Select-String "PYTHONPATH=/app","8002","app.main:app"` returns 3 matches.

### 4.6.2-c Compose wiring (`docker-compose.yml` orchestrator-app block)
- [ ] Replace the stub `orchestrator-app` block with: `build: { context: ., dockerfile: estc/services/orchestrator/Dockerfile }`; `container_name: orchestrator-app`; `ports: ["8002:8002"]`; `networks: [estc-net]`; `volumes: ["./chroma_db:/app/chroma_db:ro"]`; env (`CLASSIFIER_API_URL: http://classifier-api:8001`, `POSTGRES_HOST: postgres-db`, `POSTGRES_PORT: "5432"`, `POSTGRES_DB`, `POSTGRES_READER_USER`, `POSTGRES_READER_PASSWORD`, `GITHUB_PAT: ${GITHUB_PAT:-}`, `GITHUB_MOCK_PATH: /app/estc/tests/fixtures/github_mock.json`, `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`LANGSMITH_API_KEY` passthrough with `:-` defaults, `LANGSMITH_TRACING: ${LANGSMITH_TRACING:-false}`, `LANGSMITH_PROJECT: ${LANGSMITH_PROJECT:-estc-dev}`); `depends_on: { classifier-api: {condition: service_healthy}, postgres-db: {condition: service_healthy} }`; and a Python-based healthcheck (no `curl` in slim): `test: ["CMD","python","-c","import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8002/healthz').status==200 else 1)"]`, `interval: 15s`, `timeout: 5s`, `retries: 5`, `start_period: 90s` (covers torch/chroma import warmup — ec.7). Add a `healthcheck` to `classifier-api`'s dependency only if it already declares one (it does).
  **Verify:** `docker compose config` parses without error and `docker compose config | Select-String "8002","orchestrator-app","/healthz"` returns matches.

---

## Task 4.6.3 — Orchestrator API Test Harness *(plan-internal; satisfies AC-T1…AC-T7)*

### 4.6.3-a Test file & offline fixtures
- [ ] Create `estc/tests/test_orchestrator_api.py`. Build `client = TestClient(app)` from `estc.services.orchestrator.app.main`. Port the two fixtures from `estc/tests/test_graph_build.py`: `_force_template_path` (autouse — strips LLM keys, `llm._chat_model.cache_clear()`) and `offline_bug_run` (monkeypatch `classify_mod.httpx` → `MockTransport` returning `{"intent":"bug","confidence":0.85}`; `bug_mod.aretrieve` → async `[]`). These make the real `graph` run a deterministic `bug` ticket with zero live infra (conftest forces the GitHub mock). Add a helper that opens the stream and returns the raw body + the list of parsed `data:` JSON objects.
  **Verify:** `.venv\Scripts\pytest --collect-only estc/tests/test_orchestrator_api.py` collects ≥ 5 items with no import errors.

### 4.6.3-b Test cases — must include at minimum (AC-T1 … AC-T7 bar)
- [ ] `test_healthz` → `GET /healthz` returns `200` and `{"status":"ok"}` (**AC-T5**).
- [ ] `test_create_ticket_returns_id` → `POST /tickets {"text":...,"company_id":"9422"}` returns `201`, non-empty `ticket_id`, `status=="pending"` (**AC-T1**).
- [ ] `test_create_ticket_optional_company` → `POST /tickets {"text":...}` (no `company_id`) returns `201` (FR-1 / ec.2).
- [ ] `test_stream_emits_min_four_events` *(uses `offline_bug_run`)* → POST then GET the stream; assert **≥ 4** `data:` frames (**AC-T2**, the literal 4.6.1 verify).
- [ ] `test_stream_node_order_and_done` *(uses `offline_bug_run`)* → the `node` frames appear in order `classify` → `bug_agent` → `supervisor_review`, exactly one per transition; the terminal `done` frame's `state.agent_draft_response` is non-empty and `state.confidence_score > 0` (**AC-T3, AC-T4**).
- [ ] `test_stream_unknown_ticket_404` → `GET /tickets/nope/stream` returns `404` (**AC-T6**).

  **Verify:** `.venv\Scripts\pytest estc/tests/test_orchestrator_api.py -v` reports **all green** with at least 6 passed (**AC-T7**).

---

## Phase 4.6 Exit Gate

- [ ] **EG-1 (endpoint suite, AC-T1…AC-T7)** — All offline app tests pass with no live infra.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_orchestrator_api.py -v --tb=short` reports **0 failed**, ≥ 6 passed.
- [ ] **EG-2 (import + ≥4-event smoke, the 4.6.1 verify shape)** — The app imports and a single ticket streams ≥ 4 `data:` frames offline.
  **Verify:** `.venv\Scripts\python -c "from fastapi.testclient import TestClient; ...POST /tickets then read /stream; assert body.count('data:') >= 4; print('EVENTS_OK')"` prints `EVENTS_OK`. *(In practice run via EG-1's `test_stream_emits_min_four_events`; this is the inline equivalent.)*
- [ ] **EG-3 (no-regression, FR-11)** — Phase 4.6 (app/packaging only) breaks no prior phase and changes no graph/node file.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_graph_build.py estc/tests/test_graph_nodes.py -q` reports **0 failed**; `git diff --name-only` shows no change under `estc/services/orchestrator/graph/`, `estc/services/orchestrator/rag/`, or `estc/services/mcp_*`.
- [ ] **EG-4 (container healthy — the literal 4.6.2 verify; Docker-gated)** — The image builds and the service reports healthy on 8002. **Run only when Docker Desktop is available** (otherwise this gate is deferred to the Phase 4.7 / 5.6 live stack, mirroring the 4.4 live-e2e skip-guard).
  **Verify:** `docker compose up -d --build orchestrator-app` then within `start_period` `docker compose ps orchestrator-app` shows `healthy`; `Invoke-RestMethod http://localhost:8002/healthz` returns `status: ok`. **Fallback if Docker is unavailable:** EG-1/EG-2 (offline ASGI) stand in; flag EG-4 for the 4.7 exit gate.
- [ ] **EG-5 (clean-boot regression)** — Confirm the offline suite still passes from a clean state.
  **Verify:** re-run EG-1 after `git stash`-free clean checkout of the new files; must succeed.

---

## Risks & Open Questions

1. **TestClient + async graph on Windows.** `TestClient` drives the async SSE generator through Starlette's portal; the repo's `conftest.py` sets `WindowsSelectorEventLoopPolicy`. Mitigation: the offline fixtures keep each run sub-second and deterministic; if the portal mishandles the async generator, fall back to `httpx.ASGITransport` + `AsyncClient` in the test. Confirmed `sse-starlette` 3.4.4 and `fastapi` TestClient are present (PF-4/PF-5).
2. **Docker image weight / build time.** `chromadb` + `sentence-transformers` pull `torch` (large; slow first import). Mitigation: `start_period: 90s` on the healthcheck (ec.7); `/healthz` loads no model. If build time is prohibitive on CI, EG-4 stays Docker-gated and the offline gates (EG-1/EG-2) are the merge bar.
3. **`chroma_db` mount for the live bug path.** A live (non-mock) bug ticket needs the persisted `./chroma_db` for `kb_technical` retrieval; the read-only mount supplies it. The offline test stubs `aretrieve`, so EG-1 doesn't need it; the live EG-4/4.7 run does — ensure `python services/orchestrator/rag/ingest.py` has been run so `./chroma_db` exists before EG-4.
4. **SSE frame counting in tests.** `EventSourceResponse` may interleave keep-alive comment/ping lines; the test counts only `data:`-prefixed lines (and parses their JSON), not raw line count, so pings don't inflate or corrupt the assertion. The finite generator closes the stream so `TestClient` reads to completion.
5. **Re-open semantics (spec ec.1).** A second `GET` on a finished `ticket_id` resumes the completed `MemorySaver` thread (no new node frames). The spec's chosen behavior is to replay a single `done` frame from `graph.get_state(...)`; confirm this is acceptable, or have Phase 5.3.3 "Modify & re-evaluate" mint a fresh ticket id. Decision needed before implementing the `status=="done"` replay branch.
6. **`curl` absence in `python:3.11-slim`.** The healthcheck uses a Python `urllib` probe instead of `curl` (the existing `classifier-api` block uses `curl`, which may itself be latently broken on slim). Non-blocking for this phase; noted for a possible follow-up to align the classifier healthcheck.

---

## Out of Scope (explicitly deferred)

- `POST /tickets/{id}/approve`, `PATCH /tickets/{id}`, and a `GET /tickets/{id}` result endpoint — Phase 5.3 (draft panel / approve-modify).
- A durable (Postgres/SQLite) checkpointer and registry, plus checkpoint/registry eviction-bounding — post-Phase-4 if ticket volume grows (spec § 2.2).
- The full multi-service E2E smoke (UI → SSE → draft → approve) — Phase 5.6.
- The live 4.7.1 exit-gate run (canonical bug ticket against the live containerized stack producing `classify`/`bug_agent`/`supervisor_review` over real SSE) — Phase 4.7, which consumes this app.
- Multi-worker / horizontal scaling of the orchestrator (the in-process registry + `MemorySaver` assume a single worker) — out of scope for the dev stack.

---

**Awaiting `Proceed` to begin execution at PF-1.**
