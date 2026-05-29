"""Tests for the Phase 4.2 RAG pipeline (tasks 4.2.3 / 4.2.4).

Covers the semantic router, known-answer retrieval recall, and the Parent-Document
guarantee that retrieval returns broad parent context rather than the raw child.

The suite runs against the persisted ``./chroma_db`` store. A session-scoped fixture
guarantees the collection exists and is populated (>= 50 chunks); if not, it triggers
an ingest first so the suite is self-contained on a clean checkout.
"""

from __future__ import annotations

import chromadb
import pytest

from estc.services.orchestrator.rag.ingest import CHROMA_PATH, COLLECTION_NAME, run_ingest
from estc.services.orchestrator.rag.retriever import KBIndex, aretrieve, retrieve, route_query


@pytest.fixture(scope="session", autouse=True)
def _ensure_index() -> None:
    """Ensure the estc collection is built and dense enough before any test runs."""
    try:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        count = client.get_collection(COLLECTION_NAME).count()
    except Exception:
        count = 0
    if count < 50:
        run_ingest()


def test_semantic_router():
    # AC-3: routing is semantic — domain-defining phrases land in the right index.
    assert route_query("500 error") is KBIndex.TECHNICAL
    assert route_query("refund") is KBIndex.BILLING
    # A couple of fuller, natural phrasings to show it is not a 2-string lookup.
    assert route_query("I was charged twice and want my money back") is KBIndex.BILLING
    assert route_query("the API keeps returning an internal server error") is KBIndex.TECHNICAL


def test_retrieval_recall():
    # AC-4: a known-answer query surfaces a chunk containing the expected phrase.
    billing_hits = retrieve("how long does a refund take?", index=KBIndex.BILLING, k=4)
    assert any("5 to 7 business days" in h.content for h in billing_hits)

    tech_hits = retrieve("what does a 500 error mean?", index=KBIndex.TECHNICAL, k=4)
    assert any("unhandled exception" in h.content for h in tech_hits)


def test_retrieval_routes_when_index_omitted():
    # Retrieval without an explicit index uses the semantic router end to end.
    hits = retrieve("I need a refund for a duplicate charge", k=3)
    assert hits, "expected at least one hit"
    assert all(h.index is KBIndex.BILLING for h in hits)


def test_parent_context():
    # AC-5: retrieve returns the broad PARENT chunk, not the fine-grained child.
    # A 128-token child is ~500 chars; the 1024-token parent is materially larger.
    hits = retrieve("password reset and account lockout", index=KBIndex.TECHNICAL, k=2)
    assert hits
    longest = max(len(h.content) for h in hits)
    assert longest > 800, f"parent context unexpectedly short ({longest} chars)"
    for h in hits:
        assert -1.0 <= h.score <= 1.0
        assert h.source.endswith(".md")


async def test_aretrieve_matches_sync():
    # The async wrapper used by Phase 4.3 nodes returns the same shape as the sync call.
    hits = await aretrieve("invoice billing cycle", index=KBIndex.BILLING, k=2)
    assert hits
    assert all(h.index is KBIndex.BILLING for h in hits)
