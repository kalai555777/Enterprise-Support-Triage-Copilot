# Execution Plan: Phase 4.2 — RAG Pipeline (LangChain + ChromaDB)
**Source spec:** `.claude/specs/04-rag-pipeline-spec.md`
**Source plan section:** `docs/plan.md` § Phase 4 (tasks 4.2.1 – 4.2.4)
**Status:** AWAITING APPROVAL — no code to be executed until user replies `Proceed`.

---

## Context

This plan operationalizes Phase 4.2 of the ESTC roadmap: a LangChain Parent-Document RAG pipeline over a persisted ChromaDB store, embedded with `BAAI/bge-large-en-v1.5`, segmented into two semantically-routed indices (`kb_billing`, `kb_technical`) — the grounded-knowledge layer consumed by the Phase 4.3 LangGraph worker nodes. The work has four threads that must be done in order:

1. **Knowledge corpus** (data authoring): seed ≥ 10 markdown product docs covering API errors, billing/refunds, and account-lockout flows.
2. **Ingestion pipeline** (`ingest.py`): child(256)/parent(1024) chunking, bge embeddings, idempotent persist into Chroma collection `estc` (≥ 50 chunks).
3. **Semantic-routing retriever** (`retriever.py`): two indices + `route_query()` selecting between them, returning parent-level context.
4. **Test harness** (`tests/test_rag.py`): semantic-router routing test + known-answer retrieval-recall test.

This plan deliberately mirrors `docs/plan.md` verification phrasing (each task's **Verify** is lifted from the roadmap's own gates) and keeps `ingest.py` off the request path — it is an offline, CLI-invocable rebuild. The retriever is request-time read-only.

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**. A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** Confirm the source spec exists and is readable.
  **Verify:** `Test-Path .claude/specs/04-rag-pipeline-spec.md` returns `True`.
- [ ] **PF-2** Confirm we are on the feature branch with a clean tree.
  **Verify:** `git rev-parse --abbrev-ref HEAD` returns `feature/4.2-rag-pipeline`; `git status --short` is empty.
- [ ] **PF-3** Confirm the project venv exists and Python is 3.11.
  **Verify:** `.venv\Scripts\python --version` reports `Python 3.11.*`.
- [ ] **PF-4** Confirm orchestrator RAG dependencies are importable (from `requirements-orchestrator.txt`: langchain, chromadb, sentence-transformers).
  **Verify:** `.venv\Scripts\python -c "import langchain, chromadb, sentence_transformers; print('ok')"` prints `ok`.
- [ ] **PF-5** Confirm Phase 4.1 artifact is present (the retriever feeds `AgentState.retrieved_context`).
  **Verify:** `Test-Path shared/schemas/agent_state.py` returns `True`.
- [ ] **PF-6** Confirm `chroma_db/` is git-ignored so the vector store is never committed.
  **Verify:** `git check-ignore chroma_db/` echoes the path with no error.

---

## Task 4.2.1 — Seed the knowledge base corpus

- [ ] Create `data/knowledge_base/` (if absent) and author ≥ 10 markdown docs. Coverage must include: API error references (e.g. 500/timeout/auth errors), billing & refund flows, and account-lockout / recovery procedures. Each doc long enough to yield multiple child chunks (so the corpus clears the ≥ 50-chunk gate downstream). Embed at least one distinctive known-answer phrase per domain for the recall test (Task 4.2.4).
  **Verify:** `Get-ChildItem data/knowledge_base/*.md | Measure-Object | Select-Object Count` shows Count ≥ 10. Matches AC-1.

---

## Task 4.2.2 — Ingestion pipeline (`ingest.py`)

### 4.2.2-a Implement `services/orchestrator/rag/ingest.py`
- [ ] Implement `run_ingest(kb_dir, persist_path) -> IngestReport` per spec §4.1: load all `*.md`, split into parent (~1024 tokens) and child (~256 tokens) chunks (ParentDocumentRetriever pattern), embed children with `BAAI/bge-large-en-v1.5`, persist to Chroma `PersistentClient('./chroma_db')` collection `estc`. Idempotent: delete & recreate the collection on re-run (no duplicate append). Fail fast if `kb_dir` is missing or has 0 markdown files. Make the module CLI-invocable (`python services/orchestrator/rag/ingest.py`).
  **Verify:** `.venv\Scripts\python services/orchestrator/rag/ingest.py` exits 0 and prints an `IngestReport` with `child_chunks >= 50`.

### 4.2.2-b Confirm persisted collection count
- [ ] No code change — verify the persisted store.
  **Verify:** `.venv\Scripts\python -c "import chromadb; c=chromadb.PersistentClient('./chroma_db'); print(c.get_collection('estc').count())"` prints a number ≥ 50. Matches AC-2.

### 4.2.2-c Confirm idempotency
- [ ] Re-run ingest; count must not grow.
  **Verify:** Run 4.2.2-a again, then 4.2.2-b — the printed count is unchanged. Matches AC-6.

---

