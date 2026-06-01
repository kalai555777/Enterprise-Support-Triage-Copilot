# Implementation Report — Phases 4.5 & 4.6

**Scope:** Observability & Evaluation (Phase 4.5) and the FastAPI/SSE Wrapper for the Orchestrator (Phase 4.6).
**Audience:** anyone picking up the project who needs to know *what was built, in what order, what broke, and how it was fixed.*

Both phases followed the repo's **spec-driven development (SDD)** workflow:

1. `build-sdd-spec` → an architectural spec under `.claude/Specs/NN-*.md`
2. `build-sdd-plan` → an ordered execution plan under `.claude/plans/NN-*.md` (each step ends in a **Verify**)
3. Implement step by step, running each Verify; record deviations in the plan's status line.

> Toolchain note: the dev venv is **Python 3.12.10**; the containers use `python:3.11-slim` (the `docs/plan.md` target). This 3.11-vs-3.12 split is a known, non-blocking deviation recorded since Phase 4.4.

---

## Phase 4.5 — Observability & Evaluation

**Roadmap tasks:** 4.5.1 (LangSmith tracing), 4.5.2 (Ragas eval harness), 4.5.3 (one-command shortcut).
**Commit:** `cc5985f` ("added observability_evaluation"), merged via PR #7 (`bf06c2b`).
**Goal:** make the Phase 4.4 triage graph **observable** (LangSmith traces) and **measurable** (Ragas: Faithfulness, Answer Relevance, Context Recall), then expose a single eval command — per `design.md` §4's Evaluation-Driven-Development framework.

### Files added / changed (in execution order)

Each row states **what the file is** and, explicitly, **why it had to be added** (the reason it exists).

| # | File | New/Mod | What it is | Why it was added (reason) |
|---|------|---------|-----------|---------------------------|
| 1 | `.claude/Specs/07-Observability-Evaluation-spec.md` | new | The phase spec (FRs, contracts, acceptance criteria). | The SDD workflow requires a signed-off spec **before** any code, so the observability/eval contract (what "done" means) is fixed up front. |
| 2 | `.claude/plans/07-Observability-Evaluation-plan.md` | new | Ordered execution plan with Verify steps. | To break the spec into verifiable, ordered steps and capture deviations — nothing is implemented until the plan is approved. |
| 3 | `estc/tests/eval/_ragas_compat.py` | new | **Dependency-repair shim** (task 4.5.0). | Because `import ragas` was **completely broken** (see error below) — without this shim *no* eval code could even import. It is a hard precondition for files #7–#11. |
| 4 | `estc/shared/config.py` | mod | Added `LANGSMITH_TRACING: bool = False` setting. | Tracing has to be toggleable from the environment without touching code; default **off** so every existing test stays green on a keyless box. |
| 5 | `.env.example` | mod | Documented `LANGSMITH_TRACING=true`. | So operators know the new toggle exists and how to enable tracing — the env file is the project's single source of truth for config keys. |
| 6 | `estc/services/orchestrator/graph/observability.py` | new | `configure_tracing()` — enables LangSmith tracing iff `LANGSMITH_TRACING` **and** `LANGSMITH_API_KEY` are set; otherwise forces it off. Idempotent, never raises. | The 4.5.1 deliverable: one bootstrap function the graph, the eval harness, **and the Phase 4.6 FastAPI app** all call at startup, instead of scattering tracing env logic across callers. |
| 7 | `estc/tests/eval/fixtures/eval_tickets.jsonl` | new | 20-row gold fixture (`question`, `ground_truth`, `company_id`) across all four intents. | Ragas scores answers against **ground truth**; there was no labelled dataset, so one had to be authored (20 rows / ≥4 per intent) from the knowledge base to measure Faithfulness/Relevance/Recall. |
| 8 | `estc/tests/eval/ragas_eval.py` | new | Runs `run_ticket` over the 20 fixtures, scores the three metrics, writes `results.csv`; exits 0 (skips) when no judge LLM key is present. | The 4.5.2 deliverable — the actual evaluation harness. Skips cleanly to preserve the clean-checkout invariant (no key ⇒ no crash). |
| 9 | `scripts/eval.ps1` | new | Canonical Windows one-command eval entry. | 4.5.3 asks for a one-command shortcut; PowerShell is the project's shell, so this is the canonical entry point for running the eval. |
| 10 | `Makefile` | new | `make eval` target running the same command. | 4.5.3 parity for `make`-based workflows/CI, so the eval is reproducible the same way on any platform that has `make`. |
| 11 | `estc/tests/test_observability.py` | new | Offline wiring tests (tracing contract, fixture integrity, dataset-assembly shape, graceful skip); live paths `skipif`-guarded. | To **prove the wiring works without any keys** (CI-safe), satisfying the spec's acceptance bars while the live trace/score assertions defer to a keyed run. |
| 12 | `docs/plan.md` | mod | Ticked the 4.5 boxes with deviation notes. | The roadmap is the project's checklist of record; it has to reflect that 4.5 is done and which parts were deferred. |

