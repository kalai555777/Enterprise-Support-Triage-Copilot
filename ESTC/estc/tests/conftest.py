"""Test config: load .env, set Windows-compatible event loop, override POSTGRES_HOST."""
from __future__ import annotations

import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv

load_dotenv()
os.environ["POSTGRES_HOST"] = "localhost"
os.environ.setdefault("POSTGRES_READER_USER", "estc_reader")
os.environ.setdefault("POSTGRES_READER_PASSWORD", "estc_reader_dev_pw")

# Phase 3.2: force GitHub MCP into deterministic mock mode for the default test run.
os.environ.pop("GITHUB_PAT", None)
os.environ.setdefault("GITHUB_MOCK_PATH", "estc/tests/fixtures/github_mock.json")
