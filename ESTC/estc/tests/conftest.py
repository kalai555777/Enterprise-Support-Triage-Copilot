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
