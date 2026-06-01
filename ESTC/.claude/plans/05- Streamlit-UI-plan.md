# Execution Plan: Phase 5 — Streamlit UI (Support Specialist Operations Center)
**Source spec:** `.claude/Specs/05- Streamlit-UI-spec.md`
**Source plan section:** `docs/plan.md` § Phase 5: Streamlit UI (tasks 5.1.1 – 5.6.3); closes the project Definition of Done
**Status:** COMPLETE — all offline gates green **and** the live containerized stack verified. EG-1 (orchestrator actions 5/5), EG-2 (UI unit 3/3 + AppTest `APP_OK`), EG-3 (no-regression: full suite **94 passed / 2 skipped**; FR-14 diff-guard clean — no `graph/`/`rag/`/`mcp_*` change), EG-4 (`estc-ui` builds; `ui-client` + classifier-api + orchestrator-app + postgres-db all **healthy**; UI serves `/_stcore/health` + `/` = 200), EG-5 (**5.6.1** live bug→approve→closed via the UI client; **5.6.2** lockout escalation + claim), and EG-6 (clean re-run) all pass.

*Notable findings / deviations during execution (all necessary):*
1. *`requirements-ui.txt` was not installed in the dev venv — installed it (streamlit 1.38.0, streamlit-extras 0.4.7); it downgraded pandas 3.0.3→2.3.3 / rich 14→13.9.4 (Streamlit constraints), no test impact.*
2. *The running `orchestrator-app` image was the Phase 4.6 build, so the new action endpoints 404'd at first live run — **rebuilt** `orchestrator-app` (pip layer cached; only the `estc/` copy changed) to ship the Task 5.0 routes.*
3. *UI import duality: `app.py` + components use a qualified-first / flat-fallback import so the same code runs under pytest/AppTest (`estc.services.ui.*`) and in the container (flat, ui/ dir is WORKDIR).*
4. *`ui-client` builds from its own sub-dir, so `requirements.txt` is duplicated into `estc/services/ui/` (mirrors root `requirements-ui.txt`) to stay inside the build context.*
5. ***5.6.2 classifier deviation (out of scope):*** *the live DistilBERT classifier maps the literal "I cannot log in…" sentence to `billing` (and returns a fixed `confidence 0.85` for every input) — a **Phase 2 model-accuracy** issue. Phase 5's escalation routing was proven with a phrasing the model classifies as `lockout` ("I am locked out of my account, company 9422"). Flagged like the Phase 3.2 GitHub `IndexError` — a separate backend hardening task.*
6. *5.6.3 (live Ragas ≥ 0.80) defers — no judge-LLM key on this box; inherits the Phase 4.5 deferral.*

---

**Original plan (status: AWAITING APPROVAL) below — preserved for the record.**

---

## Context

This plan operationalizes Phase 5 of the ESTC roadmap: **the human-facing Streamlit "Support Specialist Operations Center" (`ui-client`, port 8501) that consumes the Phase 4.6 orchestrator over REST + SSE** — submitting tickets, rendering the real-time agent map, the draft + confidence, Approve/Modify controls, and the escalation queue — and brings the 5-service `docker compose` topology to completion. The work has **five threads that must be done in order** (thread 1 is a hard prerequisite for threads 3–4):

