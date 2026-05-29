# Build Plan: Enterprise Support & Triage Copilot (ESTC)

**Source Documents:** [speculation.md](./speculation.md) · [design.md](./design.md)
**Target Stack:** Python 3.11 · PyTorch · FastAPI · MCP SDK · LangChain · LangGraph · ChromaDB · Streamlit · PostgreSQL · Docker Compose

This checklist is sequential. Do not skip ahead — later phases assume artifacts from earlier phases. Every task ends with a **Verify** line: a concrete command or success criterion. A task is "done" only when its verification passes.

---

## Phase 1: Environment & Secrets Setup

Goal: a reproducible local dev environment, an empty multi-service Docker scaffold, secrets handling, and the PostgreSQL container ready to receive the schema in Phase 3.

### 1.1 Repository Scaffolding
- [ ] **1.1.1** Create top-level folder structure under repo root:
  ```
  estc/
    services/
      classifier-api/
      mcp-postgres/
      mcp-github/
      orchestrator/
      ui/
    shared/
      schemas/
    data/
      training/
      knowledge_base/
    tests/
    infra/
      docker/
      sql/
  ```
  **Verify:** `Get-ChildItem -Recurse -Directory estc | Measure-Object` returns ≥ 11 directories.
- [ ] **1.1.2** Create `.gitignore` covering `.env`, `__pycache__/`, `*.pyc`, `.venv/`, `chroma_db/`, `models/*.bin`, `*.pt`, `.pytest_cache/`, `.mypy_cache/`, `.langsmith/`.
  **Verify:** `git check-ignore .env __pycache__ .venv/` returns each path with no error.
- [ ] **1.1.3** Add `README.md` stub listing the 5 services and how to run `docker compose up`.
  **Verify:** `Get-Content README.md | Select-String "docker compose up"` matches.

### 1.2 Python Toolchain
- [ ] **1.2.1** Create Python 3.11 virtual environment at repo root: `python -m venv .venv`.
  **Verify:** `.venv\Scripts\python --version` reports `Python 3.11.*`.
- [ ] **1.2.2** Create `requirements.txt` with pinned versions for: `fastapi==0.115.*`, `uvicorn[standard]==0.30.*`, `torch==2.4.*`, `transformers==4.44.*`, `datasets==2.20.*`, `scikit-learn==1.5.*`, `pydantic==2.9.*`, `python-dotenv==1.0.*`, `httpx==0.27.*`, `pytest==8.3.*`, `pytest-asyncio==0.24.*`, `ruff==0.6.*`, `black==24.8.*`, `mypy==1.11.*`.
  **Verify:** `.venv\Scripts\pip install -r requirements.txt` exits 0.
- [ ] **1.2.3** Create `requirements-orchestrator.txt` (separate file): `langgraph==0.2.*`, `langchain==0.3.*`, `langchain-openai==0.2.*`, `langchain-anthropic==0.2.*`, `langchain-community==0.3.*`, `chromadb==0.5.*`, `sentence-transformers==3.1.*`, `ragas==0.2.*`, `langsmith==0.1.*`, `fastmcp==2.*`, `psycopg[binary]==3.2.*`, `PyGithub==2.4.*`.
  **Verify:** `.venv\Scripts\pip install -r requirements-orchestrator.txt` exits 0.
- [ ] **1.2.4** Create `requirements-ui.txt`: `streamlit==1.38.*`, `streamlit-extras==0.4.*`, `httpx-sse==0.4.*`.
  **Verify:** `.venv\Scripts\pip install -r requirements-ui.txt` exits 0.
- [ ] **1.2.5** Create `pyproject.toml` with `[tool.ruff]`, `[tool.black]` (line-length 100), and `[tool.mypy]` (strict for `shared/schemas/`).
  **Verify:** `.venv\Scripts\ruff check estc/` runs without configuration error.

### 1.3 Pre-commit Hooks
- [ ] **1.3.1** Install `pre-commit==3.8.*` and create `.pre-commit-config.yaml` running `ruff`, `black --check`, and `mypy estc/shared/`.
  **Verify:** `.venv\Scripts\pre-commit run --all-files` exits 0 on an empty repo.

