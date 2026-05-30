"""Shared LLM drafting helper for the Phase 4.3 worker agents (spec § 4.1, FR-8).

Provider selection is lazy and key-driven:

- ``ANTHROPIC_API_KEY`` set  -> Claude Sonnet 4.6 via ``langchain_anthropic``
- else ``OPENAI_API_KEY`` set -> ``gpt-4o-mini`` via ``langchain_openai``
- else ``HF_TOKEN`` set       -> Llama-3-8B-Instruct via ``langchain_huggingface``
- else (the CI / offline default) -> a deterministic, network-free template

The provider modules are imported *inside* the selected branch so the offline
template path never requires ``langchain-openai`` / ``langchain-anthropic`` /
``langchain-huggingface`` to be installed. ``draft_reply`` always injects ``facts``
into the output so the node-level assertions (the subscription tier for billing,
the ``#<n>`` issue refs for bug) hold on the template, cloud, and HF paths alike.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Optional

# Grounding penalty: a draft built with no retrieved context is less trustworthy,
# so it gets a lower confidence and naturally trends toward supervisor escalation.
CONFIDENCE_FLOOR_NO_CONTEXT = 0.55
CONFIDENCE_WITH_CONTEXT = 0.85

# Hugging Face hosted-inference model (design.md Component D names Llama-3-8B-Instruct).
HF_REPO_ID = "meta-llama/Meta-Llama-3-8B-Instruct"


@lru_cache(maxsize=1)
def _chat_model() -> Optional[Any]:
    """Build a LangChain ChatModel once, or return ``None`` for the template path."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model="claude-sonnet-4-6", temperature=0.2)
    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
    if os.environ.get("HF_TOKEN"):
        from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

        endpoint = HuggingFaceEndpoint(
            repo_id=HF_REPO_ID,
            task="text-generation",
            huggingfacehub_api_token=os.environ["HF_TOKEN"],
            temperature=0.2,
        )
        return ChatHuggingFace(llm=endpoint)
    return None


def _facts_line(facts: dict[str, str]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in facts.items()) or "no account facts available"


def _template_reply(intent: str, facts: dict[str, str], context: list[str]) -> str:
    """Deterministic, network-free draft. MUST surface every value in ``facts`` so the
    node tests (tier mention, ``#<n>`` issue refs) pass with no LLM present."""
    snippet = (context[0][:400] + "...") if context else "no knowledge-base context found"
    return (
        f"[draft:{intent}] Thanks for reaching out. Based on your account "
        f"({_facts_line(facts)}), here is what we found: {snippet}"
    )


def _build_prompt(intent: str, issue_text: str, context: list[str], facts: dict[str, str]) -> str:
    ctx = "\n\n".join(context) if context else "(no knowledge-base context retrieved)"
    return (
        f"You are an enterprise support specialist drafting a reply for a '{intent}' ticket.\n"
        f"Customer issue:\n{issue_text}\n\n"
        f"Known account facts (incorporate each one verbatim): {_facts_line(facts)}\n\n"
        f"Grounding context (base your answer ONLY on this):\n{ctx}\n\n"
        f"Write a concise, helpful draft reply."
    )


async def draft_reply(
    *,
    intent: str,
    issue_text: str,
    context: list[str],
    facts: dict[str, str],
) -> tuple[str, float]:
    """Return ``(draft_text, confidence)``.

    Confidence is ``CONFIDENCE_WITH_CONTEXT`` when at least one context chunk was
    retrieved, else ``CONFIDENCE_FLOOR_NO_CONTEXT``. The model path is used only when
    an API key is present; otherwise the deterministic template is returned.
    """
    confidence = CONFIDENCE_WITH_CONTEXT if context else CONFIDENCE_FLOOR_NO_CONTEXT
    model = _chat_model()
    if model is None:
        return _template_reply(intent, facts, context), confidence

    prompt = _build_prompt(intent, issue_text, context, facts)
    response = await model.ainvoke(prompt)
    text = getattr(response, "content", str(response))
    if isinstance(text, list):  # some providers return a list of content blocks
        text = " ".join(str(part) for part in text)
    # Guarantee the asserted facts survive even if the model omitted them.
    missing = [v for v in facts.values() if v and v not in text]
    if missing:
        text = f"{text}\n\n(Account details: {_facts_line(facts)})"
    return text, confidence
