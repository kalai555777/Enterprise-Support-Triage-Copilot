# Architectural Specification: Phase 4.2 — RAG Pipeline (LangChain + ChromaDB)
**Status:** DRAFT / PROPOSED
**Associated Tasks:** Tasks 4.2.1 – 4.2.4 (subordinate to Phase 4: LangGraph Stateful Engine + RAG)
**Target Files:**
- `data/knowledge_base/*.md` (10–20 product docs)
- `services/orchestrator/rag/ingest.py`
- `services/orchestrator/rag/retriever.py`
- `tests/test_rag.py`
- Persisted artifact: `./chroma_db/` (Chroma `PersistentClient` store)

---


## 1. Executive Summary & Problem Statement
### 1.1 Objective & Context
This sub-phase implements **Component C (Knowledge Retrieval — LangChain RAG)** of the Enterprise Support & Triage Copilot (ESTC) ecosystem. It builds the grounded-knowledge substrate that the downstream LangGraph worker nodes (`billing_agent`, `bug_agent`, `feature_agent`, `lockout_agent` — tasks 4.3.3–4.3.6) consult before drafting any customer-facing response.

Concretely, this phase delivers:
1. A curated **knowledge corpus** of markdown product documentation (API error references, billing/refund flows, account-lockout recovery procedures).
2. A **deterministic ingestion pipeline** (`ingest.py`) that applies **Parent-Document Retrieval** chunking — fine-grained 256-token child chunks for high-precision matching, broad 1024-token parent chunks passed to the LLM for readable context — embedded with `BAAI/bge-large-en-v1.5` into a persisted **ChromaDB** vector store.
3. A **semantic-routing retriever** (`retriever.py`) that maintains two specialized indices — `kb_billing` and `kb_technical` — and dynamically selects the correct index for an incoming query, mirroring the design.md "Semantic Routing" optimization.

The RAG pipeline is the factual grounding layer that makes ESTC's Faithfulness (Ragas, task 4.5.2) measurable: agent drafts must remain strictly within retrieved documentation.

### 1.2 Core Problem Statement
LLM worker nodes cannot safely answer enterprise support tickets from parametric memory — doing so produces hallucinated remediation steps, fabricated refund policies, and invented API error semantics. The system gap this specification closes is the **absence of a precise, domain-segmented, reproducible retrieval layer**: ESTC needs (a) high-precision retrieval that does not dilute matches across unrelated billing vs. technical content, and (b) sufficient surrounding context for the LLM to draft a coherent reply. Naïve flat-chunk single-index RAG fails on both counts — it returns truncated fragments and cross-contaminates billing and technical semantics. Parent-Document Retrieval plus a two-index semantic router resolves this trade-off.

---

## 2. System Boundaries & Constraints
### 2.1 Architectural Boundaries
- **Upstream Trigger/Consumer:** The LangGraph worker nodes (Phase 4.3) are the sole runtime consumers. `billing_agent` queries the `kb_billing` index; `bug_agent` queries `kb_technical`; `feature_agent` queries both. The `ingest.py` pipeline is triggered out-of-band (build/deploy time or `make ingest`), **not** on the request path.
- **Downstream Dependencies:**
  - **ChromaDB** persisted at `./chroma_db/` (local `PersistentClient`, no network service required for Phase 4.2).
  - **Embedding model** `BAAI/bge-large-en-v1.5` — downloaded via `sentence-transformers`/HuggingFace on first ingest; cached locally thereafter.
  - **Source corpus** `data/knowledge_base/*.md` must exist and be non-empty before ingestion.
  - This phase does **not** depend on the Postgres or GitHub MCP servers, the classifier API, or any external LLM — those are wired in Phase 4.3.

### 2.2 Technical & Operational Constraints
- **Performance / Latency:** Retrieval (child-similarity search + parent fetch) must complete well within the Phase 4.4.2 end-to-end budget of < 10s per ticket; target retrieval latency < 500ms warm. Embedding model load is a one-time cold cost amortized at process start (lazy singleton). Ingestion is offline and unbounded.
- **Security & Compliance:** Knowledge base contains **synthetic, non-PII** product documentation only. No customer records, secrets, or credentials are embedded into the vector store. The retriever is **read-only** at request time; only `ingest.py` writes to `./chroma_db/`. `chroma_db/` is git-ignored (per task 1.1.2).
- **Resource Limits:** `bge-large-en-v1.5` is a ~1.3GB model with 1024-dim embeddings — pin `torch` CPU threads consistently with the classifier service to avoid contention. Chroma collection must reach **≥ 50 chunks** (verification gate 4.2.2). Embedding batches should be bounded (e.g. 32 docs/batch) to cap peak memory.

