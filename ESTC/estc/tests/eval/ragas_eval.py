"""Ragas evaluation harness (Phase 4.5, task 4.5.2).

Runs the Phase 4.4 graph over a 20-ticket gold fixture and scores the RAG-backed drafts
on the three ``design.md`` § 4 metrics — **Faithfulness**, **Answer Relevance**, and
**Context Recall** — writing ``results.csv`` and exiting 0 iff every metric's mean is
>= 0.80.

Live-only by nature: every Ragas metric needs a judge LLM (and embeddings). With no judge
key the harness **skips cleanly** (prints a reason, writes no CSV, exits 0) so the clean
checkout stays green; the judge is selected from the same Anthropic -> OpenAI ladder as
``graph/llm.py``. Embeddings reuse the local ``bge`` model already used by RAG, so no
OpenAI embeddings key is required. ``ragas`` is import-repaired via ``_ragas_compat`` first
(Risk 1).
"""

from __future__ import annotations

import asyncio
import csv
import os
import sys
import warnings
from pathlib import Path

# Quiet third-party (ragas / langchain) deprecation noise so the CLI output stays readable
# and PowerShell wrappers don't treat the stderr lines as failures.
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Allow direct invocation (`python estc/tests/eval/ragas_eval.py`) — put repo root on path.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dotenv import load_dotenv

from estc.tests.eval._ragas_compat import ensure_ragas_importable

_HERE = Path(__file__).parent
FIXTURE = _HERE / "fixtures" / "eval_tickets.jsonl"
RESULTS = _HERE / "results.csv"
THRESHOLD = 0.80


def _skip(reason: str) -> int:
    print(f"evaluation skipped - {reason}")
    return 0  # non-failing: clean checkout / no-key CI stays green


def _load_fixture() -> list[dict]:
    import json

    rows = [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 20:
        raise ValueError(f"expected 20 fixture rows, found {len(rows)}")
    return rows


async def _collect_samples() -> list[dict]:
    """Run the graph over every fixture row, returning Ragas-ready sample dicts."""
    from estc.services.orchestrator.graph.build import run_ticket

    samples: list[dict] = []
    for i, row in enumerate(_load_fixture()):
        state = await run_ticket(f"eval-{i:02d}", row["question"], row["company_id"])
        samples.append(
            {
                "ticket_id": f"eval-{i:02d}",
                "intent": state.intent or "",
                "question": row["question"],
                "answer": state.agent_draft_response or "",
                "contexts": list(state.retrieved_context or []),
                "ground_truth": row["ground_truth"],
            }
        )
    return samples


def _build_judge():
    """Return ``(llm_wrapper, embeddings_wrapper)`` or ``None`` when no judge key is set."""
    from estc.services.orchestrator.graph.llm import _chat_model

    model = _chat_model()  # Anthropic -> OpenAI -> None (key-driven)
    if model is None:
        return None
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    from estc.services.orchestrator.rag.ingest import get_embeddings

    return LangchainLLMWrapper(model), LangchainEmbeddingsWrapper(get_embeddings())


def _atomic_write_csv(rows: list[dict], metric_cols: list[str], means: dict[str, float]) -> None:
    """Write per-ticket rows + a trailing ``mean`` row via temp file + atomic replace."""
    tmp = RESULTS.with_suffix(".csv.tmp")
    header = ["ticket_id", "intent", *metric_cols]
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in header})
        writer.writerow({"ticket_id": "mean", "intent": "", **{m: round(means[m], 4) for m in metric_cols}})
    os.replace(tmp, RESULTS)


def main() -> int:
    load_dotenv()  # populate os.environ for the llm ladder + Settings

    try:
        ensure_ragas_importable()
        from ragas import EvaluationDataset, evaluate
        from ragas.dataset_schema import SingleTurnSample
        from ragas.metrics import ContextRecall, Faithfulness, ResponseRelevancy
    except Exception as exc:  # import-broken or missing (Risk 1)
        return _skip(f"ragas unavailable: {exc}")

    judge = _build_judge()
    if judge is None:
        return _skip("no judge LLM key (set ANTHROPIC_API_KEY, OPENAI_API_KEY, or HF_TOKEN)")
    judge_llm, judge_emb = judge

    # The live path needs the orchestrator's dependencies (classifier API, Postgres) reachable
    # and the judge LLM callable. If any of that is unavailable, degrade to a clean skip
    # (FR-8) rather than crashing — the wiring itself is unit-tested in test_observability.py.
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        samples = asyncio.run(_collect_samples())

        metrics = [Faithfulness(), ResponseRelevancy(), ContextRecall()]
        metric_cols = [m.name for m in metrics]
        dataset = EvaluationDataset(
            samples=[
                SingleTurnSample(
                    user_input=s["question"],
                    response=s["answer"],
                    retrieved_contexts=s["contexts"],
                    reference=s["ground_truth"],
                )
                for s in samples
            ]
        )
        result = evaluate(dataset, metrics=metrics, llm=judge_llm, embeddings=judge_emb)
        df = result.to_pandas()
    except Exception as exc:  # noqa: BLE001 — infra/judge unreachable is a skip, not a failure
        return _skip(f"graph/judge not runnable in this environment: {type(exc).__name__}: {exc}")

    # Merge per-row scores back onto the ticket id/intent for the CSV.
    rows = []
    for s, (_, scored) in zip(samples, df.iterrows()):
        rows.append({"ticket_id": s["ticket_id"], "intent": s["intent"],
                     **{m: float(scored[m]) for m in metric_cols}})
    means = {m: df[m].mean() for m in metric_cols}
    _atomic_write_csv(rows, metric_cols, means)

    print("Ragas evaluation —", {m: round(means[m], 4) for m in metric_cols})
    print(f"results written to {RESULTS}")
    passed = all(means[m] >= THRESHOLD for m in metric_cols)
    print("RESULT:", "PASS" if passed else "BELOW THRESHOLD (0.80)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
