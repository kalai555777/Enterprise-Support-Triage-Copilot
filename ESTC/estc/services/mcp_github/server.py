"""Read-only FastMCP server fronting GitHub (Phase 3.2, async)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastmcp import FastMCP
from github import Github
from pydantic import BaseModel

log = logging.getLogger("estc.mcp-github")


class IssueSummary(BaseModel):
    number: int
    title: str
    url: str
    labels: list[str]


class CommitSummary(BaseModel):
    sha: str
    author: str
    message: str


class DeploymentSummary(BaseModel):
    workflow: str
    status: str
    conclusion: Optional[str] = None
    run_url: str


mcp = FastMCP("estc-github")


_FORBIDDEN = re.compile(
    r"(?i)(create|update|delete|merge|close|patch|put|post|"
    r"add|remove|fork|rename|transfer|archive)"
)


def register_readonly_tool(mcp_instance):
    """Decorator factory: rejects mutating tool names at registration time."""

    def _decorator(fn):
        if _FORBIDDEN.search(fn.__name__):
            raise RuntimeError(f"Write-method tool name forbidden: {fn.__name__}")
        return mcp_instance.tool(fn)

    return _decorator


_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _validate_repo(repo: str) -> None:
    if not isinstance(repo, str) or not _REPO_RE.match(repo):
        raise ValueError(f"Invalid repo identifier: {repo!r}")


class MockGitHubClient:
    """File-backed deterministic stand-in. Used when GITHUB_PAT is unset."""

    def __init__(self, fixture_path: Path):
        self._data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))

    async def search_issues(
        self, repo: str, query: str, state: str
    ) -> list[dict]:
        bucket = self._data.get(repo, {}).get("issues", [])
        if state == "all":
            return list(bucket)
        return [i for i in bucket if i.get("state") == state]

    async def list_recent_commits(self, repo: str, limit: int) -> list[dict]:
        return self._data.get(repo, {}).get("commits", [])[:limit]

    async def get_deployment_log(self, repo: str) -> Optional[dict]:
        deps = self._data.get(repo, {}).get("deployments", [])
        return deps[0] if deps else None


class LiveGitHubClient:
    """Async wrapper around sync PyGithub via asyncio.to_thread."""

    def __init__(self, token: str):
        self._gh = Github(token)

    async def search_issues(
        self, repo: str, query: str, state: str
    ) -> list[dict]:
        q = f"repo:{repo} {query} state:{state}"

        def _call() -> list[dict]:
            return [
                {
                    "number": i.number,
                    "title": i.title,
                    "url": i.html_url,
                    "labels": [lab.name for lab in i.labels],
                }
                for i in self._gh.search_issues(query=q)[:50]
            ]

        return await asyncio.to_thread(_call)

    async def list_recent_commits(self, repo: str, limit: int) -> list[dict]:
        def _call() -> list[dict]:
            return [
                {
                    "sha": c.sha,
                    "author": (
                        c.author.login if c.author else c.commit.author.email
                    ),
                    "message": c.commit.message.split("\n", 1)[0],
                }
                for c in list(self._gh.get_repo(repo).get_commits()[:limit])
            ]

        return await asyncio.to_thread(_call)

    async def get_deployment_log(self, repo: str) -> Optional[dict]:
        def _call() -> Optional[dict]:
            runs = self._gh.get_repo(repo).get_workflow_runs()
            for run in list(runs[:25]):
                if run.event == "deployment" or "deploy" in run.name.lower():
                    return {
                        "workflow": run.name,
                        "status": run.status,
                        "conclusion": run.conclusion,
                        "run_url": run.html_url,
                    }
            return None

        return await asyncio.to_thread(_call)


_client: MockGitHubClient | LiveGitHubClient | None = None
_mode: str = "uninitialized"


def _get_client() -> MockGitHubClient | LiveGitHubClient:
    global _client, _mode
    if _client is None:
        token = os.environ.get("GITHUB_PAT", "").strip()
        if token:
            _client = LiveGitHubClient(token)
            _mode = "live"
        else:
            fixture_path = Path(
                os.environ.get(
                    "GITHUB_MOCK_PATH",
                    "estc/tests/fixtures/github_mock.json",
                )
            )
            _client = MockGitHubClient(fixture_path)
            _mode = "mock"
    return _client


@register_readonly_tool(mcp)
async def search_issues(
    repo: str, query: str, state: str = "open"
) -> list[IssueSummary]:
    """Search GitHub issues in a repo. Returns up to 50 results."""
    _validate_repo(repo)
    rows = await _get_client().search_issues(repo, query, state)
    return [
        IssueSummary(
            number=r["number"],
            title=r["title"],
            url=r["url"],
            labels=r["labels"],
        )
        for r in rows
    ]


@register_readonly_tool(mcp)
async def list_recent_commits(
    repo: str, limit: int = 5
) -> list[CommitSummary]:
    """List up to `limit` (1..50) most recent commits on the default branch."""
    _validate_repo(repo)
    limit = max(1, min(50, limit))
    rows = await _get_client().list_recent_commits(repo, limit)
    return [
        CommitSummary(sha=r["sha"], author=r["author"], message=r["message"])
        for r in rows
    ]


@register_readonly_tool(mcp)
async def get_deployment_log(repo: str) -> Optional[DeploymentSummary]:
    """Return the most-recent deployment-shaped workflow run, or None."""
    _validate_repo(repo)
    row = await _get_client().get_deployment_log(repo)
    if row is None:
        return None
    return DeploymentSummary(
        workflow=row["workflow"],
        status=row["status"],
        conclusion=row.get("conclusion"),
        run_url=row["run_url"],
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    _get_client()
    log.info("estc-github starting in %s mode; tools registered: 3", _mode)
    await mcp.run_async()


if __name__ == "__main__":
    asyncio.run(main())
