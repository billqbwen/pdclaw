"""Tests for stash_context context manager."""

import subprocess

import pytest


def _add_tracked_file(repo, name, content, msg):
    """Helper: create a file, stage, and commit it."""
    path = repo / name
    path.write_text(content)
    subprocess.run(["git", "add", str(name)], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=repo, check=True,
                   capture_output=True)
    return path


def test_stash_clean_no_op(isolated_repo):
    """Repo has no dirty files — stash context should not call git stash."""
    from pdclaw import stash_context

    with stash_context(isolated_repo, "test-stash"):
        pass

    result = subprocess.run(
        ["git", "stash", "list"], cwd=isolated_repo,
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == ""


def test_stash_dirty_restores(isolated_repo):
    """Uncommitted changes should be restored after context exits."""
    repo = isolated_repo
    tracked = _add_tracked_file(repo, "tracked.txt", "original", "add tracked")

    tracked.write_text("modified")
    untracked = repo / "untracked.txt"
    untracked.write_text("new file")

    from pdclaw import stash_context
    with stash_context(repo, "test-stash"):
        # Inside context: tracked reverted to HEAD, untracked removed
        assert tracked.read_text() == "original"
        assert not untracked.exists()

    # Outside context: dirty state restored
    assert tracked.read_text() == "modified"
    assert untracked.read_text() == "new file"


def test_stash_context_on_exception(isolated_repo):
    """If the body raises, stash should still be popped."""
    repo = isolated_repo
    _add_tracked_file(repo, "tracked.txt", "original", "init")
    (repo / "tracked.txt").write_text("modified")

    from pdclaw import stash_context
    with pytest.raises(RuntimeError):
        with stash_context(repo, "test-stash"):
            raise RuntimeError("boom")

    # Stash popped, dirty state restored
    assert (repo / "tracked.txt").read_text() == "modified"


def test_nested_stash(isolated_repo):
    """Verify isolation when stash_context is nested — inner pop restores
    outer changes, outer pop restores original state."""
    repo = isolated_repo
    _add_tracked_file(repo, "a.txt", "a0", "add a")
    _add_tracked_file(repo, "b.txt", "b0", "add b")

    from pdclaw import stash_context

    # Pre-dirty a.txt
    (repo / "a.txt").write_text("a1")

    with stash_context(repo, "outer"):
        assert (repo / "a.txt").read_text() == "a0"  # stashed

        # Modify b.txt inside outer
        (repo / "b.txt").write_text("b1")

        with stash_context(repo, "inner"):
            assert (repo / "b.txt").read_text() == "b0"  # b1 stashed
            # Don't modify anything inside inner

        # Inner pop: b1 restored
        assert (repo / "b.txt").read_text() == "b1"

    # Outer pop: a1 restored (b1 stays)
    assert (repo / "a.txt").read_text() == "a1"
    assert (repo / "b.txt").read_text() == "b1"