### 1.4 Secrets & Environment
- [ ] **1.4.1** Create `.env.example` with placeholder keys: `OPENAI_API_KEY=`, `ANTHROPIC_API_KEY=`, `GITHUB_PAT=`, `LANGSMITH_API_KEY=`, `LANGSMITH_PROJECT=estc-dev`, `POSTGRES_USER=estc`, `POSTGRES_PASSWORD=estc_dev_pw`, `POSTGRES_DB=estc`, `POSTGRES_HOST=mcp-postgres`, `POSTGRES_PORT=5432`, `CLASSIFIER_API_URL=http://classifier-api:8001`.
  **Verify:** `Get-Content .env.example | Measure-Object -Line` reports ≥ 11 lines.
- [ ] **1.4.2** Copy `.env.example` to local `.env` and populate real values. Confirm `.env` is git-ignored.
  **Verify:** `git status --short .env` returns empty output.
- [ ] **1.4.3** Add `shared/config.py` exposing a `Settings` Pydantic class that loads from `.env`.
  **Verify:** `.venv\Scripts\python -c "from estc.shared.config import Settings; print(Settings().POSTGRES_DB)"` prints `estc`.

### 1.5 Docker Foundation
- [ ] **1.5.1** Create `docker-compose.yml` at repo root with `version: "3.9"` and a stub `networks: estc-net:` and four service blocks (`classifier-api`, `mcp-postgres`, `orchestrator-app`, `ui-client`) — each pointing to a Dockerfile to be added later.
  **Verify:** `docker compose config` parses without error.
- [ ] **1.5.2** Add `mcp-postgres` service definition using `postgres:16-alpine`, mounting `./infra/sql/init.sql` to `/docker-entrypoint-initdb.d/`, env vars from `.env`, healthcheck `pg_isready -U $$POSTGRES_USER`.
  **Verify:** `docker compose up -d mcp-postgres` followed by `docker compose ps mcp-postgres` shows `healthy` within 30s.
- [ ] **1.5.3** Create `infra/sql/init.sql` with the `enterprise_customers` DDL from `design.md` section 3.
  **Verify:** `docker compose exec mcp-postgres psql -U estc -d estc -c "\d enterprise_customers"` lists all 5 columns.
- [ ] **1.5.4** Create `infra/sql/seed.sql` inserting ≥ 20 synthetic rows covering all three `subscription_tier` values and all three `account_status` values.
  **Verify:** `docker compose exec mcp-postgres psql -U estc -d estc -c "SELECT COUNT(*), COUNT(DISTINCT subscription_tier), COUNT(DISTINCT account_status) FROM enterprise_customers;"` returns counts ≥ 20, 3, 3.

### 1.6 Phase 1 Exit Gate
- [ ] **1.6.1** Run `docker compose down -v` then `docker compose up -d mcp-postgres` and confirm seed data persists across restart by running 1.5.3 + 1.5.4 verifications.
  **Verify:** Both prior verifications pass on a clean boot.

---

## Phase 2: PyTorch Classifier API (FR-1)

Goal: a fine-tuned DistilBERT intent classifier exposed as an internal FastAPI service. Latency target: < 50ms p99 on CPU for a single classification.

### 2.1 Synthetic Training Dataset
- [ ] **2.1.1** Create `data/training/generate_dataset.py` producing a CSV with columns `text,label` and ≥ 200 examples per intent across `Billing/Subscription`, `Technical Bug`, `Feature Request`, `Account Lockout/Security`. Use templated phrasing variations (company IDs, error codes, product names) seeded with `random.seed(42)`.
  **Verify:** `.venv\Scripts\python data/training/generate_dataset.py` produces `data/training/tickets.csv` with ≥ 800 rows.
- [ ] **2.1.2** Add `data/training/split.py` that produces stratified `train.csv` (70%), `val.csv` (15%), `test.csv` (15%).
  **Verify:** `.venv\Scripts\python data/training/split.py && wc -l data/training/*.csv` (or PowerShell equivalent `Get-ChildItem data\training\*.csv | ForEach-Object { (Get-Content $_).Count }`) shows the three files in the expected proportion.