### Key design decision: offline-first is preserved

Phase 4.5 is the first phase that *can* reach external services (LangSmith, a judge LLM), but the **clean-checkout invariant is kept**: `configure_tracing()` no-ops without a key, and `ragas_eval.py` skips (exit 0, no CSV) when deps/keys are absent. Live verifies are `skipif`-gated — the same pattern Phase 4.4 used for its live e2e test.

**Error — `import ragas` crashes the whole eval stack.** The installed `ragas 0.4.3` imports `langchain_community.chat_models.vertexai` (`ChatVertexAI`), a module **removed in `langchain-community 0.4.2`** — the exact version the RAG pipeline (`rag/ingest.py` → `HuggingFaceBgeEmbeddings`) depends on. So `import ragas` failed with `ModuleNotFoundError: ...vertexai`, blocking every eval task. Downgrading `langchain-community` to restore the module would risk breaking the working RAG stack.
**Overcome by:** an inert import shim (`_ragas_compat.py`) that, *before* `import ragas`, registers a stub in `sys.modules`: `sys.modules["langchain_community.chat_models.vertexai"] = <ModuleType with ChatVertexAI = object>`. `ChatVertexAI` is never instantiated (the Ragas judge comes from the Anthropic→OpenAI ladder in `graph/llm.py`), so the stub only needs to satisfy the import; `ensure_ragas_importable()` is called at the top of `ragas_eval.py`. Documented fallback: pin a compatible `ragas`/`langchain-community` pair.

### What was deferred (by design)

- **Live LangSmith child-run assertion** (≥6 child runs per ticket) and the **live Ragas ≥0.80 mean** require a judge LLM key + the orchestrator services running. On the dev box these `skip` cleanly; they run against a keyed/containerized orchestrator at **Phase 5.6**.

### Phase 4.5 verification (recorded in the plan)

- EG-1 ragas importable via shim ✓
- EG-2 observability suite green (7 passed / 1 skipped) ✓
- EG-3 no-regression, no graph mutation (25 passed / 1 skipped) ✓
- EG-4 `eval.ps1` / `make eval` exit 0 ✓
- EG-5 (live ≥0.80) — **deferred to 5.6**

---

## Phase 4.6 — FastAPI Wrapper for Orchestrator

**Roadmap tasks:** 4.6.1 (`POST /tickets` + `GET /tickets/{id}/stream` SSE), 4.6.2 (Dockerfile + compose, port 8002). Also drove the **4.7.1** live exit-gate run.
**Commit:** `e50912f` ("Add FastAPI/SSE wrapper for orchestrator (Phase 4.6)"), branch `feature/FastAPI_Wrapper`.
**Goal:** expose the Phase 4.4 LangGraph engine over HTTP, streaming one Server-Sent Event per node transition, and containerize it as the `orchestrator-app` service on port 8002.

