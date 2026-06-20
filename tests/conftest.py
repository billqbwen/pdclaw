"""Shared fixtures for PDClaw tests."""

import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def temp_git_repo(tmp_path):
    """Initialize a bare repo as origin + a clone as working copy."""
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "--bare", str(origin)], check=True,
                   capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "--allow-empty",
                    "-m", "initial"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push"], check=True,
                   capture_output=True)
    return work


@pytest.fixture
def isolated_repo(tmp_path):
    """Create a simple git repo with an initial commit (no remote)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"],
                   cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def sample_state():
    return {
        "issue_number": 42,
        "completed_steps": [],
        "current_step": None,
        "status": "active",
        "last_check": None,
    }


@pytest.fixture(autouse=True)
def clean_sessions():
    """Reset session cache before each test."""
    from pdca_claude_session import clear_all_sessions
    clear_all_sessions()
    yield
    clear_all_sessions()


@pytest.fixture
def mock_gh(mocker):
    """Mock the _GitHubClient singleton used by pdclaw."""
    gh = mocker.patch("pdclaw._gh")
    gh.is_pdca_runner_comment.return_value = False
    gh.get_issue.return_value = {
        "number": 42,
        "title": "Test Issue",
        "body": "Test body",
        "state": "open",
        "labels": [],
        "html_url": "https://github.com/owner/repo/issues/42",
        "updated_at": "2024-01-01T00:00:00Z",
        "assignee": None,
    }
    gh.get_issue_comments.return_value = []
    return gh