### 2.2 Fine-Tuning Pipeline
- [ ] **2.2.1** Create `services/classifier-api/train.py` that loads `distilbert-base-uncased`, wraps it in `AutoModelForSequenceClassification` with `num_labels=4`, trains via HuggingFace `Trainer` for 3 epochs, and saves to `services/classifier-api/models/distilbert-intent/`.
  **Verify:** `.venv\Scripts\python services/classifier-api/train.py` finishes with eval `accuracy >= 0.90` on `val.csv` (printed to stdout).
- [ ] **2.2.2** Add a `label_map.json` (e.g. `{"0":"billing","1":"bug","2":"feature","3":"lockout"}`) saved alongside model weights.
  **Verify:** `Test-Path services/classifier-api/models/distilbert-intent/label_map.json` returns `True`.
- [ ] **2.2.3** Create `services/classifier-api/evaluate.py` that loads the model and prints a classification report against `test.csv`.
  **Verify:** Macro F1 ≥ 0.88 printed by the script.

### 2.3 FastAPI Service
- [ ] **2.3.1** Create `services/classifier-api/app/schemas.py` with `ClassifyRequest(text: str)` and `ClassifyResponse(intent: str, confidence: float, latency_ms: float)` Pydantic models.
  **Verify:** `.venv\Scripts\python -c "from estc.services.classifier_api.app.schemas import ClassifyRequest; ClassifyRequest(text='hi')"` exits 0.
- [ ] **2.3.2** Create `services/classifier-api/app/model_loader.py` that lazy-loads the model + tokenizer once at module import using `torch.set_num_threads(2)`.
  **Verify:** Importing the module and running `classify("my card was charged twice")` returns `intent == "billing"`.
- [ ] **2.3.3** Create `services/classifier-api/app/main.py` exposing `POST /classify` and `GET /healthz`. The `/classify` route must measure inference time and return it in the response.
  **Verify:** `.venv\Scripts\uvicorn estc.services.classifier_api.app.main:app --port 8001` then `Invoke-RestMethod -Method Post -Uri http://localhost:8001/classify -Body '{"text":"I cannot log in"}' -ContentType application/json` returns `intent == "lockout"` and `latency_ms < 50`.
- [ ] **2.3.4** Add `/healthz` returning `{"status":"ok","model_loaded":true}`.
  **Verify:** `Invoke-RestMethod http://localhost:8001/healthz` returns `model_loaded: True`.

### 2.4 Tests
- [ ] **2.4.1** Create `tests/test_classifier_api.py` with ≥ 8 pytest cases — two canonical examples per intent — using FastAPI's `TestClient`.
  **Verify:** `.venv\Scripts\pytest tests/test_classifier_api.py -v` reports `8 passed`.
- [ ] **2.4.2** Add a latency smoke test asserting `latency_ms < 50` across 20 sequential calls (warm cache).
  **Verify:** `.venv\Scripts\pytest tests/test_classifier_api.py::test_latency_p99 -v` passes.

### 2.5 Containerization
- [ ] **2.5.1** Write `services/classifier-api/Dockerfile` (Python 3.11-slim base, `requirements.txt`, copy app + model dir, `CMD uvicorn ... --host 0.0.0.0 --port 8001`).
  **Verify:** `docker build -t estc-classifier-api ./services/classifier-api` exits 0.
- [ ] **2.5.2** Wire `classifier-api` into `docker-compose.yml` with port mapping `8001:8001` and healthcheck calling `/healthz`.
  **Verify:** `docker compose up -d classifier-api` then `docker compose ps classifier-api` shows `healthy`.

### 2.6 Phase 2 Exit Gate
- [ ] **2.6.1** From host, `Invoke-RestMethod -Method Post -Uri http://localhost:8001/classify -Body '{"text":"500 error on /api/orders, company 9422"}' -ContentType application/json` returns `intent == "bug"`.
  **Verify:** Response matches expected intent.