---

## 3. Functional Requirements
- **FR-1 (Corpus):** The system shall provide ≥ 10 markdown knowledge documents under `data/knowledge_base/` covering at minimum API errors, billing/refunds, and account-lockout flows.
- **FR-2 (Parent-Document Ingestion):** `ingest.py` shall chunk every source doc into child segments (~256 tokens) and parent segments (~1024 tokens), embed child segments with `BAAI/bge-large-en-v1.5`, and persist them to a ChromaDB collection named `estc` at `./chroma_db/`, yielding ≥ 50 indexed chunks.
- **FR-3 (Parent linkage):** The retriever shall return broad **parent** context for each matched child, preserving readability for the consuming LLM (Parent-Document Retrieval pattern).
- **FR-4 (Two-Index Segmentation):** The system shall build two semantically distinct indices, `kb_billing` and `kb_technical`, partitioning the corpus by domain.
- **FR-5 (Semantic Router):** A semantic router shall select the correct index for a query — technical/error queries → `kb_technical`; billing/refund queries → `kb_billing`.
- **FR-6 (Retrieval Recall):** A known-answer query shall return at least one chunk containing the expected phrase (smoke recall guarantee).
- **FR-7 (Determinism/Reproducibility):** Re-running `ingest.py` shall produce a consistent collection (idempotent rebuild — clear/recreate rather than duplicate-append).

---

## 4. Detailed Component Specifications & API Contracts
### 4.1 Interface Code & Data Shapes

```python
# services/orchestrator/rag/ingest.py
from dataclasses import dataclass

CHILD_CHUNK_TOKENS: int = 256
PARENT_CHUNK_TOKENS: int = 1024
EMBED_MODEL: str = "BAAI/bge-large-en-v1.5"
CHROMA_PATH: str = "./chroma_db"
COLLECTION_NAME: str = "estc"

@dataclass(frozen=True)
class IngestReport:
    documents_loaded: int
    parent_chunks: int
    child_chunks: int          # must be >= 50 to pass 4.2.2
    collection_name: str
    persist_path: str

def run_ingest(kb_dir: str = "data/knowledge_base",
               persist_path: str = CHROMA_PATH) -> IngestReport:
    """Idempotently (re)build the Chroma collection from the markdown corpus
    using ParentDocumentRetriever-style child(256)/parent(1024) chunking and
    bge-large-en-v1.5 embeddings. Safe to re-run."""
    ...
```

```python
# services/orchestrator/rag/retriever.py
from enum import Enum
from typing import List
from pydantic import BaseModel

class KBIndex(str, Enum):
    BILLING = "kb_billing"
    TECHNICAL = "kb_technical"

class RetrievedChunk(BaseModel):
    content: str               # PARENT context (broad), not the raw child
    source: str                # originating doc filename
    index: KBIndex
    score: float               # similarity score of the matched child

def route_query(query: str) -> KBIndex:
    """Semantic router. e.g. '500 error' -> KBIndex.TECHNICAL,
    'refund' -> KBIndex.BILLING. Backs tests/test_rag.py::test_semantic_router."""
    ...

def retrieve(query: str, index: KBIndex | None = None, k: int = 4) -> List[RetrievedChunk]:
    """If index is None, call route_query(query) first. Returns parent-level
    context chunks for the top-k matched children."""
    ...
```

These shapes feed `AgentState.retrieved_context: List[str]` (design.md §3 / task 4.1.1) — node code maps `[c.content for c in retrieve(...)]` into that field.

### 4.2 Endpoint / Method Contracts
- **Target Interface / Route:** Library-level (no HTTP surface in Phase 4.2). Primary entrypoints: `services/orchestrator/rag/ingest.py::run_ingest()` (CLI-invocable via `python services/orchestrator/rag/ingest.py`) and `services/orchestrator/rag/retriever.py::retrieve()` / `route_query()`.
- **Input Parameters:**
  - `run_ingest(kb_dir: str, persist_path: str)` — corpus directory + Chroma persist path.
  - `retrieve(query: str, index: KBIndex | None, k: int)` — natural-language query, optional explicit index override, top-k.
  - `route_query(query: str)` — raw query string.
- **Output / Return Types:**
  - `run_ingest -> IngestReport` (child_chunks ≥ 50).
  - `retrieve -> List[RetrievedChunk]` (parent-level content).
  - `route_query -> KBIndex`.