### The core idea

Phase 4.4 already shipped `astream_ticket()` (an async generator yielding `(node_name, update)` per node transition) and `run_ticket()`. Phase 4.6 is a **thin HTTP/SSE skin** over `astream_ticket` — it reuses that generator verbatim (no re-implementation of `graph.astream`), so "what the SSE client sees" equals "what `run_ticket` sees." The graph runs **lazily**: `POST /tickets` only registers the ticket and returns an id; opening `GET /tickets/{id}/stream` is what drives the graph.

### Files added / changed (in execution order)

Each row states **what the file is** and, explicitly, **why it had to be added** (the reason it exists).

| # | File | New/Mod | What it is | Why it was added (reason) |
|---|------|---------|-----------|---------------------------|
| 1 | `.claude/Specs/08-fastapi-wrapper-spec.md` | new | The phase spec (lazy-on-stream model, SSE frame contract, FRs, edge cases, acceptance criteria). | SDD requires the contract before code: it fixes the HTTP/SSE shape and the key decisions (lazy-on-stream, reuse `astream_ticket`, optional `company_id`) up front. |
| 2 | `.claude/plans/08-fastapi-wrapper-plan.md` | new | Ordered execution plan (9 pre-flight checks, 3 tasks, 5 exit gates). | To turn the spec into verifiable ordered steps and record the deviations/fixes that came up during execution. |
| 3 | `estc/services/orchestrator/app/schemas.py` | new | `CreateTicketRequest(text, company_id?)`, `CreateTicketResponse(ticket_id, status)`. | FastAPI validates requests/responses from Pydantic models; kept in their own module so the request contract is separate from app wiring and reusable (e.g. by the UI later). |
| 4 | `estc/services/orchestrator/app/main.py` | new | FastAPI app: `GET /healthz`, `POST /tickets`, `GET /tickets/{id}/stream` (SSE); `configure_tracing()` at startup. | **The 4.6.1 deliverable itself** — there was no HTTP boundary on the orchestrator; this is the service that exposes the graph and streams per-node events to clients (the Phase 5 UI). |
| 5 | `estc/tests/test_orchestrator_api.py` | new | 6 offline tests via FastAPI `TestClient`, reusing the Phase 4.4 monkeypatch fixtures. | To prove the endpoints and the ≥4-event SSE stream **deterministically and offline** (no classifier/Postgres/Chroma), keeping the suite green on a clean checkout. |
| 6 | `estc/services/orchestrator/requirements.txt` | new | Orchestrator-app runtime deps (web + LangGraph + RAG + MCP), exact-pinned. | The image needs its **own** dependency manifest: the app imports across `estc/*` and pulls the heavy ML stack (torch/chromadb). Pinned exactly to defeat the resolver explosion (Error #2). Kept separate from the dev-only root requirements. |
| 7 | `estc/services/orchestrator/Dockerfile` | new | `python:3.11-slim`, **repo-root build context**, `PYTHONPATH=/app`, `CMD uvicorn ... :8002`. | The 4.6.2 deliverable — there was no orchestrator image. Repo-root context is required because the app imports the whole `estc/` tree (shared, graph, rag, in-process MCP tools). |
| 8 | `docker-compose.yml` | mod | Fleshed-out `orchestrator-app` block (8002, env, chroma mount, healthcheck, `depends_on`); also fixed the `classifier-api` healthcheck. | 4.6.2 wiring: the stub block had only a build context. Needed port mapping, runtime env, the chroma mount, a working healthcheck, and dependency ordering so the service runs in the compose network. (Also fixed Errors #4/#5/#6 here.) |
| 9 | `requirements-orchestrator.txt` | mod | Pinned `starlette<0.47` + `sse-starlette<3`. | To stop the dev venv from re-drifting into the broken `starlette 1.1.0` / `sse-starlette 3.x` combo that broke every FastAPI app (Error #1) — making the fix durable, not just a one-off local install. |

### The SSE contract

Each frame is `data: <json>`:
- `open` — `{ticket_id, status:"running"}`
- `node` (one per transition) — `{event:"node", node, ticket_id, update}` where `update` is that node's partial state delta
- `done` — `{event:"done", ticket_id, state:<full AgentState>}` (carries the draft, confidence, escalation flag, logs)
- `error` (only on failure) — `{event:"error", ticket_id, error}`

A normal run = `open` + 3 nodes (`classify`, one worker, `supervisor_review`) + `done` = **5 frames** (≥4, satisfying the 4.6.1 verify).

### Problems encountered (error → step that overcame it)

This phase hit six distinct problems during implementation and the live run. Each is written as the **Error** followed immediately by the **step that overcame it**, in the order they were encountered.

**Error #1 — every FastAPI app was broken (`starlette` version skew).** Instantiating `FastAPI(...)` raised `TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'` — and this hit **not just the new app but the existing classifier app too** (the whole repo's FastAPI surface was dead). Cause: the venv had `starlette 1.1.0`, pulled in by a previously-installed `sse-starlette 3.4.4` (needs `starlette>=1.0`), but the pinned `fastapi 0.115` requires `starlette<0.47`.
**Overcome by:** `pip install "starlette>=0.40,<0.47" "sse-starlette>=2.4,<3"` → resolved to `starlette 0.46.2` + `sse-starlette 2.4.1`; pinned that constraint in **both** `requirements-orchestrator.txt` and the service `requirements.txt` so it can't recur. (Side benefit: the classifier app works again.)

**Error #2 — Docker image build failed (pip "resolution-too-deep").** `docker compose build orchestrator-app` failed after ~16 min with pip backtracking endlessly on `huggingface-hub` versions → `ResolutionTooDeep`. (A first background build looked like it "succeeded" only because its output was piped through `tail`, masking the non-zero exit.) Cause: loose `>=` ranges (`langgraph>=0.2`, `chromadb>=0.5`, `sentence-transformers>=3.1`, …) combined with the hard `fastapi==0.115.*`/`httpx`/`pydantic` pins gave pip a combinatorial version space.
**Overcome by:** re-pinning `requirements.txt` to the **exact versions already resolved and working in the dev venv** (`chromadb==1.5.9`, `langchain==1.3.2`, `langgraph==1.2.2`, `sentence-transformers==5.5.1`, `huggingface-hub==0.36.2`, `torch==2.4.1`, …). Exact pins collapse the resolver search; the rebuild succeeded (image ~9.6 GB due to torch/chromadb).

**Error #3 — second streaming test crashed (`Event bound to a different event loop`).** The first SSE test passed but the second raised `RuntimeError: <asyncio.locks.Event> is bound to a different event loop`. Cause: `sse-starlette` caches a module-global `anyio.Event` (`AppStatus.should_exit_event`) for graceful shutdown; `TestClient` spins a fresh event loop per request, so the event cached on the first test's loop was reused on the second's.
**Overcome by:** an autouse pytest fixture that resets `AppStatus.should_exit_event = None` before each test, so `sse-starlette` lazily recreates it on the current loop (`sse.py:194`).

**Error #4 — container never goes healthy (`curl` absent).** `orchestrator-app`'s `depends_on: classifier-api: service_healthy` would block forever, because the classifier healthcheck used `curl`, which isn't installed in `python:3.11-slim`.
**Overcome by:** switching both healthchecks to a dependency-free urllib probe — `python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:PORT/healthz').status==200 else 1)"` — and giving the orchestrator `start_period: 90s` to absorb the torch/chromadb import warmup.

**Error #5 — RAG retrieval crashed in-container (`attempt to write a readonly database`).** The first live run streamed `classify` then emitted an `error` frame (`list index out of range` at `bug_agent`); reproducing inside the container showed the real cause: `chromadb.errors.InternalError: attempt to write a readonly database`. Cause: `./chroma_db` was mounted `:ro`, but **chromadb 1.5.9 opens the SQLite store in write mode (WAL/journal) even for pure reads** — the `list index out of range` was just the downstream symptom of the failed read.
**Overcome by:** changing the mount from `./chroma_db:/app/chroma_db:ro` to read-write `./chroma_db:/app/chroma_db`. The collection then read fine (54 chunks: 27 billing + 27 technical).

**Error #6 — `bug_agent` still failed (`IndexError`) — real GitHub API instead of mock.** Even with chroma fixed, `bug_agent` raised `list index out of range`, traced into PyGithub's `PaginatedList.__getitem__`. Cause: the host `.env` has a real 40-char `GITHUB_PAT`, which compose interpolated into the container via `${GITHUB_PAT:-}`, so the in-process GitHub tool called the **real** API — and the Phase 3.2 server's real-API path raises `IndexError` on an empty issue search.
**Overcome by:** defaulting `orchestrator-app`'s `GITHUB_PAT` to empty → the tool uses `GITHUB_MOCK_PATH` (deterministic mock mode, matching the test suite and spec AC-B5); the live run then completed. *Out-of-scope follow-up:* the GitHub MCP server's real-API empty-result `IndexError` (`estc/services/mcp_github/server.py:105`) is a latent **Phase 3.2** bug to harden separately — Phase 4.6 must not modify MCP servers (FR-11).

### Phase 4.6 verification

**Offline (the merge bar):**
- EG-1 — orchestrator API suite: **6/6 passed**
- EG-2 — ≥4 SSE `data:` frames: **5 frames**
- EG-3 — no-regression: **29 passed / 1 skipped**; only `docker-compose.yml` modified, no `graph/`/`rag/`/`mcp_*` source change (FR-11)

**Live containerized stack (Docker became available):**
- EG-4 — `docker compose up -d orchestrator-app` → **healthy in ~15s**; `/healthz` returns `{"status":"ok"}` on 8002
- **4.7.1 exit gate** — canonical ticket *"I am getting a 500 error when pulling the API, my company ID is 9422"* streamed:
  - `open` → `node:classify` (intent **bug**, 0.85) → `node:bug_agent` (4 KB chunks retrieved; draft cites GitHub issues **#42, #37**) → `node:supervisor_review` (**AUTO_APPROVED**) → `done`
  - **5 `data:` frames**, node order **classify → bug_agent → supervisor_review**, draft non-empty, **confidence 0.85**, `execution_logs` ends `AUTO_APPROVED` ✓

---

## Current state & what's left

**Done:** Phase 4 is functionally complete. Phase 4.6 is committed (`e50912f`) and pushed to `origin/feature/FastAPI_Wrapper`; the live 4.7.1 gate passed against the running stack (`postgres-db`, `classifier-api`, `orchestrator-app` all healthy).

**Remaining:**
- Open the PR for `feature/FastAPI_Wrapper` (link printed on push).
- **Phase 5 — Streamlit UI** (entirely unbuilt; `estc/services/ui/` does not exist yet). 5.3 needs **new** orchestrator endpoints deferred from 4.6: `POST /tickets/{id}/approve`, `PATCH /tickets/{id}`.
- **Phase 5.6** live runs: LangSmith child-run assertion + Ragas ≥0.80 mean against the containerized orchestrator (deferred from 4.5).
- **Phase 3.2 follow-up:** harden the GitHub MCP server's real-API empty-result `IndexError`.

**Known environment notes for the next engineer:**
- The dev venv's FastAPI stack must stay at `starlette<0.47` / `sse-starlette<3`.
- `./chroma_db` must exist (run `python estc/services/orchestrator/rag/ingest.py`) and be mounted read-write for the live RAG path.
- `orchestrator-app` runs the GitHub tool in mock mode by default; supplying a working PAT will surface the Phase 3.2 bug until it's fixed.
