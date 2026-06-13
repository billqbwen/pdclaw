# Design.md — PDClaw Code Review

## Functional Design

### Missing Skill Files (Critical Blocker)

**Location**: `/Users/billwen/swd/pdclaw/skills/` (empty directory)

The `skills/` directory is completely empty. The core engine needs four skill files:

- `skills/pdca-plan.md`
- `skills/pdca-do.md`
- `skills/pdca-check.md`
- `skills/pdca-act.md`

When `_read_skill()` (pdclaw.py, line 747-759) encounters a missing skill file, it logs a warning and returns an empty string. This means the AI runs without step-specific instructions. The AI model receives only the generic prompt built in `run_skill()` (lines 825-846), which provides issue context but no structured guidance on what each PDCA step should produce or how to generate the required files.

The skill files are intended to be loaded via `SKILL_DIR` (line 78, resolved at line 1915) and injected into the prompt via `--append-system-prompt` (line 921 for stateless mode, line 133 in pdca_claude_session.py for session mode). Without them, the AI has no guardrails.

**Impact**: The AI works but produces inconsistent output. Each step lacks domain-specific instructions about file format expectations, content requirements, and quality standards.

### State File Corruption Risk

**Location**: pdclaw.py, `save_state()` (lines 664-667)

```python
def save_state(state_dir: Path, issue_number: int, state: dict) -> None:
    state_file = state_dir / str(issue_number) / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True))
```

The write is not atomic. If the process is killed mid-write (SIGKILL, power loss, disk full), `state.json` will contain a truncated or corrupted JSON document. The next poll cycle will fail when `json.loads()` (line 652) attempts to parse it.

The same pattern exists in:
- `pdca_claude_session.py`, `ClaudeSession._save_session()` (line 66): `self.session_file.write_text(...)` — no atomic write.
- `pdca_metrics.py`, `MetricsCollector._append_jsonl()` (lines 241-246): appends to `ai_calls.jsonl` with `open(path, "a")` — truncation risk is lower here (append is less vulnerable), but concurrent writes from the dashboard read path are not coordinated.

**Recommendation**: Use write-to-temp-then-rename pattern for `state.json` and session files:

```python
import tempfile
tmp = path.with_suffix(".tmp")
tmp.write_text(data)
tmp.rename(path)
```

### Unbounded ai_calls.jsonl Growth

**Location**: pdca_metrics.py, `_append_jsonl()` (lines 241-247)

Every AI call is appended to `ai_calls.jsonl` in the metrics directory. There is no rotation, archival, or truncation mechanism. Over days or weeks of operation, this file grows without bound. The `snapshot()` method (lines 173-216) only reads the last 20 entries into memory, so dashboard performance is not affected, but the file itself becomes a disk space concern and a problem for any tooling that reads the full file.

**Recommendation**: Implement JSONL rotation — archive entries when the file exceeds 1000 lines, or timestamp-rotate daily.

### Hardcoded .pdca_state Path in reset_session()

**Location**: pdca_claude_session.py, `reset_session()` (line 230)

```python
session_file = Path(".pdca_state") / str(issue_number) / "claude_session.json"
```

This hardcodes `.pdca_state` as a relative path. The main engine uses the `--state-dir` config (resolved from `config.ini` or CLI argument at `pdclaw.py` line 1952-1955, `state_dir = Path(args.state_dir)`). But `reset_session()` does not receive the configured state directory. It constructs its own path from the CWD.

This means `reset_session()` may:
- Fail to find session files placed at the configured state directory.
- Create duplicate session files at the wrong location.
- Only function correctly when `--state-dir` is the default `.pdca_state` and CWD matches the expected root.

**Recommendation**: Pass `state_dir` into `reset_session()` as a parameter instead of constructing the path internally.

### .gitignore Coverage for State Files

**Location**: `/Users/billwen/swd/pdclaw/.gitignore` (lines 12-14)

The `.gitignore` already covers:
- `.pdca/memory/`
- `.pdca/metrics/`
- `.pdca_state/`

This is correct. The analysis template's concern about missing `.gitignore` entries is not applicable — the state directories are excluded.

