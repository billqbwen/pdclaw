# Design Document — Issue #2: Review Code & Report Known Issues

## 1. Functional Design

### 1.1 Objective
Perform a comprehensive codebase audit of the PDClaw project to identify and document all **bugs, security risks, maintainability problems, and design weaknesses**. The output is a prioritized catalog of issues that can be addressed in subsequent PDCA cycles.

### 1.2 Scope
- All 6 Python modules: `pdclaw.py`, `pdca_memory.py`, `pdca_memory_cli.py`, `pdca_dashboard.py`, `pdca_metrics.py`, `pdca_claude_session.py`
- Configuration: `config.ini`, `.gitignore`
- Skill files under `skills/`
- Documentation: `MEMORY_SYSTEM.md`, `SESSION_MODE.md`
- Runtime artifacts committed to git (`.pdca_state/` files under `docs/`)

### 1.3 Out of Scope
- Feature requests or enhancements
- Refactoring implementation (covered by later PDCA cycles)
- Adding tests (covered by later PDCA cycles)

## 2. Architecture Design

No architectural changes are proposed — this is a reporting-only step. Findings are organized into an **Issue Catalog** that maps to specific modules and file locations for easy actionability.

## 3. Technical Design

### 3.1 Review Methodology
- **Static analysis** — manual reading of all Python source files
- **Dependency tracing** — follow data flow between modules to identify coupling issues
- **Git history analysis** — check `.gitignore` coverage, committed runtime artifacts
- **Configuration audit** — verify `config.ini` defaults and env-var wiring

### 3.2 Issue Categorization

| Category | Description | Examples |
|----------|-------------|----------|
| **Bug** | Incorrect behavior under specific conditions | Zombie processes, double metrics recording |
| **Security** | Unauthorized access, data leakage | Dashboard on 0.0.0.0, state files committed |
| **Resilience** | Crash or silent failure scenarios | Non-atomic file writes, missing backoff |
| **Maintainability** | High cognitive load, duplicated code, dead code | 2111-line engine, embedded HTML |
| **Reliability** | Degraded operation under stress | Unbounded JSONL growth, zero tests |

## 4. UI Design

No UI changes.

## 5. Other Considerations

### 5.1 Security
- Dashboard binds to `0.0.0.0` — internal metrics exposed to network
- Subprocess inherits full environment — `GITHUB_TOKEN` reachable by AI subprocess
- Session state files committed inside `docs/` directory tree — AI conversation prompts in version control

### 5.2 Performance
- `ai_calls.jsonl` grows without bound — no rotation or archival
- State file written on every poll cycle even when nothing changed

### 5.3 Observability
- AI call metrics are double-recorded on session-to-stateless fallback — corrupts timing statistics
- `/api/log` endpoint is a stub — always returns empty

### 5.4 Testing
- **Zero test coverage** across all modules. The state machine (`resolve_next_step`), activity detection (`has_new_activity`), tag parsing, git operations, and memory commands are all untested.

## 6. Issue Catalog (Detailed)

### HIGH Severity

| # | Issue | File | Lines | Impact |
|---|-------|------|-------|--------|
| H1 | Fragile `t0 in dir()` scope check | `pdclaw.py` | 910 | If `t0` unset before exception, `elapsed_sec` is silently 0; non-idiomatic |
| H2 | Double metric recording on session fallback | `pdclaw.py` | 849–1007 | AI call counted twice in metrics when session mode fails then falls back to stateless |
| H3 | Non-atomic state file writes | `pdclaw.py` | 664–667 | Crash mid-write produces truncated JSON, breaking next poll cycle |
| H4 | Non-atomic memory/issue file writes | `pdca_memory.py` | 75–77, 161–162 | Same as H3 for memory data |
| H5 | Hardcoded `.pdca_state` in `reset_session()` | `pdca_claude_session.py` | 230 | Custom `--state-dir` silently ignored; session files end up in wrong location |
| H6 | Zombie processes on subprocess timeout | `pdca_claude_session.py` | 182–183 | `TimeoutExpired` does not kill child; accumulated claude CLI processes leak |