---

## Phase 3: MCP Servers (FR-2)

Goal: two independent, **read-only** MCP servers — PostgreSQL (transactional context) and GitHub (engineering context) — each exposing typed tool schemas. The orchestrator cannot execute raw SQL or shell commands.

### 3.1 PostgreSQL MCP Server
- [ ] **3.1.1** Create `services/mcp-postgres/server.py` using the `fastmcp` framework. Initialize `mcp = FastMCP("estc-postgres")`.
  **Verify:** `.venv\Scripts\python -c "from estc.services.mcp_postgres.server import mcp; print(mcp.name)"` prints `estc-postgres`.
- [ ] **3.1.2** Register tool `get_customer_by_id(company_id: str)` running parameterized `SELECT * FROM enterprise_customers WHERE company_id = %s`.
  **Verify:** `mcp-inspector ./services/mcp-postgres/server.py` lists `get_customer_by_id` with one string param.
- [ ] **3.1.3** Register tool `get_subscription_status(company_id: str)` returning `subscription_tier` + `account_status`.
  **Verify:** Inspector tool call against a seeded company returns the expected tier.
- [ ] **3.1.4** Register tool `list_delinquent_accounts(limit: int = 10)` filtering `account_status = 'Delinquent'`.
  **Verify:** Inspector call returns rows; database row count matches `SELECT COUNT(*) FROM enterprise_customers WHERE account_status='Delinquent'`.
- [ ] **3.1.5** Enforce read-only enforcement: the DB user the server connects with must be granted `SELECT` only. Add `infra/sql/grants.sql` and run it in `init.sql`.
  **Verify:** `docker compose exec mcp-postgres psql -U estc_reader -d estc -c "INSERT INTO enterprise_customers VALUES ('x','x','Free','Active','x@y.z');"` fails with permission denied.
- [ ] **3.1.6** Write `tests/test_mcp_postgres.py` invoking each tool via the MCP client transport in-process.
  **Verify:** `.venv\Scripts\pytest tests/test_mcp_postgres.py -v` reports all green.
- [ ] **3.1.7** `Dockerfile` for `mcp-postgres` server. Wire into `docker-compose.yml`, depends_on the `mcp-postgres` Postgres service (rename one to avoid clash, e.g. `postgres-db` vs `mcp-postgres-server`).
  **Verify:** `docker compose up -d mcp-postgres-server` healthy.

### 3.2 GitHub MCP Server
- [ ] **3.2.1** Create `services/mcp-github/server.py` exposing tools backed by `PyGithub` reading `GITHUB_PAT` from env.
  **Verify:** Server starts and lists tools via `mcp-inspector`.
- [ ] **3.2.2** Tool `search_issues(repo: str, query: str, state: str = "open")` returning `[{"number","title","url","labels"}]`.
  **Verify:** Call against a public test repo returns ≥ 1 issue dict.
- [ ] **3.2.3** Tool `list_recent_commits(repo: str, limit: int = 5)` returning sha/author/message.
  **Verify:** Inspector call returns 5 commits.
- [ ] **3.2.4** Tool `get_deployment_log(repo: str)` reading the latest GitHub Actions deployment workflow run.
  **Verify:** Returns `{"workflow","status","conclusion","run_url"}` against the project's own repo.
- [ ] **3.2.5** Mock fallback for offline tests: if `GITHUB_PAT` is empty, the server reads from `tests/fixtures/github_mock.json`.
  **Verify:** `.venv\Scripts\pytest tests/test_mcp_github.py -v` passes with `GITHUB_PAT` unset.
- [ ] **3.2.6** Disallow any write methods — add a static guard that raises if a registered tool name matches `/create|update|delete|merge|close/i`.
  **Verify:** A unit test attempting to register `close_issue` raises `RuntimeError`.
- [ ] **3.2.7** Dockerfile + compose wiring for `mcp-github-server`.
  **Verify:** `docker compose up -d mcp-github-server` healthy; `docker compose logs mcp-github-server` shows "tools registered: 3".