### No Unit Tests

There are no test files anywhere in the repository. The test runner infrastructure is absent (no `pytest.ini`, no `tests/` directory, no test dependencies in any configuration). This means:

- Regression detection is entirely manual.
- The complex tag resolution logic in `resolve_next_step()` (pdclaw.py, lines 285-325) has no automated verification.
- Git operations (`ensure_pdca_branch`, `git_commit_and_push`, `_handle_decision_tag`) cannot be validated without running against a real repository.
- The activity detection logic (`has_new_activity`, `is_meaningful_comment`, `get_new_human_comments`) is untested despite having many branching paths.

### Fragile t0 Variable Safety

**Location**: pdclaw.py, line 910

```python
elapsed_sec=time.time() - t0 if 't0' in dir() else 0,
```

This pattern checks whether `t0` exists in the local scope by testing `'t0' in dir()`. This is fragile because:

- `dir()` returns all names in the current scope, including builtins, globals, and locals. If any other variable coincidentally named `t0` exists, it returns a false positive.
- If the `try` block (lines 850-899) initializes `t0` at line 854 and then fails before line 854, `t0` will not exist in the `except` handler (lines 901-913). If it fails after line 854 but before the metric recording at lines 868-877, `t0` exists but the elapsed time is meaningless.
- The pattern is an implicit boolean check on a string membership test, which is non-idiomatic Python.

**Recommendation**: Replace with a proper `try/finally` pattern:

```python
t0 = None
try:
    t0 = time.time()
    ...
finally:
    if t0 is not None:
        elapsed = time.time() - t0
        ...
```

### Double Metric Recording on Session Failure

**Location**: pdclaw.py, `run_skill()` (lines 849-917 for session mode, lines 918-1007 for stateless fallback)

When session mode execution fails:
1. Metrics are recorded for the failed session call (lines 904-913).
2. The stateless fallback runs (line 916: `use_session = False`).
3. Metrics are recorded again for the stateless call (lines 950-959).

This means the same logical AI invocation is counted twice. If the session call fails and the stateless call succeeds, the metrics show one failure and one success for what was intended as one operation. If both fail, the metrics show two failures.

**Recommendation**: Either remove the stateless fallback entirely (if session mode is the only intended path) or add a flag to suppress the second metric recording when in fallback mode.

### git_commit_and_push Returns False on Push Failure After Local Commit

**Location**: pdclaw.py, `git_commit_and_push()` (lines 1038-1116)

The function stages, commits, and pushes in one operation. If the commit succeeds (line 1062-1073) but the push fails (lines 1087-1114), the function returns `False`. The caller at line 1788 receives `push_ok = False`, but the local commit already happened. The working tree is now in a state where the commit exists locally but was never pushed.

Additionally, line 1793-1794 generates version links conditionally on `push_ok`. When push fails, there are no version links in the GitHub comment, but the comment still confirms the step completed. This is misleading — the files exist locally but are not on GitHub.

**Recommendation**: Split into `git_commit()` and `git_push()` phases so the caller can reason about each independently. At minimum, add a retry mechanism for push failures.

---

## Architecture Design

### Skills Directory

The architecture implicitly depends on skill files to guide AI behavior per PDCA step. The empty `skills/` directory means this architectural layer is missing. When designing the skill file format, consider:

- Each skill file should have a YAML frontmatter block with metadata (step name, expected output files, required agent roles).
- The body should contain step-specific system prompts that guide the AI on what to produce, format expectations, and quality criteria.
- Template files should be created as a starting point, even if the user intends to customize them later.

### Git Operations: Consider Builder/Strategy Pattern

The `git_commit_and_push()` function (lines 1038-1116) and `ensure_pdca_branch()` (lines 1378-1442) and `_handle_decision_tag()` (lines 1226-1375) all contain inline `subprocess.run()` calls with duplicated error handling patterns. The git operations have grown organically into a single large function with branching logic for push failures, upstream setup, and dirty worktree detection.

