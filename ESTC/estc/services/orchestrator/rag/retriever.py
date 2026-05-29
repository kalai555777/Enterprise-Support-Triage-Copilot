"""Semantic-routing RAG retriever (Phase 4.2, task 4.2.3).

Exposes two logical indices over the single persisted ``estc`` Chroma collection:

- ``kb_billing``   — chunks whose ``domain`` metadata is ``billing``
- ``kb_technical`` — chunks whose ``domain`` metadata is ``technical``

The two "indices" are metadata-filtered views of one collection (the design choice
flagged as Risk #2 in the execution plan): this keeps the roadmap's single-``estc``
collection count gate (4.2.2) intact while still giving the Phase 4.3 worker nodes
domain-isolated retrieval.

Routing is genuinely *semantic*: ``route_query`` embeds the query with the bge model
and compares it against per-domain embedding centroids, selecting the nearest. There
is no keyword list — "500 error" lands in ``kb_technical`` and "refund" in
``kb_billing`` because of where they sit in embedding space.

Per the Parent-Document pattern, ``retrieve`` matches on fine-grained child chunks
but returns the broad 1024-token *parent* text (carried in child metadata) so the
downstream LLM receives readable context.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from functools import lru_cache

import chromadb
from pydantic import BaseModel

from estc.services.orchestrator.rag.ingest import (
    CHROMA_PATH,
    COLLECTION_NAME,
    DOMAIN_BILLING,
    DOMAIN_TECHNICAL,
    get_embeddings,
)


class KBIndex(str, Enum):
    """The two semantic knowledge-base indices."""

    BILLING = "kb_billing"
    TECHNICAL = "kb_technical"


_DOMAIN_BY_INDEX: dict[KBIndex, str] = {
    KBIndex.BILLING: DOMAIN_BILLING,
    KBIndex.TECHNICAL: DOMAIN_TECHNICAL,
}
_INDEX_BY_DOMAIN: dict[str, KBIndex] = {v: k for k, v in _DOMAIN_BY_INDEX.items()}


class RetrievedChunk(BaseModel):
    """One retrieval hit. ``content`` is the broad parent context, not the raw child."""

    content: str
    source: str
    index: KBIndex
    score: float


@lru_cache(maxsize=1)
def _get_collection() -> chromadb.api.models.Collection.Collection:
    """Lazy singleton handle to the persisted ``estc`` collection."""
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_collection(COLLECTION_NAME)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec] if norm else vec


@lru_cache(maxsize=1)
def _domain_centroids() -> dict[str, list[float]]:
    """Mean (L2-normalized) embedding per domain, computed once from the collection.
    These centroids are the semantic anchors the router compares queries against."""
    data = _get_collection().get(include=["embeddings", "metadatas"])
    sums: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for emb, meta in zip(data["embeddings"], data["metadatas"]):
        domain = meta["domain"]
        if domain not in sums:
            sums[domain] = [0.0] * len(emb)
            counts[domain] = 0
        sums[domain] = [s + e for s, e in zip(sums[domain], emb)]
        counts[domain] += 1
    return {d: _normalize([s / counts[d] for s in vec]) for d, vec in sums.items()}


def route_query(query: str) -> KBIndex:
    """Select the index whose domain centroid is most similar to the query embedding.
    Falls back to ``KBIndex.TECHNICAL`` if no centroids are available (empty store)."""
    centroids = _domain_centroids()
    if not centroids:
        return KBIndex.TECHNICAL
    q = _normalize(get_embeddings().embed_query(query))
    best_domain = max(centroids, key=lambda d: _dot(q, centroids[d]))
    return _INDEX_BY_DOMAIN[best_domain]


def retrieve(query: str, index: KBIndex | None = None, k: int = 4) -> list[RetrievedChunk]:
    """Retrieve up to ``k`` parent-level context chunks for ``query``.

    If ``index`` is omitted the semantic router selects it. Matching happens on child
    chunks; results are de-duplicated by parent so the same parent is returned once.
    """
    if index is None:
        index = route_query(query)
    domain = _DOMAIN_BY_INDEX[index]
    collection = _get_collection()

    q = get_embeddings().embed_query(query)
    # Over-fetch children so that after de-duplicating by parent we still have k.
    n = min(max(k * 4, k), collection.count())
    res = collection.query(
        query_embeddings=[q],
        n_results=n,
        where={"domain": domain},
        include=["metadatas", "distances"],
    )

    metadatas = res["metadatas"][0]
    distances = res["distances"][0]

    hits: list[RetrievedChunk] = []
    seen_parents: set[str] = set()
    for meta, dist in zip(metadatas, distances):
        parent_id = meta["parent_id"]
        if parent_id in seen_parents:
            continue
        seen_parents.add(parent_id)
        hits.append(
            RetrievedChunk(
                content=meta["parent_content"],
                source=meta["source"],
                index=index,
                score=1.0 - dist,  # cosine space -> similarity
            )
        )
        if len(hits) >= k:
            break
    return hits


async def aretrieve(query: str, index: KBIndex | None = None, k: int = 4) -> list[RetrievedChunk]:
    """Async wrapper for the LangGraph worker nodes (Phase 4.3), which call from
    inside coroutines. Offloads the blocking embed/Chroma work to a thread so the
    event loop is never blocked."""
    return await asyncio.to_thread(retrieve, query, index, k)
