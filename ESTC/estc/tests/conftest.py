"""Test config: load .env, set Windows-compatible event loop, override POSTGRES_HOST."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv

load_dotenv()
os.environ["POSTGRES_HOST"] = "localhost"
os.environ.setdefault("POSTGRES_READER_USER", "estc_reader")
os.environ.setdefault("POSTGRES_READER_PASSWORD", "estc_reader_dev_pw")
os.environ.setdefault("GITHUB_MOCK_PATH", "estc/tests/fixtures/github_mock.json")

# Vars a populated local .env sets that the default test baseline assumes are OFF
# (auth/rate-limit/persistence off, GitHub MCP in mock mode — matching CI).
_NEUTRALIZE = ("GITHUB_PAT", "ESTC_API_KEY", "ESTC_RATE_LIMIT_PER_MIN", "ESTC_PERSIST_POSTGRES")


@pytest.fixture(autouse=True)
def _test_env_baseline():
    """Re-assert the neutral baseline before EVERY test.

    A one-time pop isn't enough: some imported modules (e.g. ragas_eval) call
    ``load_dotenv()`` at import, which re-pollutes os.environ mid-session from a
    populated .env. Tests that need these vars set do so via monkeypatch (applied
    in the test body, after this fixture's setup).
    """
    for _k in _NEUTRALIZE:
        os.environ.pop(_k, None)
    yield