---

## 5. Edge Cases & Error Handling
### 5.1 Anticipated Edge Cases
1. **Empty / missing corpus:** `data/knowledge_base/` absent or contains 0 markdown files → ingestion must fail fast with an actionable error rather than silently creating an empty collection that later starves the agents.
2. **Re-ingestion (idempotency):** Running `ingest.py` twice must not double the chunk count or create duplicate parent linkages — the pipeline clears/recreates the collection (or upserts by stable doc ID).
3. **Ambiguous router query:** A query with no clear billing/technical signal (e.g. "I have a question about my account") must deterministically fall back to a default index (`kb_technical`) rather than raising.
4. **Oversized / undersized document:** A doc shorter than one child chunk still yields exactly one child + one parent; a very large doc must be split without exceeding the parent token ceiling.
5. **Embedding model unavailable offline:** First-run download failure (no network) must surface a clear remediation message, not a truncated stack trace mid-ingest.

### 5.2 Error Handling & State Recovery Matrix

| Trigger / Exception | Handled State / Action | Fallback Behavior / Mitigation |
|---|---|---|
| `kb_dir` missing or 0 `.md` files | Raise `FileNotFoundError` with path + expected count | Abort ingest; instruct operator to populate `data/knowledge_base/` (task 4.2.1) |
| Embedding model download/load failure | Catch, log model name + cache dir | Abort with guidance to pre-pull `BAAI/bge-large-en-v1.5`; do not write partial collection |
| Re-run over existing collection | Detect existing `estc` collection | Delete & recreate (idempotent rebuild) before re-embedding |
| `retrieve()` returns 0 chunks | Log query + selected index | Return empty list; downstream node lowers `confidence_score` → supervisor escalation (task 4.3.7) |
| `route_query()` ambiguous query | No high-confidence index match | Default to `KBIndex.TECHNICAL`; record routing decision for observability |
| Chroma persist path unwritable | Raise on client init | Surface path + permission error; never swallow |
| Child chunk count < 50 after ingest | `IngestReport` flagged | Fail the 4.2.2 verification gate explicitly so the corpus gap is visible |

---

## 6. Acceptance Criteria
### 6.1 Technical Acceptance Criteria
- **AC-1 (4.2.1):** `Get-ChildItem data/knowledge_base/*.md | Measure-Object | Select Count` ≥ 10.
- **AC-2 (4.2.2):** `python services/orchestrator/rag/ingest.py` runs clean, then
  `python -c "import chromadb; c=chromadb.PersistentClient('./chroma_db'); print(c.get_collection('estc').count())"` prints ≥ 50.
- **AC-3 (4.2.3):** `pytest tests/test_rag.py::test_semantic_router -v` passes — "500 error" → `kb_technical`, "refund" → `kb_billing`.
- **AC-4 (4.2.4):** `pytest tests/test_rag.py::test_retrieval_recall -v` passes — a known-answer query returns ≥ 1 chunk containing the expected phrase.
- **AC-5 (Parent-Document):** Retrieved chunk content is parent-level (≈1024-token granularity), confirming child-match → parent-context expansion.
- **AC-6 (Idempotency):** Two consecutive `ingest.py` runs yield the same collection count (no duplication).

### 6.2 Business & Functional Alignment
- **BA-1:** Retrieval is **domain-segmented** (billing vs. technical), directly enabling design.md's "Semantic Routing" optimization and reducing cross-domain hallucination risk.
- **BA-2:** Parent-Document Retrieval preserves "technical text readability" for the drafting LLM, per design.md Component C.
- **BA-3:** The grounded context produced here is the prerequisite for Phase 4.5 Ragas **Faithfulness / Context Recall ≥ 0.80** gates — the retriever must surface the documentation an agent needs to answer without inventing facts.
- **BA-4:** Read-only-at-request-time retrieval honors the ESTC security posture — no customer PII or secrets enter the vector store; only synthetic product docs are indexed.

---

## Engineering Notes
- The downstream graph nodes that consume this retriever are async (LangGraph). When `retrieve()` is invoked from inside async node coroutines, wrap any blocking embedding/Chroma calls appropriately (e.g. `await asyncio.to_thread(...)`) so the event loop is never blocked — consistent with the project's async-first runtime convention.
- `BAAI/bge-large-en-v1.5` benefits from the recommended retrieval query instruction prefix ("Represent this sentence for searching relevant passages:") on the query side — apply it in `retrieve()`/`route_query()` for best recall, and keep the document side un-prefixed.