Consider extracting git operations into a `GitOperator` class or module with methods like:
- `create_branch(name, base)` / `checkout_branch(name)` / `branch_exists(name)`
- `commit(files, message)` / `push(branch)` / `push_with_upstream(branch)`
- `stash()` / `pop()` / `is_dirty()`
- `merge(source, target)` / `delete_branch(name)`

This would reduce duplication and make the git operations testable by allowing a mock git backend during tests.

### Session Manager vs. Stateless Fallback Complexity

The `run_skill()` function now has two code paths:
- **Session mode** (lines 849-916): Uses `ClaudeSession` to maintain conversation history across poll cycles.
- **Stateless mode** (lines 918-1007): Falls back to a single-shot `subprocess.run()` call.

The session manager (pdca_claude_session.py) already implements the richer functionality: history tracking, context injection, conversation persistence. The stateless path duplicates much of this (metric recording, conversation log saving, memory command parsing, error handling).

If session mode is the intended production path, consider removing the stateless fallback entirely. The `--no-session` flag (line 1986) could be deprecated. This would:
- Reduce the codebase size by ~90 lines.
- Eliminate the double-metric-recording bug.
- Simplify the execution model to a single path.

---

## Technical Design

### Atomic File Writes

Add a utility function for atomic writes to `pdclaw.py`:

```python
def _atomic_write(path: Path, data: str) -> None:
    """Write data atomically using temp-file-and-rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data)
    tmp.rename(path)
```

Replace the direct `write_text()` calls in:
- `save_state()` (pdclaw.py, line 667)
- `ClaudeSession._save_session()` (pdca_claude_session.py, line 66)
- `MetricsCollector.save_summary()` (pdca_metrics.py, line 261)

### JSONL Log Rotation

Add rotation to `MetricsCollector._append_jsonl()` (pdca_metrics.py, line 241). A rotation policy could be:

- Keep at most 1000 entries per JSONL file.
- When the 1001st entry is appended, rename the current file to `ai_calls.1.jsonl` and start a new file.
- Keep at most N archives (e.g., 5), then start overwriting the oldest.

Alternatively, rotate by timestamp (daily) and let external tooling handle archival.

### Centralize .pdca_state Path Resolution

The `ClaudeSession.__init__()` (pdca_claude_session.py, line 36) already receives a `work_dir` parameter and constructs the session file path relative to it. The problem is specifically in the module-level `reset_session()` function (line 223) and its fallback file cleanup (line 230).

Fix: Add a `state_dir` parameter to `reset_session()`:

```python
def reset_session(issue_number: int, state_dir: Optional[Path] = None) -> None:
    ...
    if state_dir:
        session_file = state_dir / str(issue_number) / "claude_session.json"
    else:
        session_file = Path(".pdca_state") / str(issue_number) / "claude_session.json"
```

Then update the call sites in `pdclaw.py` (lines 817, 1191, 1211, 1324, 1480) to pass `state_dir` when available.

### SkillLoader Class

Replace `_read_skill()` (pdclaw.py, lines 747-759) with a `SkillLoader` class:

```python
class SkillLoader:
    def __init__(self, skill_dir: Path):
        self.skill_dir = Path(skill_dir)
    
    def load(self, skill_name: str) -> str:
        """Load a skill file, stripping YAML frontmatter. Returns empty string if missing."""
        skill_file = self.skill_dir / f"{skill_name}.md"
        if not skill_file.exists():
            log.warning("Skill file not found: %s", skill_file)
            return ""
        content = skill_file.read_text()
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        return content
```

This encapsulation makes it easier to add caching, fallback defaults, or validation in the future.

### Replace t0-in-dir with try/finally

In `run_skill()` (pdclaw.py), restructure the session execution block to use a proper `try/finally` pattern. See "Fragile t0 Variable Safety" in Functional Design above for the specific recommendation.

### Metrics Deduplication

Add a `skip_metric` parameter or an internal flag to `run_skill()` to prevent double-recording when operating in fallback mode. Alternatively, restructure the function so that the stateless fallback path reuses the same `t0` and records metrics only once at the end of the outer function.

### Split git_commit_and_push

Split `git_commit_and_push()` (pdclaw.py, lines 1038-1116) into two phases:

