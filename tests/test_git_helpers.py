"""Tests for _git_push_with_retry."""

import subprocess

import pytest


def test_push_to_local_branch(temp_git_repo):
    """Commit something, push, and verify the remote received the commit."""
    repo = temp_git_repo
    (repo / "test.txt").write_text("hello")
    subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "test commit"], cwd=repo, check=True,
                   capture_output=True)

    from pdclaw import _git_push_with_retry
    assert _git_push_with_retry(repo) is True

    local = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True,
    ).stdout.strip()
    remote = subprocess.run(
        ["git", "rev-parse", "origin/main"], cwd=repo,
        capture_output=True, text=True,
    ).stdout.strip()
    assert local == remote


def test_push_no_upstream_creates_branch(temp_git_repo):
    """Push a branch that doesn't exist on remote — should use -u flag."""
    repo = temp_git_repo
    subprocess.run(["git", "checkout", "-b", "new-branch"], cwd=repo, check=True,
                   capture_output=True)
    (repo / "new.txt").write_text("content")
    subprocess.run(["git", "add", "new.txt"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "new branch"], cwd=repo, check=True,
                   capture_output=True)

    from pdclaw import _git_push_with_retry
    assert _git_push_with_retry(repo) is True

    remote = subprocess.run(
        ["git", "rev-parse", "origin/new-branch"], cwd=repo,
        capture_output=True, text=True,
    )
    assert remote.returncode == 0
    local = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True,
    ).stdout.strip()
    assert remote.stdout.strip() == local


def test_push_retry_all_failures_logs_warning(temp_git_repo, mocker):
    """Point remote to unreachable URL, verify 3 attempts are made."""
    repo = temp_git_repo
    subprocess.run(
        ["git", "remote", "set-url", "origin",
         "https://invalid.example.com/repo.git"],
        cwd=repo, check=True, capture_output=True,
    )
    sleep_mock = mocker.patch("pdclaw.time.sleep")

    from pdclaw import _git_push_with_retry
    assert _git_push_with_retry(repo) is False

    # sleep called on attempts 1 and 2 (attempt 3 doesn't sleep before last)
    assert sleep_mock.call_count >= 2
    sleep_mock.assert_has_calls([mocker.call(10), mocker.call(20)])