### 3.3 Phase 3 Exit Gate
- [x] **3.3.1** Run the MCP inspector against both servers and confirm only read-style tools appear.
  **Verify:** Tool list output contains the 3 + 3 tool names above and no others.

---

## Phase 4: LangGraph Stateful Engine (FR-3) + RAG

Goal: a LangGraph state machine implementing `classify → router → {billing | bug | feature | lockout} → supervisor_review`, backed by a LangChain RAG pipeline over ChromaDB, with Ragas + LangSmith observability.

### 4.1 Shared Schema
- [x] **4.1.1** Implement `shared/schemas/agent_state.py` with the exact `AgentState` Pydantic model from `design.md` section 3 (8 fields).
  **Verify:** `.venv\Scripts\python -c "from estc.shared.schemas.agent_state import AgentState; AgentState(ticket_id='t1', raw_issue_text='x', company_id='9422')"` exits 0.

### 4.2 RAG Pipeline (LangChain + ChromaDB)
- [x] **4.2.1** Drop 10–20 sample product docs into `data/knowledge_base/` (markdown). Cover API errors, billing, account lockout flows.
  **Verify:** `Get-ChildItem data/knowledge_base/*.md | Measure-Object | Select Count` ≥ 10.
- [x] **4.2.2** Create `services/orchestrator/rag/ingest.py` that chunks docs with `ParentDocumentRetriever` (child 256 tokens / parent 1024) and embeds with `BAAI/bge-large-en-v1.5` into Chroma persisted at `./chroma_db/`.
  **Verify:** `.venv\Scripts\python services/orchestrator/rag/ingest.py` then `.venv\Scripts\python -c "import chromadb; c=chromadb.PersistentClient('./chroma_db'); print(c.get_collection('estc').count())"` ≥ 50.
- [x] **4.2.3** Add `services/orchestrator/rag/retriever.py` building two semantic indices — `kb_billing` and `kb_technical` — with a semantic router selecting between them.
  **Verify:** Unit test `tests/test_rag.py::test_semantic_router` routes "500 error" to `kb_technical` and "refund" to `kb_billing`.
- [x] **4.2.4** Smoke retrieval test: a known-answer query returns at least one chunk with the expected phrase.
  **Verify:** `.venv\Scripts\pytest tests/test_rag.py::test_retrieval_recall -v` passes.

### 4.3 LangGraph Nodes
- [ ] **4.3.1** Create `services/orchestrator/graph/nodes/classify.py` calling `POST $CLASSIFIER_API_URL/classify` and writing `state.intent`.
  **Verify:** Unit test using `httpx.MockTransport` returns expected intent for known input.
- [ ] **4.3.2** Conditional router function `route_by_intent(state)` returning one of `billing_agent | bug_agent | feature_agent | lockout_agent`.
  **Verify:** Pytest table-driven test covers all 4 intents.
- [ ] **4.3.3** `billing_agent` node: calls Postgres MCP `get_subscription_status`, runs RAG over `kb_billing`, drafts a reply with `gpt-4o-mini` (or Claude Sonnet 4.6 if `ANTHROPIC_API_KEY` set).
  **Verify:** Integration test against seeded company 9422 produces `state.agent_draft_response` mentioning the customer's tier.
- [ ] **4.3.4** `bug_agent` node: calls GitHub MCP `search_issues`, runs RAG over `kb_technical`, drafts a reply citing issue numbers.
  **Verify:** Test asserts `state.agent_draft_response` contains `#<digit>`.
- [ ] **4.3.5** `feature_agent` node: RAG over both indices; drafts an acknowledgement and creates an internal-only synthetic ticket (no MCP write).
  **Verify:** Test asserts `state.execution_logs` records "feature_logged".
- [ ] **4.3.6** `lockout_agent` node (escalation path): pulls Postgres customer record, marks `state.requires_escalation = True` regardless of confidence, drafts a verification explainer.
  **Verify:** Test asserts `state.requires_escalation is True` and `state.confidence_score >= 0`.
