# PDClaw Testing Plan

## Overview

This plan covers the refactoring targets in priority order. Each section specifies the test strategy, expected coverage, and how to verify the refactored code matches the original behavior.

---

## 1. Git Push Retry Helper (`_git_push_with_retry`)

**Refactoring target:** Extract 5 duplications of the same retry loop into one function.

### Test strategy: Integration tests with a live temp git repo

```python
# tests/test_git_helpers.py
# Requires: git available on PATH

def test_push_to_local_branch():
    """Set up a local git repo with a remote pointing to another local dir,
    commit something, push, and verify the remote received the commit."""
    ...

def test_push_no_upstream_creates_branch():
    """Push a branch that doesn't exist on remote — should use -u flag."""

def test_push_retry_all_failures_logs_warning():
    """Point remote to unreachable URL, verify 3 attempts are made."""

def test_push_verify_checks_local_vs_remote():
    """After push, fetch and compare SHAs."""
```

### Key invariant to preserve

The original 5 call sites all follow this pattern:
```python
push_ok = False
for attempt in range(1, 4):
    try:
        subprocess.run([...], check=True, timeout=120, ...)
        push_ok = True
        break
    except (TimeoutExpired, CalledProcessError):
        ...
    if attempt < 3:
        time.sleep(attempt * 10)
return push_ok  # or equivalent action
```

The helper must return `bool` matching the original semantics exactly.

---

## 2. Stash Context Manager (`stash_context`)

**Refactoring target:** Extract the "check dirty → stash → yield → pop stash" pattern.

### Test strategy: Integration tests with dirty/unstaged files

```python
def test_stash_clean_no_op():
    """Repo has no dirty files — stash context should not call git stash."""

def test_stash_dirty_restores():
    """Repo has uncommitted changes — after context exits, files should
    be restored exactly as before."""

def test_stash_context_on_exception():
    """If the body raises, stash should still be popped."""

def test_nested_stash():
    """If stash_context is used inside another stash, verify isolation."""
```

### Key invariant to preserve

The original code:
1. Checks `git status --porcelain`
2. Only stashes if output is non-empty
3. Uses `git stash push --include-untracked -m "<label>"`
4. Always pops stash after the operation
5. Pops even if the operation fails (wrapped in try/except at the pop site)

---

## 3. `process_issue()` Decomposition

**Refactoring target:** Split the 758-line function into focused sub-functions:
- `_retry_pending_push(state, ...)`
- `_execute_step(state, step_to_execute, ...)`
- Phase handlers that already exist (`_handle_lifecycle_tags`, `_handle_decision_tag`)

### Test strategy: Function-level integration tests

Since `process_issue` couples to GitHub API, git, and file system, pure unit tests aren't practical. Instead:

#### a) End-to-end regression test

Create a test harness that:
1. Sets up a real git repo with a known state
2. Creates a mock state directory with controlled state files
3. Mocks `_gh` (GitHubClient) to return canned issue/comment data
4. Runs `process_issue()` and checks final state + git state + comments written

```python
# tests/test_process_issue.py

@pytest.fixture
def isolated_repo(tmp_path):
    """Create a git repo with initial commit, return repo_root."""
    ...

@pytest.fixture
def mock_gh(mocker):
    """Mock _GitHubClient to return controlled responses."""
    gh = mocker.patch("pdclaw._gh")
    gh.get_issue.return_value = {...}
    gh.get_issue_comments.return_value = [...]
    return gh

def test_plan_step_execution(isolated_repo, mock_gh):
    """#pdca-start with empty state → plan step runs, files generated."""
    ...

def test_idempotent_poll():
    """No new tags → no step execution, state unchanged."""

def test_check_approved_enters_decision_phase():
    """#check-approved in tags with phase=check-review → phase becomes decision."""

def test_lifecycle_abort_sets_state():
    """#pdca-abort → state.status == 'aborted'."""

def test_refresh_reprocesses_current_step():
    """#pdca-refresh → current step re-executed."""
```

#### b) Snapshot-based state machine verification

For each state transition, pre-seed a state file and provide controlled tags, then verify:
- The `completed_steps` list
- The `current_step` value
- The `phase` value
- Whether a comment was posted (and what it said)

This covers the `resolve_next_step` → phase guards → execution dispatch chain.

---

## 4. AI Metrics Recording Wrapper

**Refactoring target:** Replace 5 manual `record_ai_call()` calls with a context manager or decorator.

### Test strategy: Unit tests with a mock MetricsCollector

```python
def test_metrics_decorator_records_on_success():
    """Decorator wraps function, records success metrics."""

def test_metrics_decorator_records_on_failure():
    """Decorator catches exception, records failure metrics with elapsed time."""

def test_metrics_decorator_no_side_effects_on_return_value():
    """Return value is passed through unchanged."""
```

### Key invariant

Original behavior:
- Records `issue_number, step, success, elapsed_sec, model, output_chars`
- Uses `time.time()` at start and end of the block
- Records on ALL exit paths: success, exception (session mode), failure (stateless), timeout, FileNotFoundError
- Metrics collector may be `None` (guarded by `if _mt:`)

---

## 5. Dashboard `/api/log` Fix

**Trivial fix** — just needs the response body to match the route's apparent intent (serve log lines). A simple verification test:

```python
def test_api_log_returns_content():
    """GET /api/log returns 200 with non-empty body."""
```

---

## Test Infrastructure

### Fixtures

```python
# conftest.py

@pytest.fixture
def temp_git_repo(tmp_path):
    """Initialize a bare repo as origin + a clone as working copy."""
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "--bare", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "--allow-empty",
                    "-m", "initial"], check=True)
    subprocess.run(["git", "-C", str(work), "push"], check=True)
    return work

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
```

### Mock patterns

```python
# Mock the GitHub client
@pytest.fixture
def mock_gh(mocker):
    gh = mocker.patch("pdclaw._gh")
    gh.is_pdca_runner_comment.return_value = False
    return gh

# Mock subprocess for git operations
@pytest.fixture
def mock_subprocess(mocker):
    return mocker.patch("pdclaw.subprocess.run")

# Mock Claude CLI
@pytest.fixture
def mock_claude(mocker):
    run = mocker.patch("pdclaw.subprocess.run")
    run.return_value = mocker.Mock(
        returncode=0,
        stdout="## Generated files\n- Design.md: ...",
        stderr="",
    )
    return run
```

### Dependencies

Add to `requirements.txt` or a new `requirements-dev.txt`:
```
pytest>=7.0
pytest-mock>=3.10
```

---

## Test Execution

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=pdclaw --cov=pdca_memory --cov-report=term-missing

# Run specific test group
python -m pytest tests/ -k "test_push" -v
```

---

## Regression Checklist

Before merging the refactoring PR, verify:

- [ ] `test_push_to_local_branch` passes (git push helper)
- [ ] `test_stash_dirty_restores` passes (stash context manager)
- [ ] `test_plan_step_execution` passes (process_issue decomposition)
- [ ] `test_idempotent_poll` passes (no regressions in activity detection)
- [ ] `test_lifecycle_abort_sets_state` passes (lifecycle tag handling)
- [ ] `test_metrics_decorator_records_on_success` passes (metrics wrapper)
- [ ] Manual smoke test: run `python pdclaw.py --repo owner/repo --once` against a test repo
