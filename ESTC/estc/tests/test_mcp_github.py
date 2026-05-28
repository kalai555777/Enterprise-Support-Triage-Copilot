"""Phase 3.2.6 — in-process MCP client + direct-call coverage for GitHub MCP."""
from __future__ import annotations

import time

import pytest
from fastmcp import Client

from estc.services.mcp_github.server import (
    CommitSummary,
    DeploymentSummary,
    IssueSummary,
    get_deployment_log,
    list_recent_commits,
    mcp,
    register_readonly_tool,
    search_issues,
)

REPO = "kalai555777/Enterprise-Support-Triage-Copilot"

EXPECTED_TOOLS = {
    "search_issues",
    "list_recent_commits",
    "get_deployment_log",
}


def test_server_name():
    assert mcp.name == "estc-github"


@pytest.mark.asyncio
async def test_lists_exactly_three_tools():
    async with Client(mcp) as c:
        tools = await c.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_search_issues_happy():
    rows = await search_issues(REPO, "anything", "open")
    assert len(rows) >= 1
    assert all(isinstance(r, IssueSummary) for r in rows)
    assert rows[0].number == 42
    assert "bug" in rows[0].labels


@pytest.mark.asyncio
async def test_search_issues_state_filter():
    open_rows = await search_issues(REPO, "x", "open")
    closed_rows = await search_issues(REPO, "x", "closed")
    all_rows = await search_issues(REPO, "x", "all")
    assert all(r.number != 11 for r in open_rows)  # #11 is closed in fixture
    assert any(r.number == 11 for r in closed_rows)
    assert len(all_rows) == len(open_rows) + len(closed_rows)


@pytest.mark.asyncio
async def test_list_recent_commits_count():
    rows = await list_recent_commits(REPO, limit=5)
    assert len(rows) == 5
    assert all(isinstance(r, CommitSummary) for r in rows)
    assert rows[0].sha == "abc123"


@pytest.mark.asyncio
async def test_get_deployment_log_populated():
    dep = await get_deployment_log(REPO)
    assert isinstance(dep, DeploymentSummary)
    assert dep.conclusion == "success"
    assert dep.workflow == "Deploy to staging"


@pytest.mark.asyncio
async def test_unknown_repo_returns_empty():
    assert await search_issues("nobody/nothing", "x", "open") == []
    assert await list_recent_commits("nobody/nothing", 5) == []
    assert await get_deployment_log("nobody/nothing") is None


@pytest.mark.asyncio
async def test_limit_clamped_high():
    rows = await list_recent_commits(REPO, limit=999)
    assert len(rows) <= 50


@pytest.mark.asyncio
async def test_limit_clamped_low():
    rows = await list_recent_commits(REPO, limit=-3)
    assert len(rows) == 1


@pytest.mark.parametrize(
    "bad_repo",
    [
        "; rm -rf /",
        "owner/repo/extra",
        "http://evil.com/x",
        "no-slash-at-all",
        "",
        "owner with space/repo",
    ],
)
@pytest.mark.asyncio
async def test_repo_validation_rejects_ssrf(bad_repo):
    with pytest.raises(ValueError, match="Invalid repo identifier"):
        await search_issues(bad_repo, "q", "open")


@pytest.mark.parametrize(
    "forbidden_name",
    [
        "create_issue",
        "update_repo",
        "delete_branch",
        "merge_pr",
        "close_issue",
        "patch_file",
        "post_comment",
        "add_label",
        "remove_label",
        "fork_repo",
        "rename_branch",
        "transfer_ownership",
        "archive_repo",
    ],
)
def test_write_guard_blocks_forbidden_verbs(forbidden_name):
    """FR-5: every mutating verb in `_FORBIDDEN` is rejected at registration."""
    decorator = register_readonly_tool(mcp)

    async def _stub():
        return None

    _stub.__name__ = forbidden_name
    with pytest.raises(RuntimeError, match="Write-method tool name forbidden"):
        decorator(_stub)


@pytest.mark.asyncio
async def test_latency_p95_mock_mode():
    # warm
    for _ in range(5):
        await search_issues(REPO, "x", "open")

    samples = []
    for _ in range(50):
        t0 = time.perf_counter()
        await search_issues(REPO, "x", "open")
        samples.append((time.perf_counter() - t0) * 1000)

    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 <= 20, f"p95={p95:.2f}ms exceeds 20ms mock-mode budget"
