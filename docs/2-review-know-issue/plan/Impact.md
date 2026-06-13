# Impact Document — Issue #2: Review Code & Report Known Issues

This is a **reporting-only** step. No code will be modified. The following files are identified as requiring changes in **subsequent PDCA cycles** to address the discovered issues.

## Module: Core Engine (pdclaw.py)

- `pdclaw.py` — lines 157–170: Add GitHub API rate-limit backoff/retry
- `pdclaw.py` — line 272–282: Remove dead code `get_step_from_tags()`
- `pdclaw.py` — lines 344–349: Narrow `MEANINGFUL_INPUT_PATTERNS` regex
- `pdclaw.py` — lines 664–667: Replace `write_text()` with atomic write (tempfile + rename)
- `pdclaw.py` — lines 849–1007: Fix double metric recording on session fallback
- `pdclaw.py` — line 910: Replace `t0 in dir()` with proper `try/finally`
- `pdclaw.py` — lines 924–928: Construct minimal env for subprocess
- `pdclaw.py` — lines 1033–1034: Remove silent `except Exception: pass`
- `pdclaw.py` — lines 1226–1375: Extract git operations into shared helper
- `pdclaw.py` — lines 1378–1442: Extract branch management into shared helper
- `pdclaw.py` — lines 1445–1883: Refactor `process_issue()` into smaller functions
- `pdclaw.py` — line 1600: Fix stale tag handling in decision phase
- `pdclaw.py` — line 2050: Change dashboard default host to `127.0.0.1`

## Module: Memory System (pdca_memory.py)

- `pdca_memory.py` — lines 75–77, 161–162: Make `_save_global()` and `_save_issue()` atomic
- `pdca_memory.py` — line 66: Remove dead `frequent_issues` field
- `pdca_memory.py` — lines 141–145: Clean corrupted file on JSON decode error
- `pdca_memory.py` — line 145: Use `%s`-style lazy logging

## Module: Memory CLI (pdca_memory_cli.py)

- `pdca_memory_cli.py` — lines 46, 144: Add public save API instead of calling private `_save_*` methods

## Module: Dashboard (pdca_dashboard.py)

- `pdca_dashboard.py` — lines 26–384: Extract embedded HTML/CSS/JS into separate file(s)
- `pdca_dashboard.py` — line 393: Implement or remove `/api/log` endpoint
- `pdca_dashboard.py` — line 481: Default host to `127.0.0.1`
- `pdca_dashboard.py` — line 510: Add proper error logging in shutdown

## Module: Metrics Collector (pdca_metrics.py)

- `pdca_metrics.py` — lines 241–247: Add rotation or size cap for `ai_calls.jsonl`
- `pdca_metrics.py` — lines 247, 256, 262: Remove silent `except: pass`

## Module: Session Manager (pdca_claude_session.py)

- `pdca_claude_session.py` — lines 182–183: Kill child process on `TimeoutExpired`
- `pdca_claude_session.py` — line 230: Accept `state_dir` parameter instead of hardcoded `.pdca_state`

## Configuration

- `config.ini` — no changes needed
- `.gitignore` — add `.pdca/` root and `docs/*/.pdca_state/` patterns

## Infrastructure

- No CI/CD changes needed (testing will be addressed in a future PDCA cycle)

## Files to Create (Future PDCA Cycles)

- `tests/` directory, `pytest.ini`, and initial test files for:
  - State machine logic (`resolve_next_step`)
  - Activity detection (`has_new_activity`)
  - Tag parsing (`parse_tags_from_comment`)
  - Memory command parsing (`parse_memory_commands`)
  - Git helper utilities