## Task 4.2.3 — Semantic-routing retriever (`retriever.py`)

- [ ] Implement `services/orchestrator/rag/retriever.py` per spec §4.1: `KBIndex` enum (`kb_billing`, `kb_technical`), `RetrievedChunk` Pydantic model (parent-level `content`), `route_query(query) -> KBIndex` (technical/error → `kb_technical`, billing/refund → `kb_billing`, ambiguous → default `kb_technical`), and `retrieve(query, index=None, k=4) -> List[RetrievedChunk]` returning parent context for top-k matched children. Apply the bge query-instruction prefix on the query side only. Build the two indices `kb_billing` / `kb_technical` partitioning the corpus by domain.
- [ ] Keep blocking embedding/Chroma calls wrappable for async consumers (Phase 4.3 nodes call this from async coroutines) — structure `retrieve()` so a blocking core can be `asyncio.to_thread`-wrapped without refactor.
  **Verify:** `.venv\Scripts\python -c "from services.orchestrator.rag.retriever import route_query, KBIndex; print(route_query('500 error'), route_query('refund'))"` prints `KBIndex.TECHNICAL KBIndex.BILLING` (or their values `kb_technical kb_billing`). Backs AC-3.

---

## Task 4.2.4 — Test harness (`tests/test_rag.py`)

### 4.2.4-a Test module + fixtures
- [ ] Create `tests/test_rag.py`. Add a session-scoped fixture that ensures the corpus is ingested (or reuses the persisted `./chroma_db/`) so tests run against a populated store.
  **Verify:** `.venv\Scripts\pytest tests/test_rag.py --collect-only -q` lists the test functions with no collection error.

### 4.2.4-b Test cases — must include at minimum (spec §6.1 AC-3, AC-4)
- [ ] `test_semantic_router` → asserts `route_query("500 error") == KBIndex.TECHNICAL` and `route_query("refund") == KBIndex.BILLING`.
- [ ] `test_retrieval_recall` → a known-answer query returns ≥ 1 chunk whose content contains the expected seeded phrase.
- [ ] `test_parent_context` (supports AC-5) → a retrieved chunk's content is parent-granularity (length materially larger than a 256-token child).

  **Verify:** `.venv\Scripts\pytest tests/test_rag.py -v` reports **all green** with at least 3 passed.

---

## Phase 4.2 Exit Gate

- [ ] **EG-1 (corpus + index integrity, AC-1/AC-2)** — proves the corpus is ingested and the store is populated.
  **Verify:** `Get-ChildItem data/knowledge_base/*.md | Measure-Object | Select-Object Count` ≥ 10 **and** the Chroma count one-liner ≥ 50. **Fallback if the embedding model cannot download:** pre-pull `BAAI/bge-large-en-v1.5` into the HF cache, then re-run ingest.
- [ ] **EG-2 (test suite)** — proves routing + recall behavior.
  **Verify:** `.venv\Scripts\pytest tests/test_rag.py -v` reports **0 failed**.
- [ ] **EG-3 (clean-boot regression)** — confirm reproducibility from a clean store.
  **Verify:** `Remove-Item -Recurse -Force chroma_db` then re-run `.venv\Scripts\python services/orchestrator/rag/ingest.py`, then re-run EG-2. Both must succeed.

---

## Risks & Open Questions

1. **Embedding model size / offline availability** — `bge-large-en-v1.5` is ~1.3GB; first ingest needs network + disk. Mitigation: pre-pull into HF cache; document in README; fail ingest with a clear remediation message rather than a partial collection.
2. **Single collection vs. two named indices** — plan.md task 4.2.2 says collection `estc` (count ≥ 50) while 4.2.3 says build `kb_billing` + `kb_technical`. Mitigation: implement two indices as metadata-filtered partitions of (or sibling collections alongside) `estc` so the 4.2.2 count gate and the 4.2.3 routing gate are both satisfied. **Open question for reviewer:** prefer two physical Chroma collections, or one `estc` collection with a `domain` metadata filter? (Default chosen: metadata-partition within `estc`.)
3. **Router precision** — a keyword/embedding semantic router may misroute ambiguous queries. Mitigation: deterministic default to `kb_technical`; the routing decision is logged for Phase 4.5 observability; threshold tunable.
4. **Token-based chunking dependency** — exact 256/1024 token splitting needs a tokenizer; LangChain token splitters require `tiktoken` or the model tokenizer. Mitigation: use the recursive token splitter with the bge tokenizer; if unavailable, approximate by character count calibrated to the token target.

---

## Out of Scope (explicitly deferred)

- LangGraph node integration (`billing_agent`/`bug_agent` calling `retrieve()`) — Phase 4.3.
- Ragas Faithfulness / Context Recall scoring of retrieved context — Phase 4.5.2.
- LangSmith tracing of retrieval spans — Phase 4.5.1.
- Serving Chroma as a networked service / containerization of the orchestrator — Phase 4.6.2.

---

**Awaiting `Proceed` to begin execution at PF-1.**
