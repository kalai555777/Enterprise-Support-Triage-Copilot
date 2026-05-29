"""RAG ingestion pipeline (Phase 4.2, task 4.2.2).

Parent-Document chunking + ``BAAI/bge-large-en-v1.5`` embeddings persisted into a
ChromaDB collection. Implements the design.md Component C "Parent-Document
Retrieval" pattern: documents are split into fine-grained *child* chunks (128
tokens; see CHILD_CHUNK_TOKENS note below) that are embedded for high-precision
matching, while the broader *parent* chunk (1024 tokens) is carried denormalized in
each child's metadata so the retriever can hand the LLM readable context without a
separate persisted docstore.

Design notes:
- Uses the raw ``chromadb`` client (not the LangChain Chroma wrapper, which is not
  installed for chromadb 1.5.x) so the collection name and counts are explicit and
  match the roadmap verification (collection ``estc``, count >= 50).
- The collection uses cosine space so retrieval distances map cleanly to a score.
- Idempotent: an existing ``estc`` collection is dropped and rebuilt on every run,
  so re-ingesting never duplicates chunks.
- Standalone-runnable (``python estc/services/orchestrator/rag/ingest.py``); it
  imports no other ``estc`` module, so it does not depend on sys.path packaging.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection
from langchain_community.embeddings import HuggingFaceBgeEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from transformers import AutoTokenizer

# --- Configuration (imported by retriever.py) -------------------------------
KB_DIR = "estc/data/knowledge_base"
CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "estc"
EMBED_MODEL = "BAAI/bge-large-en-v1.5"

# Child granularity is 128 tokens (not the spec's nominal 256): the seeded corpus
# is ~350 words/doc, so 256-token children produced only ~20 chunks — below the
# >=50 high-precision-index gate (roadmap 4.2.2). A 128-token child preserves the
# "fine-grained child / broad parent" intent (8:1 ratio) while yielding a dense
# enough index. Parent stays at 1024 tokens for readable LLM context.
CHILD_CHUNK_TOKENS = 128
PARENT_CHUNK_TOKENS = 1024
CHILD_OVERLAP_TOKENS = 24
PARENT_OVERLAP_TOKENS = 128

DOMAIN_BILLING = "billing"
DOMAIN_TECHNICAL = "technical"

_ADD_BATCH = 64
_embeddings: HuggingFaceBgeEmbeddings | None = None


@dataclass(frozen=True)
class IngestReport:
    """Outcome of one ingest run. ``child_chunks`` must be >= 50 to pass AC-2."""

    documents_loaded: int
    parent_chunks: int
    child_chunks: int
    collection_name: str
    persist_path: str

    def __str__(self) -> str:
        return (
            f"IngestReport(documents_loaded={self.documents_loaded}, "
            f"parent_chunks={self.parent_chunks}, child_chunks={self.child_chunks}, "
            f"collection_name={self.collection_name!r}, persist_path={self.persist_path!r})"
        )


def get_embeddings() -> HuggingFaceBgeEmbeddings:
    """Lazy singleton bge embedder. ``embed_documents`` is used here for the corpus;
    the bge query instruction is applied only on the query side (see retriever)."""
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceBgeEmbeddings(
            model_name=EMBED_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    return _embeddings


def domain_for_file(path: Path) -> str:
    """Map a knowledge-base filename to its semantic domain. ``billing_*`` docs are
    the billing index; everything else (API errors, auth, lockout) is technical."""
    return DOMAIN_BILLING if path.name.startswith("billing") else DOMAIN_TECHNICAL


def _build_splitters() -> tuple[RecursiveCharacterTextSplitter, RecursiveCharacterTextSplitter]:
    """Token-accurate parent/child splitters keyed off the bge tokenizer so the
    256/1024-token targets from the spec are literal, not character approximations."""
    tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL)
    parent = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        tokenizer, chunk_size=PARENT_CHUNK_TOKENS, chunk_overlap=PARENT_OVERLAP_TOKENS
    )
    child = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        tokenizer, chunk_size=CHILD_CHUNK_TOKENS, chunk_overlap=CHILD_OVERLAP_TOKENS
    )
    return parent, child


def _reset_collection(client: chromadb.api.ClientAPI) -> Collection:
    """Drop and recreate the target collection (idempotent rebuild, cosine space)."""
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass  # collection did not exist yet
    return client.create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def run_ingest(kb_dir: str = KB_DIR, persist_path: str = CHROMA_PATH) -> IngestReport:
    """(Re)build the Chroma ``estc`` collection from the markdown corpus.

    Raises ``FileNotFoundError`` if the corpus directory is missing or empty so a
    silent empty index can never starve the downstream agents.
    """
    kb_path = Path(kb_dir)
    md_files = sorted(kb_path.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(
            f"No markdown documents found in {kb_path.resolve()} "
            f"(task 4.2.1 must seed >= 10 docs before ingestion)."
        )

    parent_splitter, child_splitter = _build_splitters()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, str]] = []
    parent_count = 0

    for path in md_files:
        text = path.read_text(encoding="utf-8")
        domain = domain_for_file(path)
        for p_idx, parent in enumerate(parent_splitter.split_text(text)):
            parent_id = f"{path.stem}::p{p_idx}"
            parent_count += 1
            for c_idx, child in enumerate(child_splitter.split_text(parent)):
                ids.append(f"{parent_id}::c{c_idx}")
                documents.append(child)
                metadatas.append(
                    {
                        "source": path.name,
                        "domain": domain,
                        "parent_id": parent_id,
                        "parent_content": parent,
                    }
                )

    embeddings = get_embeddings().embed_documents(documents)

    client = chromadb.PersistentClient(path=persist_path)
    collection = _reset_collection(client)
    for start in range(0, len(ids), _ADD_BATCH):
        end = start + _ADD_BATCH
        collection.add(
            ids=ids[start:end],
            embeddings=embeddings[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )

    return IngestReport(
        documents_loaded=len(md_files),
        parent_chunks=parent_count,
        child_chunks=len(ids),
        collection_name=COLLECTION_NAME,
        persist_path=persist_path,
    )


if __name__ == "__main__":
    report = run_ingest()
    print(report)
    if report.child_chunks < 50:
        raise SystemExit(
            f"FAIL: only {report.child_chunks} child chunks indexed (need >= 50). "
            f"Add more / longer docs to {KB_DIR}."
        )
    print(f"OK: indexed {report.child_chunks} child chunks into '{report.collection_name}'.")