- [ ] **4.3.7** `supervisor_review` node: if `state.confidence_score < 0.70` OR `state.requires_escalation`, append "ESCALATE" to logs and set requires_escalation. Otherwise mark "AUTO_APPROVED".
  **Verify:** Two pytest scenarios — one low-confidence, one high — produce the correct log entries.

### 4.4 Graph Wiring
- [ ] **4.4.1** Create `services/orchestrator/graph/build.py` wiring nodes via `StateGraph(AgentState)` with conditional edges. Compile with `MemorySaver` checkpointer for run-resume.
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.graph.build import graph; print(graph.get_graph().draw_mermaid())"` prints a Mermaid diagram showing all 6 nodes.
- [ ] **4.4.2** Expose a `run_ticket(ticket_id, text, company_id) -> AgentState` async entrypoint that streams node events.
  **Verify:** End-to-end test invokes it against the seeded DB and returns a populated `AgentState` in < 10s.

### 4.5 Observability & Evaluation
- [ ] **4.5.1** Initialize LangSmith tracing — set `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=estc-dev`. Every node call must appear as a child run.
  **Verify:** After one ticket run, `langsmith.Client().list_runs(project_name='estc-dev', limit=10)` returns ≥ 1 root run with ≥ 6 child runs.
- [ ] **4.5.2** Build `tests/eval/ragas_eval.py` computing **Faithfulness**, **Answer Relevance**, **Context Recall** over a fixture set of 20 tickets.
  **Verify:** `.venv\Scripts\python tests/eval/ragas_eval.py` writes `tests/eval/results.csv` with all three metrics ≥ 0.80 mean.
- [ ] **4.5.3** Add `make eval` / `pwsh ./scripts/eval.ps1` shortcut.
  **Verify:** Running the script reproduces 4.5.2 output.

### 4.6 FastAPI Wrapper for Orchestrator
- [ ] **4.6.1** Expose `POST /tickets` and `GET /tickets/{id}/stream` (SSE) on the orchestrator-app. The SSE endpoint must emit one event per LangGraph node transition.
  **Verify:** `Invoke-RestMethod` POSTs a ticket and `curl -N http://localhost:8002/tickets/<id>/stream` shows ≥ 4 `data:` events.
- [ ] **4.6.2** Dockerfile + compose wiring for `orchestrator-app` exposing port 8002.
  **Verify:** `docker compose up -d orchestrator-app` healthy.

### 4.7 Phase 4 Exit Gate
- [ ] **4.7.1** End-to-end CLI run: `POST /tickets {"text":"I am getting a 500 error when pulling the API, my company ID is 9422"}` produces an SSE stream containing node names `classify`, `bug_agent`, `supervisor_review`, ending with a draft response and a non-zero confidence.
  **Verify:** Captured SSE log contains all three node markers and a `draft_response` field.

---

## Phase 5: Streamlit UI (FR-4)

Goal: a Support Specialist Operations Center showing inbound tickets, the real-time agent execution map, the draft response with confidence, Approve / Modify controls, and the human escalation queue.

### 5.1 Skeleton
- [ ] **5.1.1** Create `services/ui/app.py` with sidebar = "Inbound Tickets", main = three columns ("AI Analysis", "Draft", "Escalation Queue").
  **Verify:** `.venv\Scripts\streamlit run services/ui/app.py` renders all three column headers.
- [ ] **5.1.2** Add a mock ingestion form (text area + company_id input + Submit) that POSTs to the orchestrator.
  **Verify:** Submitting a ticket via the UI returns a `ticket_id` shown in a toast.

### 5.2 Real-Time Agent Map
- [ ] **5.2.1** Subscribe to the orchestrator's SSE stream; render a vertical progress timeline updating per event (`classify → bug_agent → supervisor_review`).
  **Verify:** While a ticket runs, the timeline advances in < 2s steps without page refresh.
- [ ] **5.2.2** Each node row shows status icon (in-progress / done / failed) + millisecond duration.
  **Verify:** Visual inspection — every node row shows an icon and a duration after completion.