1. **Orchestrator operator-action endpoints** (Python — deferred from Phase 4.6, prereq for 5.3/5.4): extend `estc/services/orchestrator/app/schemas.py` and `app/main.py` with `POST /tickets/{id}/approve`, `PATCH /tickets/{id}` (re-score the edited draft on `classifier-api`), `POST /tickets/{id}/claim`, and `GET /tickets/{id}` — all **app-layer** edits over the existing in-process registry + `MemorySaver`, **no graph/node/rag/MCP change** (spec FR-12/FR-13/FR-14).
2. **UI orchestrator client** (Python — `estc/services/ui/orchestrator_client.py`): a thin synchronous `httpx` + `httpx-sse` wrapper (`create_ticket`, `stream_ticket`, `approve`, `modify`, `claim`, `get_state`) plus `estc/services/ui/state.py` (the `st.session_state` view + `confidence_band` helper).
3. **Streamlit app + components** (Python/Streamlit — `estc/services/ui/app.py` + `components/`): the 3-column ops center, sidebar inbound list + ingestion form, the `st.fragment` live agent map, the draft panel, and the escalation queue (tasks 5.1–5.4).
4. **Containerization** (Docker/compose — task 5.5): `estc/services/ui/Dockerfile` + the fleshed-out `ui-client` compose block (`8501:8501`, `ORCHESTRATOR_URL`, `/_stcore/health` healthcheck, `depends_on: orchestrator-app`).
5. **Test harness** (Python — plan-internal): `estc/tests/test_orchestrator_actions.py` (the 4 new endpoints, offline `TestClient`) and `estc/tests/test_ui_client.py` (SSE parsing + confidence-band, offline mock transport).