### MEDIUM Severity

| # | Issue | File | Lines | Impact |
|---|-------|------|-------|--------|
| M1 | Stale tags passed to decision handler | `pdclaw.py` | 1600 | Uses unfiltered `tags` instead of `new_tags` — old `#deploy` comments can trigger unintended actions |
| M2 | Dashboard binds to `0.0.0.0` | `pdclaw.py`, `pdca_dashboard.py` | 2050, 481 | Internal metrics exposed on network |
| M3 | Subprocess inherits full environment | `pdclaw.py` | 924–928 | `GITHUB_TOKEN` passable to compromised AI response |
| M4 | Silent swallowing of git errors | `pdclaw.py` | 1033–1034 | `_find_changed_files` returns `[]` on git failure with no warning |
| M5 | `process_issue()` is ~430 lines | `pdclaw.py` | 1445–1883 | Untestable, high risk of regression |
| M6 | Duplicate git subprocess patterns | `pdclaw.py` | multiple | 3 independent implementations of the same git operations |
| M7 | Corrupted memory file not cleaned | `pdca_memory.py` | 141–145 | Failed JSON parse leaves corrupt file; next load fails again silently |
| M8 | CLI calls private `_save_*` methods | `pdca_memory_cli.py` | 46, 144 | Breaks encapsulation; changes to save logic break CLI |
| M9 | Embedded HTML/CSS/JS in raw strings | `pdca_dashboard.py` | 26–384 | No syntax checking, uneditable without custom tooling |
| M10 | Unbounded `ai_calls.jsonl` growth | `pdca_metrics.py` | 241–247 | File grows forever; no rotation needed for long-running instances |
| M11 | No GitHub API rate-limit backoff | `pdclaw.py` | 157–170 | 429/403 responses crash poll cycle; `X-RateLimit-Reset` unused |
| M12 | Session state files committed in `docs/` | `docs/` subtrees | multiple | AI conversation prompts leaked to git history |

### LOW Severity

| # | Issue | File | Lines | Impact |
|---|-------|------|-------|--------|
| L1 | Redundant state writes per poll | `pdclaw.py` | multiple | All-cycles-all-the-time serialization |
| L2 | Dead code: `get_step_from_tags()` | `pdclaw.py` | 272–282 | Unused function |
| L3 | Dead field: `frequent_issues` | `pdca_memory.py` | 66 | Initialized but never read |
| L4 | F-string in log defeats lazy eval | `pdca_memory.py` | 145 | Evaluated even when log suppressed |
| L5 | Bare `except: pass` in shutdown | `pdca_dashboard.py` | 510 | Shutdown failures invisible |
| L6 | `/api/log` returns empty | `pdca_dashboard.py` | 460–466 | Dead endpoint |
| L7 | Silent `except: pass` in metrics persistence | `pdca_metrics.py` | 247, 256, 262 | Write failures invisible |
| L8 | Global `_sessions` dict without locks | `pdca_claude_session.py` | 203–240 | Race condition if concurrency added |
| L9 | Overbroad meaningful-input regex | `pdclaw.py` | 344–349 | Single words like "yes"/"no" classified as meaningful |
| L10 | `.gitignore` does not cover `.pdca/` root | `.gitignore` | 12–14 | New files under `.pdca/` would be tracked |

## 7. Outstanding Questions

1. **Should `ai_calls.jsonl` be rotated by line count, file age, or file size?** Depends on expected run duration.
2. **Should the CI pipeline remain as `py_compile` only, or should actual test execution be added?** Adding `pytest` would require a test runner dependency and test files.
3. **What is the migration strategy for embedded HTML dashboard strings?** Extract to Jinja2 templates, or keep embedded but add a build step?
4. **Should the metrics collector schema be versioned?** No version field exists in `summary.json` schema.