### 5.3 Draft Panel
- [ ] **5.3.1** Display `agent_draft_response` in a code-fence-style box with a "Confidence: NN%" badge derived from `state.confidence_score`.
  **Verify:** Badge color is green ≥ 80, amber 60–79, red < 60.
- [ ] **5.3.2** `Approve Draft` button calls `POST /tickets/{id}/approve` (stub endpoint that closes the ticket in state).
  **Verify:** Clicking moves the ticket from "Active" sidebar list to "Closed" with a green check.
- [ ] **5.3.3** `Modify & Override` opens an editable text area; saving posts `PATCH /tickets/{id}` and re-evaluates confidence on the new text.
  **Verify:** Edited draft updates confidence in the UI after save.

### 5.4 Escalation Queue
- [ ] **5.4.1** Right column lists tickets where `requires_escalation == True` under "Requires Manual Verification".
  **Verify:** A simulated `lockout` ticket appears in this column and **not** in the auto-approval flow.
- [ ] **5.4.2** Each escalation row shows the customer's tier, account status, and a "Claim" button that assigns the ticket to the current operator.
  **Verify:** Clicking Claim removes the ticket from the queue and adds operator name to `state.execution_logs`.

### 5.5 Containerization
- [ ] **5.5.1** Write `services/ui/Dockerfile` (Python 3.11-slim, `requirements-ui.txt`, `CMD streamlit run app.py --server.address 0.0.0.0 --server.port 8501`).
  **Verify:** `docker build -t estc-ui ./services/ui` exits 0.
- [ ] **5.5.2** Wire `ui-client` into `docker-compose.yml` with port `8501:8501` and `depends_on: [orchestrator-app]`.
  **Verify:** `docker compose up -d` brings all 5 services up; `docker compose ps` shows all `healthy`.

### 5.6 Phase 5 Exit Gate — Full E2E Smoke Test
- [ ] **5.6.1** Spin everything up with `docker compose up -d`. From `http://localhost:8501` submit the canonical ticket *"I am getting a 500 error when pulling the API, my company ID is 9422"*. Confirm: timeline shows `classify → bug_agent → supervisor_review`, draft references at least one GitHub issue number, confidence ≥ 80%, Approve closes the ticket.
  **Verify:** All four observations hold; LangSmith run for this ticket is visible at `https://smith.langchain.com/projects/estc-dev`.
- [ ] **5.6.2** Submit *"I cannot log in to my account, company 9422"*. Confirm: classifier returns `lockout`, ticket appears in **Requires Manual Verification**, auto-approval is blocked.
  **Verify:** Both observations hold and `state.requires_escalation == True` in the orchestrator log.
- [ ] **5.6.3** Run the Ragas eval suite (4.5.2) once more against the live containerized orchestrator.
  **Verify:** All three metrics still ≥ 0.80 mean.

---

## Definition of Done (Whole Project)
- All 5 phase exit gates pass on a fresh `docker compose down -v && docker compose up -d --build`.
- `pytest` green across `tests/` (classifier, MCP servers, RAG, graph nodes).
- Ragas evaluation: Faithfulness, Answer Relevance, Context Recall ≥ 0.80 mean.
- LangSmith project `estc-dev` shows traces for every E2E test in 5.6.
- README.md updated with run instructions and a screenshot of the UI dashboard.

---

## Decisions Embedded in This Plan
- **Two compose-level Postgres entities** — design.md names `mcp-postgres-server` as a service, but a real Postgres DB is also needed. Split into `postgres-db` (datastore) and `mcp-postgres-server` (the MCP wrapper) per task 3.1.7.
- **DistilBERT over Llama-3-8B QLoRA** — design.md mentions both. Plan commits to DistilBERT (faster to fine-tune, CPU-friendly, meets < 50ms latency target). QLoRA path is out of scope.
- **Read-only enforcement** at two layers — database GRANT (3.1.5) and GitHub MCP code-level guard (3.2.6) — to honor the security constraint in design.md §2.
- **Confidence threshold = 0.70** for supervisor escalation (4.3.7). speculation.md says "below a determined baseline"; this is a tunable.