```python
def git_commit(files: list[Path], message: str, repo_root: Path) -> tuple[bool, str]:
    """Stage and commit. Returns (success, sha_or_error)."""
    ...

def git_push(repo_root: Path) -> tuple[bool, str]:
    """Push current branch. Returns (success, error_message)."""
    ...
```

This allows the caller to:
1. Commit locally.
2. Try to push.
3. If push fails, retry or report the partial state accurately.

---

## UI Design

### Dashboard Binds to 0.0.0.0

**Location**: pdclaw.py, line 2050:
```python
_dashboard = start_dashboard(
    host="0.0.0.0",
    ...
)
```

And in pdca_dashboard.py, `DashboardServer.__init__()` (line 481) and `start_dashboard()` (line 518):
```python
host: str = "0.0.0.0",
```

Binding to `0.0.0.0` exposes the dashboard to all network interfaces, including external ones. For a local developer tool, this is unnecessary. If the tool runs on a shared machine or cloud VM, it exposes internal state (issue numbers, AI call timing, step success rates) to the network.

**Recommendation**: Change the default to `127.0.0.1`. Add documentation about the `--dashboard-port` option for users who need remote access. The dashboard is already a read-only monitoring tool, but binding to localhost reduces the attack surface.

### Minor: Overall Dashboard Quality

The dashboard is functional and well-structured:
- Clean HTML with inline CSS, no external dependencies.
- Proper REST API endpoints for JSON data.
- Auto-refresh every 5 seconds.
- Issue detail page with PDCA step progress visualization.
- No JavaScript framework overhead.

No significant changes needed beyond the binding address.

---

## Other Considerations

### Security: Dashboard Binding

See "UI Design" above. The default binding to `0.0.0.0:9191` should be `127.0.0.1:9191` for a local developer tool.

### Performance: Redundant State File Writes

`save_state()` is called on every poll cycle even when nothing has changed. For example, in `process_issue()`:

- Lines 1643-1646: Called when no step to execute, just updating `last_check`.
- Lines 1664-1667: Called when no new activity detected.
- Lines 1699-1702: Called when skipping AI execution.

On a repo with many open issues, this means N disk writes per poll cycle (where N = number of issues), even in a steady state where no issue has new activity. Each write serializes the entire state dict to JSON.

**Recommendation**: Add a dirty-flag mechanism to `save_state()` — only write to disk if the state actually changed since the last write. Or compare the JSON output before writing.

### Observability: Structured Logging

The current logging uses standard Python `logging` with plain-text format (pdclaw.py, lines 2012-2016). This works well for human reading but is harder to integrate with log aggregation tools (ELK, Datadog, etc.).

**Recommendation**: Consider adding an optional structured log format (JSON lines). This could be controlled by an environment variable or CLI flag. The key fields per log line would be: `timestamp`, `level`, `logger`, `message`, and structured data (issue_number, step, elapsed_sec, etc.).

### Maintainability: TAG_RE Complexity

**Location**: pdclaw.py, lines 82-87:

```python
TAG_RE = re.compile(
    r"#(pdca-start|plan-approved|do-approved|check-approved|act-approved|"
    r"pdca-refresh|pdca-abort|pdca-close|pdca-skip|pdca-new-session|"
    r"pdca-reset|deploy|fix|fallback)\b",
    re.IGNORECASE,
)
```

This regex covers 13 tag variants in a single pattern. The `re.IGNORECASE` flag creates potential for false positives (e.g., `#DEPLOY` and `#deploy` both match). The `\b` word boundary at the end can cause missed matches if punctuation follows (e.g., `#plan-approved,` — the comma is a word boundary break, so this would match, but `#plan-approved-extra` would also match, which is undesirable).

**Recommendation**: Consider a dedicated `TagParser` class with:
- A set of known tag constants.
- Individual compiled regex patterns per tag for clarity.
- Case-sensitive matching by default (tags should be lowercase).
- A public API: `TagParser.parse(text) -> set[str]`.

### No GitHub API Rate-Limit Backoff

**Location**: pdclaw.py, `_GitHubClient._request()` (lines 157-170)

