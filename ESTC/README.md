# Enterprise Support & Triage Copilot (ESTC)

A decoupled, multi-service support-triage system. A local **DistilBERT** classifier
routes inbound tickets; a **LangGraph** state machine drafts grounded replies using
**RAG** (LangChain + ChromaDB) and two read-only **MCP** servers (PostgreSQL + GitHub);
a **Streamlit** operations center lets a human approve, modify, or escalate.

```
[ Streamlit UI ]  ──REST/SSE──▶  [ LangGraph Orchestrator ]
                                   │          │            │
                       ┌───────────┘          │            └───────────┐
                       ▼                       ▼                        ▼
              [ DistilBERT API ]      [ LangChain RAG ]        [ MCP servers ]
              (intent + conf.)        (ChromaDB)               ├─ PostgreSQL (read-only)
                                                               └─ GitHub (read-only)
```

## Services

| Service               | Port | What it does                                              |
|-----------------------|------|-----------------------------------------------------------|
| `classifier-api`      | 8001 | DistilBERT intent classifier (FastAPI). Trains at build.  |
| `orchestrator-app`    | 8002 | LangGraph engine + REST/SSE API.                          |
| `ui-client`           | 8501 | Streamlit support-operations dashboard.                   |
| `postgres-db`         | 5432 | Customer records datastore.                               |
| `mcp-postgres-server` | —    | Read-only MCP wrapper over Postgres.                      |
| `mcp-github-server`   | —    | Read-only MCP wrapper over GitHub (mock fallback).        |

## Quick start (Docker)

```bash
cp .env.example .env          # optional: add ANTHROPIC_API_KEY / OPENAI_API_KEY for real LLM drafts
docker compose up -d --build  # first build trains the classifier — allow a few minutes
docker compose ps             # all services should report healthy
```

Then open the dashboard at **http://localhost:8501** and submit a ticket, e.g.
*"I am getting a 500 error when pulling the API, my company ID is 9422"*.

> **Note on the classifier image:** the `classifier-api` Dockerfile is multi-stage —
> the first stage fine-tunes DistilBERT from the committed CSVs in
> `estc/data/training/`, the runtime stage ships only the trained weights. No large
> model binary is committed to git; the model is reproduced from source.

> **LLM drafting:** with no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`HF_TOKEN`, the worker
> agents fall back to a deterministic offline template — the system runs fully offline.

## Local development

```bash
python -m venv .venv
.venv\Scripts\activate                              # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt                     # core + classifier
pip install -r requirements-orchestrator.txt        # langgraph / rag / mcp
pip install -r requirements-ui.txt                  # streamlit
```

### Train / evaluate the classifier

```bash
python estc/data/training/generate_dataset.py       # regenerate tickets.csv (seeded)
python estc/data/training/split.py                  # stratified train/val/test
python -m estc.services.classifier_api.train        # writes models/distilbert_intent/ (val acc >= 0.90)
python -m estc.services.classifier_api.evaluate     # macro F1 on the test split (>= 0.88)
```

### Build the RAG store

```bash
python estc/services/orchestrator/rag/ingest.py     # embeds knowledge_base/ into ./chroma_db
```

## Tests

```bash
pytest estc/tests -q
```

Two suites need extra setup and are skipped/failing otherwise:
- `test_rag.py` needs `sentence-transformers` installed (downloads the bge embedder).
- `test_mcp_postgres.py` needs the `postgres-db` service running and seeded.

The classifier tests load the trained model from `estc/services/classifier_api/models/`;
they skip cleanly if it is absent (run `train.py` first).

## Evaluation (Ragas)

```bash
pwsh ./scripts/eval.ps1      # or: make eval
```

Computes Faithfulness, Answer Relevance, and Context Recall over the 20-ticket gold
fixture. Requires a judge-LLM key and a running orchestrator; skips cleanly otherwise.

## Configuration

All settings load from `.env` (see `.env.example`) via `estc/shared/config.py`.
Secrets (API keys, DB passwords) are never committed — `.env` is git-ignored.
