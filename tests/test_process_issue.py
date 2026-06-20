"""Tests for process_issue decomposition."""

import json
from datetime import datetime, timezone

import pytest


def _seed_state(state_dir, number, state):
    """Helper: write a state file for the given issue number."""
    path = state_dir / str(number)
    path.mkdir(parents=True, exist_ok=True)
    (path / "state.json").write_text(json.dumps(state))


def _default_mocks(mocker):
    """Set up mocks needed for step execution paths."""
    mock_branch = mocker.patch("pdclaw.ensure_pdca_branch",
                               return_value="pdca/42-test-issue")
    mock_skill = mocker.patch("pdclaw.run_skill",
                              return_value=(True, "output"))
    mock_push = mocker.patch("pdclaw.git_commit_and_push", return_value=True)
    mocker.patch("pdclaw.get_metrics", return_value=None)
    mocker.patch("pdclaw.step_files_exist", return_value=True)
    return mock_branch, mock_skill, mock_push


def test_plan_step_execution(isolated_repo, mock_gh, mocker):
    """#pdca-start with empty state → plan step runs, files generated."""
    state_dir = isolated_repo / ".pdca_state"

    # A human comment has the #pdca-start tag
    mock_gh.get_issue_comments.return_value = [
        {"id": 1, "body": "Let's do this #pdca-start",
         "user": {"login": "user"},
         "created_at": "2024-01-02T00:00:00Z"}
    ]

    mock_branch, mock_skill, mock_push = _default_mocks(mocker)

    from pdclaw import process_issue
    process_issue("owner", "repo", 42, state_dir, isolated_repo,
                  auto_run=True, use_session=False)

    # State should show plan as completed
    state_file = state_dir / "42" / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert "plan" in state["completed_steps"]
    assert state["current_step"] == "plan"
    assert mock_skill.called
    assert mock_branch.called


def test_idempotent_poll(isolated_repo, mock_gh, mocker):
    """No new tags → no step execution, state unchanged."""
    state_dir = isolated_repo / ".pdca_state"
    _seed_state(state_dir, 42, {
        "issue_number": 42,
        "completed_steps": [],
        "current_step": None,
        "status": "active",
        "last_check": "2024-01-01T00:00:00+00:00",
    })

    mock_skill = mocker.patch("pdclaw.run_skill")

    from pdclaw import process_issue
    process_issue("owner", "repo", 42, state_dir, isolated_repo,
                  auto_run=True, use_session=False)

    mock_skill.assert_not_called()


def test_check_approved_enters_decision_phase(isolated_repo, mock_gh, mocker):
    """#check-approved with phase=check-review → phase becomes decision."""
    state_dir = isolated_repo / ".pdca_state"
    _seed_state(state_dir, 42, {
        "issue_number": 42,
        "completed_steps": ["plan", "do", "check"],
        "current_step": "check",
        "status": "active",
        "phase": "check-review",
        "last_check": "2024-01-01T00:00:00+00:00",
    })

    mock_gh.get_issue_comments.return_value = [
        {"id": 1, "body": "Looks good #check-approved",
         "user": {"login": "user"},
         "created_at": "2024-01-02T00:00:00Z"}
    ]

    from pdclaw import process_issue
    process_issue("owner", "repo", 42, state_dir, isolated_repo,
                  auto_run=True, use_session=False)

    state = json.loads((state_dir / "42" / "state.json").read_text())
    assert state["phase"] == "decision"
    mock_gh.add_comment.assert_called()


def test_lifecycle_abort_sets_state(isolated_repo, mock_gh, mocker):
    """#pdca-abort → state.status == 'aborted'."""
    state_dir = isolated_repo / ".pdca_state"
    _seed_state(state_dir, 42, {
        "issue_number": 42,
        "completed_steps": ["plan"],
        "current_step": "plan",
        "status": "active",
        "last_check": "2024-01-01T00:00:00+00:00",
    })

    mock_gh.get_issue_comments.return_value = [
        {"id": 1, "body": "Abort this #pdca-abort",
         "user": {"login": "user"},
         "created_at": "2024-01-02T00:00:00Z"}
    ]

    from pdclaw import process_issue
    process_issue("owner", "repo", 42, state_dir, isolated_repo,
                  auto_run=True, use_session=False)

    state = json.loads((state_dir / "42" / "state.json").read_text())
    assert state["status"] == "aborted"
    mock_gh.add_comment.assert_called_once()


def test_refresh_reprocesses_current_step(isolated_repo, mock_gh, mocker):
    """#pdca-refresh → current step is re-executed."""
    state_dir = isolated_repo / ".pdca_state"
    _seed_state(state_dir, 42, {
        "issue_number": 42,
        "completed_steps": ["plan"],
        "current_step": "plan",
        "status": "active",
        "last_check": "2024-01-01T00:00:00+00:00",
    })

    mock_gh.get_issue_comments.return_value = [
        {"id": 1, "body": "Please refresh #pdca-refresh",
         "user": {"login": "user"},
         "created_at": "2024-01-02T00:00:00Z"}
    ]

    mock_branch, mock_skill, mock_push = _default_mocks(mocker)

    from pdclaw import process_issue
    process_issue("owner", "repo", 42, state_dir, isolated_repo,
                  auto_run=True, use_session=False)

    # run_skill should be called (current step re-executed)
    assert mock_skill.called
    # The skill name should contain "plan" (current step)
    call_args = mock_skill.call_args[0]
    assert "plan" in call_args[0]
