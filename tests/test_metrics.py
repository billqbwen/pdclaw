"""Tests for AI call metrics recording helper."""

import pytest


def test_record_ai_call_no_metrics(mocker):
    """_record_ai_call should not crash when get_metrics() returns None."""
    mocker.patch("pdclaw.get_metrics", return_value=None)
    from pdclaw import _record_ai_call
    _record_ai_call(42, "plan", True, 1.5, 100)


def test_record_ai_call_on_success(mocker):
    """_record_ai_call records correct values on success."""
    mock_mt = mocker.Mock()
    mocker.patch("pdclaw.get_metrics", return_value=mock_mt)
    from pdclaw import _record_ai_call, CLAUDE_MODEL

    _record_ai_call(42, "do", True, 2.3, 500)

    mock_mt.record_ai_call.assert_called_once_with(
        issue_number=42,
        step="do",
        success=True,
        elapsed_sec=2.3,
        model=CLAUDE_MODEL,
        output_chars=500,
    )


def test_record_ai_call_on_failure(mocker):
    """_record_ai_call records failure metrics with elapsed time."""
    mock_mt = mocker.Mock()
    mocker.patch("pdclaw.get_metrics", return_value=mock_mt)
    from pdclaw import _record_ai_call, CLAUDE_MODEL

    _record_ai_call(99, "check", False, 30.0, 0)

    mock_mt.record_ai_call.assert_called_once_with(
        issue_number=99,
        step="check",
        success=False,
        elapsed_sec=30.0,
        model=CLAUDE_MODEL,
        output_chars=0,
    )