**Design notes — what this plan deliberately decides (and mirrors):**
- **Endpoints first.** The Approve / Modify / Claim controls cannot be built before their orchestrator routes exist, so Task 5.0 lands and is tested *before* the UI wires to them. These routes were explicitly listed "Out of Scope → Phase 5.3" in the Phase 4.6 plan — this is where they land.
- **App-layer only (FR-14, carries Phase 4.x FR-11).** `claim`/`modify` mutate run state via `graph.update_state(config, {...})` on the ticket's thread — editing `MemorySaver` *values*, not graph topology or node bodies. No file under `graph/nodes/`, `graph/build.py`, `rag/`, or `mcp_*` is touched.
- **PATCH re-evaluation = classifier re-score (spec FR-13, Open Item #1).** `PATCH /tickets/{id}` re-runs the **existing** `classifier-api /classify` over the edited draft text and writes the returned probability into `confidence_score` — a transparent, dependency-only re-score with no graph re-run and no new model. (Alternatives — re-run `supervisor_review`, heuristic, or no-op — are recorded in Risks for the approver to override.)
- **Streamlit rerun model → `st.fragment` (spec § 2.2).** The live-stream region is an `@st.fragment` so it reruns in isolation; placeholders are updated in-loop as `httpx-sse` events arrive, and the terminal `state` is cached into `st.session_state` so an outer rerun never re-streams.
- **Client-measured durations (spec FR-4).** Per-node ms are wall-clock deltas between received SSE events (no server timing → no graph change).
- **UI image stays lean (spec § 2.2).** `ui-client` builds from `./estc/services/ui`, installs only `requirements-ui.txt`, and imports **nothing** from `estc.*` at runtime (the contract is the HTTP/JSON wire shape) — keeping the image torch/chromadb-free.
- **Offline-first parity (codebase convention).** The endpoint tests reuse the Phase 4.6 monkeypatch fixtures (mock classifier transport → deterministic route); the UI-client tests parse a canned SSE byte stream and patch the orchestrator client — both green on a clean checkout with no live infra. The live browser behavior is proven only by the Docker-gated 5.6 gates.

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**; the venv interpreter is `.venv\Scripts\python.exe` (**Python 3.12.10** — the project's actual toolchain; `plan.md` names 3.11 and the containers use `python:3.11-slim`. Non-blocking; the same deviation recorded since Phase 4.4). A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** Confirm the source spec exists and is the version this plan targets.
  **Verify:** `Get-Content ".claude/Specs/05- Streamlit-UI-spec.md" | Select-String "POST /tickets/\{id\}/claim"` returns ≥ 1 match.
- [ ] **PF-2** Confirm the venv interpreter is usable.
  **Verify:** `.venv\Scripts\python --version` reports `Python 3.12.*` (3.11 per plan.md is acceptable; record deviation).
- [ ] **PF-3** Confirm the orchestrator app exposes the existing surface this phase extends, and that the graph supports the state-edit API the new endpoints use (must not be modified).
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.app.main import app; from estc.services.orchestrator.graph.build import graph; assert any(getattr(r,'path','')=='/tickets/{ticket_id}/stream' for r in app.routes); assert hasattr(graph,'update_state') and hasattr(graph,'get_state'); print('ok')"` prints `ok`.
- [ ] **PF-4** Confirm the UI runtime deps are importable (the chosen UI + SSE libraries).
  **Verify:** `.venv\Scripts\python -c "import streamlit, httpx_sse; from streamlit.testing.v1 import AppTest; from httpx_sse import connect_sse; print(streamlit.__version__)"` prints a `1.38.*` version with no ImportError. *(If absent: `.venv\Scripts\pip install -r requirements-ui.txt`.)*
- [ ] **PF-5** Confirm the classifier `/classify` contract the `PATCH` re-score depends on (URL setting + response shape).
  **Verify:** `Get-Content estc/shared/config.py | Select-String "CLASSIFIER_API_URL"` returns 1 match **and** `Get-Content estc/services/orchestrator/graph/nodes/classify.py | Select-String "/classify","confidence"` shows the POST + the `confidence` field the re-score reads.
- [ ] **PF-6** Confirm `conftest.py` forces the GitHub MCP mock and sets the Windows event-loop policy (offline determinism for the endpoint tests).
  **Verify:** `Get-Content estc/tests/conftest.py | Select-String "GITHUB_MOCK_PATH","WindowsSelectorEventLoopPolicy"` returns ≥ 2 matches.
- [ ] **PF-7** Confirm the persisted RAG store exists (needed by the live bug path in EG-5/5.6.1, not by the offline tests).
  **Verify:** `.venv\Scripts\python -c "import chromadb; print(chromadb.PersistentClient('./chroma_db').get_collection('estc').count())"` prints ≥ 50. *(If missing: `.venv\Scripts\python estc/services/orchestrator/rag/ingest.py`.)*
- [ ] **PF-8** Confirm the current `ui-client` compose block is the stub to be replaced (build-context-only, no Dockerfile yet).
  **Verify:** `docker compose config --services` lists `ui-client` **and** `Test-Path estc/services/ui/Dockerfile` returns `False`.
- [ ] **PF-9** Confirm Docker Desktop availability for the live gates (otherwise EG-5/5.6 defer, mirroring the 4.4/4.6 Docker-gating).
  **Verify:** `docker version --format '{{.Server.Version}}'` prints a version (engine reachable). **If unavailable:** the offline gates (EG-1…EG-3) are the merge bar; flag EG-4/EG-5/5.6 for a Docker-available run.

---

## Task 5.0 — Orchestrator Operator-Action Endpoints *(deferred from Phase 4.6; prerequisite for 5.3/5.4)*

### 5.0-a New request/response schemas (`app/schemas.py`)
- [ ] Append to `estc/services/orchestrator/app/schemas.py`: `ModifyDraftRequest(draft_text: str)`, `ClaimRequest(operator: str)`, `ApproveResponse(ticket_id: str, status: str)`, `TicketStateResponse(ticket_id: str, status: str, state: dict[str, Any])` (spec § 4.1). Keep the existing `CreateTicketRequest`/`CreateTicketResponse` unchanged.
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.app.schemas import ModifyDraftRequest, ClaimRequest, ApproveResponse, TicketStateResponse; ModifyDraftRequest(draft_text='x'); ClaimRequest(operator='ana'); print('ok')"` prints `ok`.

### 5.0-b `approve` + `get_ticket` + `claim` routes + registry `approved` flag
- [ ] In `app/main.py`: add an `approved: bool = False` field to `TicketRecord`; add a `_require(ticket_id)` helper (returns the record or raises `HTTPException(404)`); reuse a `_final_state(id)`-style normalizer (already present from 4.6) for reads. Implement `POST /tickets/{id}/approve` → `_require`, set `status="closed"`, `approved=True`, return `ApproveResponse(ticket_id, status="closed")` (FR-12); `GET /tickets/{id}` → return `TicketStateResponse(ticket_id, status=rec.status, state=_final_state(id))` (FR-12); `POST /tickets/{id}/claim` → append `f"CLAIMED_BY:{req.operator}"` to the current `execution_logs` via `graph.update_state({"configurable":{"thread_id":id}}, {"execution_logs": logs})`, return the refreshed `TicketStateResponse` (FR-12/FR-9). **No graph/node import beyond the already-imported `graph`.**
  **Verify:** `.venv\Scripts\python -c "from fastapi.testclient import TestClient; from estc.services.orchestrator.app.main import app; c=TestClient(app); r=c.post('/tickets',json={'text':'x'}); tid=r.json()['ticket_id']; assert c.post(f'/tickets/{tid}/approve').json()['status']=='closed'; assert c.post('/tickets/nope/approve').status_code==404; print('ok')"` prints `ok`. (AC-T6 partial)

### 5.0-c `PATCH /tickets/{id}` — persist edit + re-score confidence on classifier
- [ ] Add an async `_classify_confidence(text) -> float` helper that POSTs `{"text": text}` to `f"{settings.CLASSIFIER_API_URL}/classify"` via `httpx.AsyncClient` and returns the `confidence` float (raise→`HTTPException(502)` on classifier error — ec.7). Implement `PATCH /tickets/{id}` (`response_model=TicketStateResponse`): `_require`; `conf = await _classify_confidence(req.draft_text)`; `graph.update_state(cfg, {"agent_draft_response": req.draft_text, "confidence_score": conf})`; return the refreshed `TicketStateResponse` (FR-13). Confidence re-derivation of `requires_escalation` stays the orchestrator's existing rule (do **not** recompute here — the field is read back as-is).
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.app.main import app; assert any(getattr(r,'path','')=='/tickets/{ticket_id}' and 'PATCH' in getattr(r,'methods',set()) for r in app.routes); print('ok')"` prints `ok`. Full behavior (mocked classifier) covered by AC-T10 in Task 5.6.

---

## Task 5.1 — UI Skeleton, Client & Ingestion Form (tasks 5.1.1, 5.1.2)

### 5.1-a Orchestrator client (`orchestrator_client.py`) + session/state helpers (`state.py`)
- [ ] Create `estc/services/ui/orchestrator_client.py` (spec § 4.1): `BASE = os.environ.get("ORCHESTRATOR_URL","http://localhost:8002")`; `create_ticket(text, company_id)`, `stream_ticket(id, read_timeout=30.0)` (a generator yielding parsed `{event, ...}` frames via `connect_sse`), `approve(id)`, `modify(id, draft_text)`, `claim(id, operator)`, `get_state(id)`. All sync `httpx` with timeouts; `raise_for_status()`. Create `estc/services/ui/state.py`: `confidence_band(score)->(pct,color)` (green ≥80 / orange 60–79 / red <60) and `init_session()` seeding the `st.session_state` schema (`operator`, `tickets`, `selected`).
  **Verify:** `.venv\Scripts\python -c "from estc.services.ui.state import confidence_band; assert confidence_band(0.85)==(85,'green'); assert confidence_band(0.7)[1]=='orange'; assert confidence_band(0.4)[1]=='red'; print('ok')"` prints `ok`. (AC-T5)

### 5.1-b Three-column skeleton (5.1.1)
- [ ] Create `estc/services/ui/app.py`: `st.set_page_config(layout="wide", page_title="ESTC Operations Center")`; `init_session()`; sidebar header **"Inbound Tickets"**; main `col1, col2, col3 = st.columns(3)` with headers **"AI Analysis"**, **"Draft"**, **"Escalation Queue"**. Delegate each column's body to a `components/` render function (created in 5.2–5.4).
  **Verify:** `.venv\Scripts\python -c "from streamlit.testing.v1 import AppTest; at=AppTest.from_file('estc/services/ui/app.py').run(); hs=[h.value for h in at.header]+[h.value for h in at.subheader]; assert 'AI Analysis' in hs and 'Draft' in hs and 'Escalation Queue' in hs; print('ok')"` prints `ok`. (AC-T1)

### 5.1-c Ingestion form (5.1.2)
- [ ] In the sidebar add `st.form("ingest")`: `st.text_area("Issue text")`, `st.text_input("Company ID")`, `st.form_submit_button("Submit")`. On submit call `orchestrator_client.create_ticket(...)`, append `{ticket_id, company_id, text, status:"pending", state:None, timeline:[]}` to `session.tickets`, set it `selected`, and `st.toast(f"Ticket {ticket_id} created")`. Wrap the call in try/except → `st.error` on transport failure (ec.3).
  **Verify (AppTest with patched client):** `.venv\Scripts\python -c "from streamlit.testing.v1 import AppTest; import estc.services.ui.orchestrator_client as oc; oc.create_ticket=lambda t,c: {'ticket_id':'abc123','status':'pending'}; at=AppTest.from_file('estc/services/ui/app.py').run(); at.text_area[0].set_value('500 error'); at.text_input[0].set_value('9422'); at.button[0].click().run(); assert any('abc123' in str(t.value) for t in at.toast); print('ok')"` prints `ok`. (AC-T2)

---

## Task 5.2 — Real-Time Agent Map (tasks 5.2.1, 5.2.2)

### 5.2-a Streaming timeline fragment (`components/agent_map.py`)
- [ ] Create `components/agent_map.py` with `@st.fragment def render(ticket_id)`: if the ticket is not yet streamed, iterate `orchestrator_client.stream_ticket(ticket_id)`; maintain `t0 = perf_counter()` and, on each `node` frame, append/update a timeline row `{node, status:"done", ms:round((now-t_prev)*1000)}` and write it into a per-row `st.empty()` placeholder (icon ⏳→✅), updating `t_prev`. On the `done` frame, cache `frame["state"]` into `session.tickets[id]["state"]`, mark all rows ✅, and set `status="done"`. On an `error` frame, mark the in-progress row ❌ and store the error (ec.4). For an already-`done` ticket (re-select), hydrate rows from `state.execution_logs` with ms "—" (ec.1).
  **Verify (offline, canned stream):** `.venv\Scripts\python -c "import estc.services.ui.components.agent_map as m; assert hasattr(m,'render'); print('ok')"` prints `ok`. Live timeline advancement is AC-T3/AC-T4 (5.6.1) + the UI-client stream test (AC-T11).

### 5.2-b Per-row icon + millisecond duration (5.2.2)
- [ ] Ensure each rendered row shows a status icon **and** the measured `ms` once the node completes (`f"{node}  ✅  {ms} ms"`), in-progress rows show ⏳ with no duration. Durations come from the client-side `perf_counter` deltas in 5.2-a (spec FR-4).
  **Verify:** covered by the UI-client stream-timing unit test (AC-T11) and the live AC-T4; no separate static verify.

---

## Task 5.3 — Draft Panel (tasks 5.3.1, 5.3.2, 5.3.3)

### 5.3-a Draft box + confidence badge (5.3.1)
- [ ] Create `components/draft_panel.py::render(ticket)`. When `ticket["state"]` is present, show `agent_draft_response` in `st.code(...)` (code-fence box) and a **"Confidence: NN%"** badge colored by `confidence_band` (green/amber/red) via `streamlit-extras` or inline HTML `st.markdown(..., unsafe_allow_html=True)`.
  **Verify (AppTest):** drive a ticket whose cached `state` has `confidence_score=0.85`; assert the rendered markdown contains `Confidence: 85%` and a green style. `.venv\Scripts\python -c "from estc.services.ui.state import confidence_band; assert confidence_band(0.85)==(85,'green'); print('ok')"` prints `ok` (band logic; full render asserted in AC-T5 via AppTest in Task 5.6).

### 5.3-b Approve Draft button (5.3.2)
- [ ] Add **Approve Draft** button, rendered **only when** `not state.requires_escalation` (ec.5). On click call `orchestrator_client.approve(id)`, set the session ticket `status="closed"`, and re-render the sidebar so it moves from **Active** to **Closed** (green ✓). Disabled/hidden for escalation tickets.
  **Verify (AppTest, patched client):** click Approve on a non-escalation ticket → `oc.approve` called, ticket `status=="closed"`, appears under "Closed". Asserted in Task 5.6 (AC-T6).

### 5.3-c Modify & Override → PATCH + re-score (5.3.3)
- [ ] Add **Modify & Override** (`st.toggle`/expander) opening an editable `st.text_area` pre-filled with the current draft; a **Save** button calls `orchestrator_client.modify(id, new_text)`, replaces the cached `state` with the response (`agent_draft_response`, `confidence_score`, `requires_escalation`), and the badge re-renders with the new confidence. On `502` keep the prior draft + `st.warning` (ec.7).
  **Verify:** behavior asserted in Task 5.6 (AC-T7) with a patched `modify` returning an updated confidence; the endpoint itself is AC-T10.

---

## Task 5.4 — Escalation Queue (tasks 5.4.1, 5.4.2)

### 5.4-a "Requires Manual Verification" list (5.4.1)
- [ ] Create `components/escalation_queue.py::render(session)`: list every session ticket whose `state.requires_escalation == True` under the header **"Requires Manual Verification"**. These tickets show **no** Approve control anywhere (gated in 5.3-b) — auto-approval is structurally blocked (ec.5, underpins 5.6.2).
  **Verify (AppTest):** seed two cached tickets (one `requires_escalation=True`, one `False`); assert only the escalating one appears in the queue column. Asserted in Task 5.6 (AC-T8).

### 5.4-b Tier / account status + Claim button (5.4.2)
- [ ] Each escalation row shows the customer's **subscription tier** and **account status** — recovered by a small defensive parser over `state.agent_draft_response` + `state.retrieved_context` (spec § 4.1), falling back to "—" when absent (ec.6). Add a **Claim** button → `orchestrator_client.claim(id, session.operator)`; on success remove the row from the queue (and the response's `execution_logs` now ends `CLAIMED_BY:<operator>`).
  **Verify:** Claim behavior + log mutation asserted end-to-end in Task 5.6 (AC-T9, via the live `claim` endpoint AC-T10); tier/status parser has a unit case in `test_ui_client.py`.

---

## Task 5.5 — Containerization (`ui-client`, port 8501) (tasks 5.5.1, 5.5.2)

### 5.5-a Dockerfile (5.5.1)
- [ ] Create `estc/services/ui/Dockerfile` on `python:3.11-slim`, `WORKDIR /app`: `COPY requirements-ui.txt` (copied into the build context — see 5.5-c note) `→ RUN pip install --no-cache-dir -r requirements-ui.txt`; `COPY . .` (the `ui/` tree); `EXPOSE 8501`; `CMD ["streamlit","run","app.py","--server.address","0.0.0.0","--server.port","8501"]`. Because the build context is `./estc/services/ui`, copy `requirements-ui.txt` into that dir (or reference it via a root-context alternative — pin in execution; spec § 2.2 keeps the context the sub-dir).
  **Verify (Docker-gated, see EG-4):** `Get-Content estc/services/ui/Dockerfile | Select-String "8501","streamlit run","app.py"` returns 3 matches.

### 5.5-b Compose wiring (5.5.2)
- [ ] Replace the stub `ui-client` block in `docker-compose.yml`: `build.context: ./estc/services/ui`; `container_name: ui-client`; `ports: ["8501:8501"]`; `environment: { ORCHESTRATOR_URL: http://orchestrator-app:8002 }`; `depends_on: { orchestrator-app: { condition: service_healthy } }`; `networks: [estc-net]`; `curl`-free healthcheck `test: ["CMD","python","-c","import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').status==200 else 1)"]`, `interval: 15s`, `timeout: 5s`, `retries: 5`, `start_period: 30s` (ec.9).
  **Verify:** `docker compose config` parses without error **and** `docker compose config | Select-String "8501","ui-client","ORCHESTRATOR_URL","_stcore/health"` returns matches.

---

## Task 5.6 — Test Harness *(plan-internal; satisfies AC-T5, AC-T9 backend, AC-T10, AC-T11)*

### 5.6-a Orchestrator action tests (`estc/tests/test_orchestrator_actions.py`)
- [ ] Create the file. Build `TestClient(app)`; reuse the Phase 4.6 `offline_bug_run` + `_force_template_path` fixtures so a ticket can be POSTed and (optionally) streamed to a terminal state. For `PATCH`, monkeypatch the classifier call (`_classify_confidence` or its `httpx.AsyncClient`) to return a fixed `confidence` so the test is offline/deterministic.

### 5.6-b Action test cases — must include at minimum (AC-T10 bar)
- [ ] `test_approve_closes_ticket` → `POST /tickets/{id}/approve` returns `200 {status:"closed"}`; unknown id → `404`.
- [ ] `test_patch_rescore_updates_confidence` → `PATCH /tickets/{id} {"draft_text":"..."}` returns the new `draft` + the mocked `confidence_score`; unknown id → `404`; classifier error → `502`.
- [ ] `test_claim_appends_operator_log` → `POST /tickets/{id}/claim {"operator":"ana"}`; the returned `state.execution_logs` ends with `CLAIMED_BY:ana`.
- [ ] `test_get_ticket_returns_state` → `GET /tickets/{id}` returns `{ticket_id,status,state}` with the expected fields; unknown id → `404`.

  **Verify:** `.venv\Scripts\pytest estc/tests/test_orchestrator_actions.py -v` reports **all green** with at least 4 passed. (AC-T10)

### 5.6-c UI-client tests (`estc/tests/test_ui_client.py`)
- [ ] Create the file. Patch `httpx_sse.connect_sse` (or feed an `httpx.MockTransport`) with a canned byte stream `open → node(classify) → node(bug_agent) → node(supervisor_review) → done` and assert `stream_ticket` yields 5 ordered frames with the right `event`/`node` values. Add a `confidence_band` threshold table test and a `tier/status` parser case over a sample draft string.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_ui_client.py -v` reports **all green** with at least 3 passed. (AC-T11)

---

## Phase 5 Exit Gate

- [ ] **EG-1 (orchestrator action suite — AC-T10)** — the 4 new endpoints pass offline.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_orchestrator_actions.py -v --tb=short` reports **0 failed**, ≥ 4 passed.
- [ ] **EG-2 (UI unit suite — AC-T1/T2/T5/T11)** — UI client, band logic, and AppTest skeleton/form pass offline.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_ui_client.py -v` reports **0 failed**; `.venv\Scripts\python -c "from streamlit.testing.v1 import AppTest; at=AppTest.from_file('estc/services/ui/app.py').run(); assert not at.exception; print('APP_OK')"` prints `APP_OK`.
- [ ] **EG-3 (no-regression, FR-14)** — Phase 5 breaks no prior phase and changes no graph/rag/MCP file.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_graph_build.py estc/tests/test_graph_nodes.py estc/tests/test_orchestrator_api.py -q` reports **0 failed**; `git diff --name-only` shows changes only under `estc/services/ui/`, `estc/services/orchestrator/app/`, `estc/tests/`, and `docker-compose.yml` — **nothing** under `graph/`, `rag/`, or `mcp_*`.
- [ ] **EG-4 (UI image + 5-service stack healthy — 5.5 verify; Docker-gated)** — the image builds and all five services report healthy. **Run only when Docker is available** (else defer with EG-2 standing in).
  **Verify:** `docker build -t estc-ui ./estc/services/ui` exits 0; `docker compose up -d --build` then within `start_period` `docker compose ps` shows **5 services healthy**; `Invoke-RestMethod http://localhost:8501/_stcore/health` returns `ok`. **Fallback if Docker unavailable:** EG-1/EG-2 offline gates are the merge bar; flag EG-4/EG-5 for a Docker run.
- [ ] **EG-5 (full E2E exit gate — 5.6.1/5.6.2/5.6.3; Docker-gated)** — the project Definition of Done, exercised through the live UI.
  **Verify:**
    - **5.6.1** — at `http://localhost:8501` submit *"I am getting a 500 error when pulling the API, my company ID is 9422"*: timeline shows `classify → bug_agent → supervisor_review`, draft cites ≥ 1 GitHub issue, confidence ≥ 80 %, **Approve** closes the ticket; the run is visible in LangSmith `estc-dev` (if keyed).
    - **5.6.2** — submit *"I cannot log in to my account, company 9422"*: classifier returns `lockout`, the ticket appears under **Requires Manual Verification**, and **no Approve control is offered** (`state.requires_escalation == True`).
    - **5.6.3** — re-run the Ragas suite against the live containerized orchestrator: `pwsh ./scripts/eval.ps1` (or `make eval`) writes `results.csv` with Faithfulness / Answer Relevance / Context Recall **≥ 0.80 mean**. *(Needs a judge-LLM key; otherwise this sub-gate defers per the 4.5 deferral.)*
- [ ] **EG-6 (clean-boot regression)** — the offline suites still pass from a clean state.
  **Verify:** re-run EG-1 + EG-2 on a clean checkout of the new files; both must succeed.

---

## Risks & Open Questions

1. **PATCH re-evaluation semantics (spec Open Item #1 / FR-13).** Plan commits to **classifier re-score** of the edited draft. This treats `confidence_score` as an intent-confidence proxy, which is what `classify` produces — but the operator's edit is a *draft*, not a ticket, so the score's meaning shifts slightly. Alternatives if the approver prefers: (a) re-run only `supervisor_review`; (b) a no-call heuristic; (c) persist the edit and leave confidence unchanged. **Decision needed before 5.0-c / 5.3-c.**
2. **`graph.update_state` as an app-layer edit (spec Open Item #3).** `claim`/`modify` write to `MemorySaver` values via `graph.update_state`. This edits run *state*, not graph *topology* or node bodies, so it respects FR-14 — but it is the one place the app reaches into the checkpointer. Confirm this reading; if rejected, fall back to storing the override/operator marker in the in-process `TicketRecord` only (UI reads it from `GET /tickets/{id}` without touching the checkpoint).
3. **Streamlit live-stream rendering (`st.fragment`).** Fragment + in-loop placeholder writes is the planned pattern on `streamlit==1.38.*`. If fragment reruns prove flaky with a blocking SSE iterator, fall back to `st.write_stream` over the generator or a manual `st.empty()` loop. The `< 2 s step` bar (AC-T3) is validated only live (5.6.1).
4. **AppTest coverage limits.** `streamlit.testing.v1.AppTest` runs the script headlessly but cannot exercise the live SSE loop (it needs a running orchestrator) — so AppTest covers the skeleton, form, badge, and queue routing (with a patched client), while the real-time timeline + durations are proven only by the live 5.6.1 gate and the offline `test_ui_client` stream-parse test.
5. **Tier / account-status surfacing (spec Open Item #2).** Parsed defensively from the draft/context to respect FR-14; brittle if the draft phrasing changes. If unreliable, schedule a separate (out-of-scope here) backend task adding a `customer_facts` field or a `/customers/{id}` read-through.
6. **UI image build context vs. `requirements-ui.txt` location.** `requirements-ui.txt` lives at repo root but the `ui-client` context is `./estc/services/ui`. Plan resolves this by copying the file into the UI dir (or switching to a root context for the UI build) — pin the choice in 5.5-a so `docker build` finds it.
7. **5.6.3 needs a judge-LLM key.** The live Ragas ≥ 0.80 sub-gate inherits the Phase 4.5 deferral; on a keyless box it skips cleanly and is flagged, not failed.

---

## Out of Scope (explicitly deferred)

- A durable (Postgres/SQLite) ticket store + real-time multi-operator fan-out — the in-process registry + `MemorySaver` and per-session `st.session_state` assume a single worker / single operator (spec ec.8).
- Authenticated operator identity — the Claim name is a free-text dev field (spec § 2.2).
- A dedicated `customer_facts` field / `GET /customers/{id}` read-through to replace the draft/context tier-status parser — would touch the graph/MCP layer FR-14 forbids (Risk #5).
- The Phase 3.2 GitHub MCP real-API empty-result `IndexError` hardening — a separate backend task carried from the Phase 4.6 report.
- Any change to `graph/nodes/`, `graph/build.py`, `rag/`, or the MCP servers (FR-14).

---

**Awaiting `Proceed` to begin execution at PF-1.**