The client tracks rate-limit headers:
```python
remaining = resp.headers.get("X-RateLimit-Remaining")
if remaining is not None and int(remaining) < 10:
    log.warning("GitHub API rate limit nearly exhausted: %s remaining", remaining)
```

But there is no backoff mechanism. When rate limits are exhausted (HTTP 403 or 429), the `resp.raise_for_status()` at line 169 will raise an exception, causing the current poll cycle to fail for all issues. The main loop catches `requests.RequestException` at line 2080 and continues to the next cycle, which will fail again.

**Recommendation**: Add exponential backoff on 429/403 responses. Track the `X-RateLimit-Reset` header to know when to resume. Consider adding a sleep-before-retry mechanism that respects the rate-limit window.

### Base-URL Defaults to api.deepseek.com

**Location**: pdclaw.py, lines 79, 122; config.ini, line 23

```python
CLAUDE_BASE_URL = "https://api.deepseek.com/anthropic"
```

And in config.ini:
```ini
base_url = https://api.deepseek.com/anthropic
```

This points to DeepSeek's Anthropic-compatible proxy, not to Anthropic's own API. This is a deployment-specific default buried in code and config. A user expecting a standard Claude setup will get confusing errors.

**Recommendation**: Either:
- Default to the official Anthropic API endpoint (`https://api.anthropic.com`) and document that DeepSeek users can override via config.
- Or add a comment explaining why `api.deepseek.com` is the default and how to switch.
- Or make the default empty (no base URL override) so the Claude CLI uses its own configured endpoint.

---

## Outstanding Questions

1. **Skill file creation**: Should the four skill files (`pdca-plan.md`, `pdca-do.md`, `pdca-check.md`, `pdca-act.md`) be created as part of this issue, or is the intent purely to report the finding? Creating template files would immediately fix the #1 blocker.

2. **Priority of enhancement categories**:
   - **Correctness** (atomic writes, path config) — prevents data loss and runtime errors.
   - **Completeness** (skill files) — is the #1 functional blocker.
   - **Quality** (tests, refactoring) — improves maintainability but doesn't fix immediate bugs.
   
   Which category should be prioritized?

3. **Stateless fallback deprecation**: Should the stateless mode in `run_skill()` (pdclaw.py, lines 918-1007) be removed now that session mode is the default and stable? This would simplify the code but remove the escape hatch.

4. **Decision phase correctness**: The decision phase (#Deploy/#Fix/#Fallback) was added after the Check step. It is currently working, but the act step completion path (`state["phase"] = "decision"` at line 1849) creates an additional state dimension not originally modeled. Is the three-way decision (Deploy/Fix/Fallback) working as expected, or does it need refinement for production use?

5. **Additional .gitignore entries**: `.pdca/` is not in `.gitignore` (only `.pdca/memory/` and `.pdca/metrics/` are). If other tools or future code creates files directly under `.pdca/`, they would not be excluded. Should `.pdca/` be added to `.gitignore`?

<!-- memory:issue:add_decision step="plan" decision="Completed comprehensive code review of PDClaw across all 6 Python modules" -->
<!-- memory:issue:add_todo item="Determine which enhancement category to prioritize: correctness, completeness, or quality" -->
<!-- memory:issue:add_todo item="Address the missing skill files (skills/pdca-*.md) — the #1 blocker" -->
<!-- memory:issue:add_todo item="Fix atomic file writes in save_state() and _save_session()" -->
<!-- memory:issue:add_todo item="Fix hardcoded .pdca_state path in reset_session()" -->
<!-- memory:issue:add_todo item="Resolve double metric recording on session→stateless fallback" -->
<!-- memory:issue:set_context key="primary_module" value="pdclaw.py" -->
<!-- memory:global:add_lesson lesson="Always use atomic writes (tmp+rename) for JSON state files that could be read mid-write" issue_ref="#1" -->
<!-- memory:global:add_lesson lesson="When implementing fallback paths in AI execution, ensure metrics are recorded once per logical operation, not per code path" issue_ref="#1" -->
<!-- memory:global:add_lesson lesson="File-based session paths must use the configured state directory, not hardcoded defaults, to support custom --state-dir" issue_ref="#1" -->
