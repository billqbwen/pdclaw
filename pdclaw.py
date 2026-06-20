#!/usr/bin/env python3
"""
PDClaw — GitHub Issue PDCA Cycle Automation

Polls GitHub issues for PDCA tags and executes Plan-Do-Check-Act cycles.

Tags in GitHub issue bodies:
  #pdca-start                — start PDCA cycle (triggers Plan step)
  #plan-approved             — approve Plan output, advance to Do step
  #do-approved               — approve Do output, advance to Check step
  #check-approved            — approve Check output, enter decision phase
  #deploy                    — (decision phase) merge changes into target branch
  #fix                       — (decision phase) reset to Do step for fixes
  #fallback                  — (decision phase) revert all changes
  #pdca-refresh              — re-run the current step
  #pdca-abort                — stop processing this issue
  #pdca-close                — close the GitHub issue
  #pdca-skip                 — mark issue as skipped

Plan step flow:
  1. Detect new issues OR new comments from the issue assignee
  2. If #pdca-start tag is present, consolidate issue body + all comments
  3. Pass consolidated context to Claude Code for analysis
  4. Generate Design.md and Impact.md in /docs/<issue#>-<title>/plan/
  5. Git commit and push the generated files

Usage:
  export GITHUB_TOKEN=ghp_...
  pdclaw --repo owner/repo [--interval 180] [--auto-run]
  pdclaw --issue <issue-url> [--interval 180] [--auto-run] [--once]
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests

from pdca_memory import PDCAMemory
from pdca_claude_session import get_session, reset_session, clear_all_sessions
from pdca_metrics import init_metrics, get_metrics
from pdca_dashboard import start_dashboard

log = logging.getLogger("pdclaw")


# ── Constants ────────────────────────────────────────────────────────────────

PDCA_STEPS = ("plan", "do", "check", "act")
LIFECYCLE_TAGS = frozenset({"#pdca-refresh", "#pdca-abort", "#pdca-close", "#pdca-skip", "#pdca-new-session"})

STEP_FILES: dict[str, list[str]] = {
    "plan": ["Design.md", "Impact.md"],
    "do": ["Change.md"],
    "check": ["Review.md", "Test.md"],
    "act": ["Decision.md", "CodeDiff.md"],
}

SKILL_NAMES: dict[str, str] = {
    "plan": "pdca-plan",
    "do": "pdca-do",
    "check": "pdca-check",
    "act": "pdca-act",
}

# Configurable defaults — overridden by config.ini and CLI args
DEPLOY_BRANCH = "main"
SKILL_DIR = Path(__file__).parent / "skills"
CLAUDE_BASE_URL = "https://api.deepseek.com/anthropic"
CLAUDE_MODEL = "deepseek-v4-flash"

TAG_RE = re.compile(
    r"#(pdca-start|plan-approved|do-approved|check-approved|act-approved|"
    r"pdca-refresh|pdca-abort|pdca-close|pdca-skip|pdca-new-session|"
    r"pdca-reset|deploy|fix|fallback)\b",
    re.IGNORECASE,
)

NEW_SESSION_RE = re.compile(r"/new-refresh\b", re.IGNORECASE)

GH_API_BASE = "https://api.github.com"

# Memory system
_memory: PDCAMemory | None = None

# ── Runtime state ────────────────────────────────────────────────────────────

_running = True


def load_config(script_dir: Path) -> configparser.ConfigParser:
    """Load config.ini with defaults, returning a ConfigParser.

    Looks for ``config.ini`` in *script_dir*.  All values have built-in
    defaults so the file is entirely optional.  Returns a ``ConfigParser``
    whose sections can be queried with ``.get()`` / ``.getint()``.
    """
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "runner": {
            "deploy_branch": "main",
            "interval": "180",
        },
        "paths": {
            "work_dir": ".",
            "state_dir": ".pdca_state",
            "memory_dir": ".pdca/memory",
            "skills_dir": "skills",
        },
        "ai": {
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com/anthropic",
        },
    })
    config_path = script_dir / "config.ini"
    if config_path.is_file():
        log.info("Config: loading %s", config_path)
        cfg.read(str(config_path))
    else:
        log.info("Config: no config.ini found at %s, using defaults", config_path)
    return cfg


def _handle_signal(signum: int, _frame) -> None:
    log.info("Received signal %d, shutting down gracefully...", signum)
    global _running
    _running = False


# ── GitHub API client ────────────────────────────────────────────────────────


class _GitHubClient:
    """Thin client wrapping the GitHub REST API with rate-limit awareness."""

    @staticmethod
    def _headers() -> dict[str, str]:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request(self, path: str) -> requests.Response:
        """Make a GET request and check for common error codes."""
        url = f"{GH_API_BASE}{path}"
        resp = requests.get(url, headers=self._headers(), timeout=30)

        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) < 10:
            log.warning("GitHub API rate limit nearly exhausted: %s remaining", remaining)
        if resp.status_code == 404:
            raise SystemExit(f"Resource not found: {url}")
        if resp.status_code == 401:
            raise SystemExit("GitHub API authentication failed. Check your GITHUB_TOKEN.")
        resp.raise_for_status()
        return resp

    def get_dict(self, path: str) -> dict:
        resp = self._request(path)
        data = resp.json()
        _normalize_labels(data)
        return data

    def get_list(self, path: str) -> list:
        resp = self._request(path)
        data = resp.json()
        for item in data:
            if isinstance(item, dict):
                _normalize_labels(item)
        return data

    def get_issue(self, owner: str, repo: str, number: int) -> dict:
        return self.get_dict(f"/repos/{owner}/{repo}/issues/{number}")

    def get_issue_comments(self, owner: str, repo: str, number: int) -> list[dict]:
        """List all comments on an issue, paginating through all results."""
        all_comments: list[dict] = []
        page = 1
        while True:
            items = self.get_list(
                f"/repos/{owner}/{repo}/issues/{number}/comments?per_page=100&page={page}"
            )
            if not items:
                break
            all_comments.extend(items)
            if len(items) < 100:
                break
            page += 1
        log.info("[API] Fetched %d comments for issue #%d (pages=%d)", len(all_comments), number, page)
        return all_comments

    def list_open_issues(self, owner: str, repo: str) -> list[dict]:
        """List open issues, paginating through all results."""
        all_issues: list[dict] = []
        page = 1
        while True:
            items = self.get_list(
                f"/repos/{owner}/{repo}/issues?state=open&per_page=100&page={page}"
            )
            if not items:
                break
            all_issues.extend(i for i in items if "pull_request" not in i)
            if len(items) < 100:
                break
            page += 1
        return all_issues

    def add_comment(self, owner: str, repo: str, number: int, body: str, include_marker: bool = True) -> None:
        """Add a comment to a GitHub issue. Includes a hidden marker to identify runner comments."""
        if include_marker:
            body = f"{body}\n\n{PDCA_RUNNER_MARKER}"
        url = f"{GH_API_BASE}/repos/{owner}/{repo}/issues/{number}/comments"
        resp = requests.post(url, headers=self._headers(), json={"body": body}, timeout=30)
        resp.raise_for_status()

    def close_issue(self, owner: str, repo: str, number: int) -> None:
        url = f"{GH_API_BASE}/repos/{owner}/{repo}/issues/{number}"
        resp = requests.patch(url, headers=self._headers(), json={"state": "closed"}, timeout=30)
        resp.raise_for_status()

    def check_auth(self) -> bool:
        """Verify GitHub API authentication."""
        try:
            self.get_dict("/user")
            return True
        except Exception:
            return False

    def is_pdca_runner_comment(self, comment: dict) -> bool:
        """Check if a comment was added by PDClaw itself.

        Uses the hidden HTML comment marker — the author check is intentionally
        omitted because PDClaw may run under the same GitHub account as the
        human providing input, which would falsely filter out human comments.
        """
        body = comment.get("body") or ""
        return PDCA_RUNNER_MARKER_PATTERNS.search(body) is not None


def _normalize_labels(obj: dict) -> None:
    """Convert label objects to label name strings in-place."""
    if "labels" in obj:
        obj["labels"] = [lb["name"] for lb in (obj["labels"] or [])]


# Module-level client singleton
_gh = _GitHubClient()


# ── Tag detection ────────────────────────────────────────────────────────────


def get_tags(text: str) -> set[str]:
    """Extract all matching tags (lowercase with # prefix)."""
    return {f"#{m.group(1).lower()}" for m in TAG_RE.finditer(text)}


def resolve_next_step(tags: set[str], completed_steps: list[str]) -> str | None:
    """Determine which PDCA step to execute based on approval chain and state.

    Semantics:
      #pdca-start          → execute Plan (initial trigger)
      #plan-approved       → Plan confirmed by user; advance to Do
      #do-approved         → Do confirmed by user; advance to Check
      #check-approved      → Check confirmed by user; advance to Act
                            (intercepted by check-review phase handler;
                             use #Deploy/#Fix/#Fallback in decision phase)

    Each -approved tag implies the approved step should run first (if it hasn't
    yet), then advance to the next step.  Sequential ordering is enforced: a
    step only runs if all predecessors are done.

    NOTE: #check-approved is intercepted by the check-review phase handler
    (in process_issue) before this function is reached.  If this function IS
    reached with #check-approved (e.g. backward-compat or edge case), it
    resolves to "act" only when all predecessors are completed.
    """
    confirmed_order: list[str] = []

    # #pdca-start maps to plan; guard against duplicating when #plan-approved is also present
    if "#pdca-start" in tags and "#plan-approved" not in tags:
        confirmed_order.append("plan")

    if "#plan-approved" in tags:
        if "plan" not in confirmed_order:
            confirmed_order.append("plan")
        confirmed_order.append("do")

    if "#do-approved" in tags:
        if "do" not in confirmed_order:
            confirmed_order.append("do")
        confirmed_order.append("check")

    # #check-approved advances to act, BUT the check-review phase handler
    # (in process_issue) will intercept this tag first and transition to
    # the decision phase rather than jumping straight to the act step.
    if "#check-approved" in tags:
        if "check" not in confirmed_order:
            confirmed_order.append("check")
        confirmed_order.append("act")

    # First uncompleted step in the confirmed chain is what to execute
    for step in confirmed_order:
        if step not in completed_steps:
            # Verify all predecessors are done (sequential guard)
            step_idx = PDCA_STEPS.index(step)
            for i in range(step_idx):
                if PDCA_STEPS[i] not in completed_steps:
                    return None  # predecessor not done — can't skip ahead
            return step

    return None


def issue_text(issue: dict) -> str:
    """Combine body and labels into one searchable string."""
    parts = [issue.get("body") or ""]
    parts.extend(issue.get("labels") or [])
    return " ".join(parts)


# ── Activity detection ───────────────────────────────────────────────────────


# Unique marker added to all comments created by PDClaw
PDCA_RUNNER_MARKER = "<!-- pdclaw -->"

# Pattern to detect PDClaw comments by the marker
PDCA_RUNNER_MARKER_PATTERNS = re.compile(r"<!-- pdclaw -->", re.IGNORECASE)

# Comments starting with these prefixes will NOT be processed even if they contain tags
# Use to discuss tags without triggering actions: "what about #pdca-refresh?"
IGNORE_COMMENT_PATTERNS = re.compile(r"^\s*(\[skip\]|<!--|noreview:|no-action:|#\s*no-trigger)", re.IGNORECASE)

# Minimum content length to consider as meaningful (avoid empty/very short comments)
MIN_COMMENT_LENGTH = 10




def issue_content_changed(state: dict, issue: dict) -> bool:
    """Check if the issue body or labels actually changed since last check.

    GitHub's updated_at changes for any edit, but we only care if
    the content we track (body, labels) actually changed.
    """
    stored_body = state.get("last_issue_body")
    current_body = issue.get("body") or ""

    stored_labels = state.get("last_issue_labels")
    current_labels = issue.get("labels", [])

    # If no previous snapshot, no change detected (will be saved after this check)
    if stored_body is None and stored_labels is None:
        return False

    # Compare body if we have a stored snapshot
    if stored_body is not None and stored_body != current_body:
        return True

    # Compare labels if we have a stored snapshot
    if stored_labels is not None and set(stored_labels) != set(current_labels):
        return True

    return False


def store_issue_snapshot(state: dict, issue: dict) -> None:
    """Store current issue content for future comparison."""
    state["last_issue_body"] = issue.get("body") or ""
    state["last_issue_labels"] = list(issue.get("labels", []))


def get_new_human_comments(
    state: dict,
    comments: list[dict],
) -> tuple[list[dict], str]:
    """Get all new human comments since last check.

    Returns (new_comments, latest_human_author).
    Only returns comments that:
    - Are not from PDClaw (has our marker)
    - Are not from known bot accounts
    - Are new (created after last_check timestamp)
    """
    last_check = state.get("last_check")
    last_ts = _parse_ts(last_check) if last_check else None

    new_human_comments = []
    latest_author = ""

    for c in comments:
        # Skip runner's own comments
        if _gh.is_pdca_runner_comment(c):
            continue

        author = c.get("user", {}).get("login", "")

        # Skip known bot accounts
        if author.endswith("[bot]") or author in ("web-flow", "github-actions[bot]"):
            continue

        created_at = c.get("created_at", "")
        if not created_at:
            continue

        # Skip comments older than last check
        if last_ts and _parse_ts(created_at) <= last_ts:
            continue

        new_human_comments.append(c)
        latest_author = author  # Keep updating to get the latest

    # Filter out ignored comments (those with skip prefix)
    filtered_comments = []
    for c in new_human_comments:
        body = c.get("body", "") or ""
        if not IGNORE_COMMENT_PATTERNS.search(body):
            filtered_comments.append(c)

    return filtered_comments, latest_author


def has_new_activity(state: dict, issue: dict, comments: list[dict]) -> tuple[bool, str, list[dict]]:
    """Check if the issue has meaningful new activity since last poll.

    Strategy:
    - Collect all new human comments since last check
    - If latest human comment has PDCA tag → batch process all
    - If multiple human comments (discussion) → batch process all
    - If single meaningful comment → process it
    - Otherwise skip

    Returns (triggered, reason, new_comments_to_process).
    """
    last_check = state.get("last_check")
    if last_check is None:
        return True, "first check", comments  # First time: process all comments

    # Check if issue body/labels actually changed
    if issue_content_changed(state, issue):
        return True, "issue content changed", []

    # Get all new human comments
    new_human_comments, latest_author = get_new_human_comments(state, comments)

    if not new_human_comments:
        return False, "no new human comments", []

    # Multiple new human comments = discussion, process together
    if len(new_human_comments) > 1:
        return True, f"new discussion ({len(new_human_comments)} comments) by @{latest_author}", new_human_comments

    # Single new human comment
    single_comment = new_human_comments[0]
    body = single_comment.get("body", "")

    # Skip comments with ignore prefix (allows discussing tags without triggering)
    if IGNORE_COMMENT_PATTERNS.search(body):
        return False, f"ignored comment (has skip marker) by @{latest_author}", []

    # Check if it has PDCA tags (human explicitly triggering a step)
    if get_tags(body):
        return True, f"new comment with PDCA tag by @{latest_author}", new_human_comments

    # Check if it's meaningful content (not just noise)
    clean_body = re.sub(r"\[.*?\]\(.*?\)|[#*`>_\-~]|```[\s\S]*?```", "", body).strip()
    if len(clean_body) >= MIN_COMMENT_LENGTH:
        return True, f"new meaningful comment by @{latest_author}", new_human_comments

    # Single short/noisy comment - skip
    return False, f"noisy comment skipped by @{latest_author}", []


# ── Time utilities ───────────────────────────────────────────────────────────


def _parse_ts(s: str) -> datetime:
    """Parse ISO 8601 string (with Z or +/-HH:MM offset) into a datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── Context consolidation ────────────────────────────────────────────────────


def build_consolidated_context(
    issue: dict,
    comments: list[dict],
    new_comment_ids: set[int] | None = None,
) -> str:
    """Build a full context string from issue body + all comments for AI input.

    Args:
        issue: GitHub issue dict
        comments: All comments on the issue
        new_comment_ids: Set of comment IDs that are new (will be marked)
    """
    new_ids = new_comment_ids or set()

    lines = [
        "=" * 60,
        f"ISSUE #{issue['number']}: {issue['title']}",
        "=" * 60,
        f"State: {issue.get('state', 'open')}",
        f"URL: {issue.get('html_url', '')}",
    ]
    if issue.get("assignee"):
        lines.append(f"Assignee: @{issue['assignee']['login']}")
    if issue.get("labels"):
        lines.append(f"Labels: {', '.join(issue['labels'])}")

    lines += [
        "",
        "─── Original Description ───────────────────────────────────────",
        issue.get("body") or "(no description)",
        "",
    ]

    if comments:
        lines.append("─── Comments (chronological) ────────────────────────────────")
        for i, c in enumerate(comments, 1):
            author = c["user"]["login"]
            created = c["created_at"]
            c_id = c.get("id")
            is_new = c_id in new_ids

            header = f"Comment #{i} by @{author} ({created})"
            if is_new:
                header += " [NEW]"

            lines += [
                "",
                header,
                c.get("body", ""),
            ]

    return "\n".join(lines)


def context_hash(issue: dict, comments: list[dict]) -> str:
    """Compute a quick hash of the issue + comments to detect content changes."""
    key_parts = [
        str(issue.get("updated_at", "")),
        issue.get("body") or "",
        str(issue.get("labels", [])),
    ]
    for c in comments:
        key_parts.append(f"{c.get('id')}:{c.get('updated_at', '')}:{c.get('body', '')}")
    content = "|".join(key_parts)
    return hashlib.md5(content.encode()).hexdigest()[:12]


def should_skip_ai_execution(
    state: dict,
    step_dir: Path,
    step: str,
    issue: dict,
    comments: list[dict],
) -> tuple[bool, str]:
    """Check if AI execution can be skipped (files exist and context unchanged).

    Returns (skip, reason). Skip if:
    - Step files already exist AND
    - Context hash matches stored hash AND
    - Not a refresh request
    """
    # Always run if files don't exist
    if not step_files_exist(step_dir, step):
        log.info("[SkipCheck] Step '%s' files missing in %s — will execute AI", step, step_dir)
        return False, ""

    # Check context hash
    current_hash = context_hash(issue, comments)
    stored_hash = state.get(f"{step}_context_hash")

    if stored_hash == current_hash:
        log.info("[SkipCheck] Step '%s' — files exist, hash unchanged (%s) — skipping AI", step, current_hash)
        return True, f"no context changes (hash: {current_hash})"

    # Context changed, update the hash
    log.info("[SkipCheck] Step '%s' — context changed (stored=%s, current=%s) — will execute AI",
             step, stored_hash, current_hash)
    state[f"{step}_context_hash"] = current_hash
    return False, ""


# ── State management ─────────────────────────────────────────────────────────


def load_state(state_dir: Path, issue_number: int) -> dict:
    state_file = state_dir / str(issue_number) / "state.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {
        "issue_number": issue_number,
        "completed_steps": [],
        "current_step": None,
        "status": "active",
        "last_check": None,
        "last_issue_body": None,
        "last_issue_labels": [],
        "step_completed_at": None,
    }


def save_state(state_dir: Path, issue_number: int, state: dict) -> None:
    state_file = state_dir / str(issue_number) / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True))


# ── Path / slug utilities ────────────────────────────────────────────────────


def slugify(text: str, max_len: int = 50) -> str:
    """Convert text to a filesystem-safe slug, truncated to max_len."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    # Truncate to max_len, but avoid cutting in the middle of a word
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


def step_output_dir(base: Path, issue_number: int, title: str, step: str) -> Path:
    """Build the output directory: <base>/docs/<n>-<slug>/<step>/"""
    return base / "docs" / f"{issue_number}-{slugify(title)}" / step


# ── Skill execution ──────────────────────────────────────────────────────────


# ── Conversation persistence ─────────────────────────────────────────────
# Each (issue, step) pair maintains its own Claude conversation so the AI
# can continue where it left off across poll cycles without redoing work.
# ─────────────────────────────────────────────────────────────────────────


def _conv_path(state_dir: Path, issue_number: int, step: str) -> Path:
    """Get the conversation file path for an issue+step pair."""
    return state_dir / str(issue_number) / "conversation" / f"{step}.jsonl"


def _save_conversation_turn(path: Path, prompt: str, response: str) -> None:
    """Append a conversation turn to the JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    turn = json.dumps({
        "prompt": prompt,
        "response": response,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    with open(path, "a") as f:
        f.write(turn + "\n")


def _conversation_context(conv_path: Path, new_session: bool) -> str:
    """Build continuation instructions if a prior conversation exists.

    Returns an empty string when starting fresh (new issue, new step, or
    an explicit /new-refresh / #pdca-new-session).
    """
    if new_session or not conv_path.exists():
        if conv_path.exists():
            conv_path.unlink()
        return ""

    # Verify there's at least one completed turn
    try:
        with open(conv_path) as f:
            first = f.readline()
            if not first or not first.strip():
                return ""
    except Exception:
        return ""

    return (
        "\n─── Continuation ────────────────────────────────────────────\n"
        "This is a continuation of the previous conversation for this "
        "step.  The files you generated earlier are still in the working "
        "directory — review them and continue from where you left off, "
        "taking into account any new information provided below.\n"
    )


# ── Skill launcher ────────────────────────────────────────────────────────


def _read_skill(skill_name: str) -> str:
    """Read a skill file and return its content with YAML frontmatter stripped."""
    skill_file = SKILL_DIR / f"{skill_name}.md"
    if not skill_file.exists():
        log.warning("Skill file not found: %s", skill_file)
        return ""
    log.info("[Skill] Loading skill: %s (%d bytes)", skill_file, skill_file.stat().st_size)
    content = skill_file.read_text()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2].strip()
    return content


def _inject_memory_into_skill(skill_content: str, issue_number: int) -> str:
    """Inject memory context into skill content."""
    global _memory
    if _memory is None:
        log.info("[Memory] Memory system not initialized, skipping injection")
        return skill_content

    # Get memory contexts
    global_context = _memory.get_global_context()
    issue_context = _memory.get_issue_context(issue_number)

    has_global = bool(global_context and global_context.strip())
    has_issue = bool(issue_context and issue_context.strip())
    log.info("[Memory] Injecting context for issue #%d — global: %s (%d chars), issue: %s (%d chars)",
             issue_number,
             "yes" if has_global else "no",
             len(global_context) if global_context else 0,
             "yes" if has_issue else "no",
             len(issue_context) if issue_context else 0)

    # Replace placeholders
    skill_content = skill_content.replace("{{GLOBAL_MEMORY}}", global_context)
    skill_content = skill_content.replace("{{ISSUE_MEMORY}}", issue_context)
    skill_content = skill_content.replace("{{ISSUE_NUMBER}}", str(issue_number))

    return skill_content


def _record_ai_call(issue_number: int, step: str, success: bool,
                    elapsed_sec: float, output_chars: int) -> None:
    """Record AI call metrics safely (metrics collector may be None)."""
    _mt = get_metrics()
    if _mt:
        _mt.record_ai_call(
            issue_number=issue_number,
            step=step,
            success=success,
            elapsed_sec=elapsed_sec,
            model=CLAUDE_MODEL,
            output_chars=output_chars,
        )


def run_skill(
    skill_name: str,
    issue: dict,
    work_dir: Path,
    state_dir: Path | None = None,
    extra_context: str = "",
    conv_path: Path | None = None,
    new_session: bool = False,
    timeout: int = 600,
    use_memory: bool = True,
    use_session: bool = True,
) -> tuple[bool, str]:
    """Invoke a PDCA skill via the claude CLI.

    Returns ``(success, stdout_text)``.  Saves the full conversation turn
    to *conv_path* when provided so the next invocation can continue the
    dialogue.

    Args:
        use_session: 使用有状态会话模式，同一 Issue 的步骤间保持上下文
        new_session: 强制创建新会话（清除历史）
    """
    issue_number = issue["number"]
    step_name = skill_name.replace("pdca-", "")

    # 如果强制新会话，重置会话
    if new_session and use_session:
        reset_session(issue_number, work_dir, state_dir)
        log.info("[AI] Session reset for issue #%d (new_session=True, work_dir=%s)", issue_number, work_dir)

    skill_content = _read_skill(skill_name)

    # Inject memory context into skill
    if use_memory:
        skill_content = _inject_memory_into_skill(skill_content, issue_number)

    # ── Build the prompt ───────────────────────────────────────────────
    prompt_parts = [
        f"Execute the PDCA step for GitHub Issue #{issue_number}: {issue['title']}",
        f"Issue URL: {issue.get('html_url', '')}",
        "",
        "Use sub-agents to parallelise independent sub-tasks (e.g. "
        "generating multiple files concurrently).",
    ]

    # 只有不使用 session 模式时才使用旧的 conversation 上下文
    if not use_session and conv_path and not new_session:
        continuation = _conversation_context(conv_path, new_session)
        if continuation:
            prompt_parts.append(continuation)

    prompt_parts += [
        f"Issue Body:\n{issue.get('body', '')}",
        extra_context,
        "\nGenerate the required files in the current working directory.",
    ]

    prompt = "\n".join(prompt_parts)

    # ── 使用 Session 模式执行 ──────────────────────────────────────────
    if use_session:
        try:
            session = get_session(issue_number, work_dir, state_dir, CLAUDE_MODEL, CLAUDE_BASE_URL)
            log.info("[AI] Session mode — model=%s, base_url=%s, step=%s, timeout=%ds, history_turns=%d",
                     session.model, session.base_url, step_name, timeout, len(session.history))
            t0 = time.time()
            success, output = session.execute(
                prompt=prompt,
                skill_content=skill_content,
                step_name=step_name,
                timeout=timeout,
                use_context=not new_session,
            )
            elapsed = time.time() - t0
            output_len = len(output) if output else 0
            log.info("[AI] Session call completed — success=%s, elapsed=%.1fs, output=%d chars",
                     success, elapsed, output_len)

            # Record AI call metrics
            _record_ai_call(issue_number, step_name, success, elapsed, output_len)

            # 同时保存到 conversation log（向后兼容）
            if conv_path:
                _save_conversation_turn(conv_path, prompt, output)

            if not success:
                log.error(
                    "Skill %s failed for issue #%d\n  output: %s",
                    skill_name, issue_number, output[:500]
                )
                return False, output

            log.info("Skill %s completed successfully (session mode)", skill_name)

            # Parse and execute memory commands from output
            if use_memory and _memory is not None:
                commands = _memory.parse_memory_commands(output)
                if commands:
                    log.info("Executing %d memory commands", len(commands))
                    _memory.execute_commands(issue_number, commands)

            return True, output

        except Exception as e:
            log.error("Session execution failed: %s", e)
            # Record failed AI call
            _elapsed = time.time() - t0 if 't0' in dir() else 0
            _record_ai_call(issue_number, step_name, False, _elapsed, 0)
            # 失败时回退到 stateless 模式
            log.info("Falling back to stateless mode")
            use_session = False

    # ── 使用 Stateless 模式执行（兼容旧版本）────────────────────────────
    cmd = ["claude", "--model", CLAUDE_MODEL, "--permission-mode", "bypassPermissions", "-p", prompt]
    if skill_content:
        cmd = ["claude", "--model", CLAUDE_MODEL, "--permission-mode", "bypassPermissions", "--append-system-prompt", skill_content, "-p", prompt]

    # Pass API credentials and endpoint to the claude subprocess
    claude_env = os.environ.copy()
    claude_env["ANTHROPIC_BASE_URL"] = CLAUDE_BASE_URL
    if os.environ.get("DEEPSEEK_API_KEY"):
        claude_env["ANTHROPIC_AUTH_TOKEN"] = os.environ["DEEPSEEK_API_KEY"]
        claude_env["ANTHROPIC_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]

    log.info("[AI] Stateless mode — model=%s, base_url=%s, step=%s, timeout=%ds, prompt=%d chars",
             CLAUDE_MODEL, CLAUDE_BASE_URL, step_name, timeout, len(prompt))

    # ── Execute ────────────────────────────────────────────────────────
    try:
        t0 = time.time()
        result = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=claude_env,
        )
        elapsed = time.time() - t0
        output_len = len(result.stdout) if result.stdout else 0
        log.info("[AI] Stateless call completed — rc=%d, elapsed=%.1fs, stdout=%d chars",
                 result.returncode, elapsed, output_len)

        # Record AI call metrics
        _record_ai_call(issue_number, step_name, result.returncode == 0, elapsed, output_len)

        # Save the conversation turn regardless of outcome
        if conv_path:
            _save_conversation_turn(conv_path, prompt, result.stdout)

        if result.returncode != 0:
            log.error(
                "Skill %s failed (rc=%d)\n  stderr: %s\n  stdout: %s",
                skill_name, result.returncode,
                result.stderr[:500], result.stdout[:500],
            )
            return False, result.stdout

        log.info("Skill %s completed successfully (stateless mode)", skill_name)

        # Parse and execute memory commands from output
        if use_memory and _memory is not None:
            commands = _memory.parse_memory_commands(result.stdout)
            if commands:
                log.info("Executing %d memory commands", len(commands))
                _memory.execute_commands(issue_number, commands)

        return True, result.stdout

    except subprocess.TimeoutExpired:
        log.error("Skill %s timed out after %ds", skill_name, timeout)
        _record_ai_call(issue_number, step_name, False, timeout, 0)
        if conv_path:
            turn = json.dumps({
                "prompt": prompt,
                "error": f"TIMEOUT after {timeout}s",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            with open(conv_path, "a") as f:
                f.write(turn + "\n")
        return False, ""
    except FileNotFoundError:
        log.warning("claude CLI not found. Install Claude Code or run skills manually.")
        return False, ""


def step_files_exist(work_dir: Path, step: str) -> bool:
    """Check if all required files for a given step exist."""
    return all((work_dir / f).is_file() for f in STEP_FILES[step])


# ── Git operations ───────────────────────────────────────────────────────────


def _find_changed_files(repo_root: Path) -> list[str]:
    """Get list of files changed (modified + new untracked) since last commit."""
    files: list[str] = []
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        files.extend(f for f in result.stdout.strip().split("\n") if f)

        result2 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        files.extend(f for f in result2.stdout.strip().split("\n") if f)
    except Exception:
        pass
    return files


def _verify_push_landed(repo_root: Path) -> bool:
    """After a push, fetch and compare local vs remote HEAD to confirm
    the commit actually reached the remote.  Returns True if verified,
    False if the remote is behind (push silently failed)."""
    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        )
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""

        if not current_branch:
            log.warning("Push verification: cannot determine current branch — skipping check")
            return True  # can't verify but don't block

        subprocess.run(
            ["git", "fetch", "origin", current_branch],
            cwd=str(repo_root), capture_output=True, timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        local_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        remote_sha = subprocess.run(
            ["git", "rev-parse", f"origin/{current_branch}"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        ).stdout.strip()

        if local_sha and remote_sha and local_sha == remote_sha:
            log.info("Push verified — local and remote HEAD match (%s)", local_sha[:10])
            return True
        else:
            log.warning(
                "Push verification FAILED — local HEAD %s != origin/%s %s. "
                "Push may not have reached the remote.",
                local_sha[:10] if local_sha else "?",
                current_branch,
                remote_sha[:10] if remote_sha else "?",
            )
            return False
    except Exception as exc:
        log.warning("Push verification error (non-fatal): %s", exc)
        return True  # can't verify but don't block


def _git_push_with_retry(repo_root: Path, push_args: list[str] | None = None) -> bool:
    """Push current branch with retry for transient failures.

    Handles "no upstream" errors with automatic -u origin HEAD fallback.
    Verifies push landed on the remote after each attempt.
    Returns True if push succeeded and was verified.
    """
    cmd = ["git", "push"]
    if push_args:
        cmd.extend(push_args)

    for attempt in range(1, 4):
        try:
            subprocess.run(
                cmd,
                cwd=str(repo_root),
                check=True,
                capture_output=True,
                timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                stdin=subprocess.DEVNULL,
            )
            if _verify_push_landed(repo_root):
                return True
            log.warning("Push verification failed (attempt %d/3)", attempt)
        except subprocess.TimeoutExpired:
            log.warning("git push timed out (attempt %d/3)", attempt)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr or "")
            if "no upstream" in stderr or "no such branch" in stderr:
                try:
                    subprocess.run(
                        ["git", "push", "-u", "origin", "HEAD"],
                        cwd=str(repo_root),
                        check=True,
                        capture_output=True,
                        timeout=120,
                        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                        stdin=subprocess.DEVNULL,
                    )
                    if _verify_push_landed(repo_root):
                        return True
                    log.warning("Push -u verification failed (attempt %d/3)", attempt)
                except subprocess.TimeoutExpired:
                    log.warning("git push -u timed out (attempt %d/3)", attempt)
                except subprocess.CalledProcessError as e2:
                    s2 = e2.stderr.decode() if isinstance(e2.stderr, bytes) else str(e2.stderr or "")
                    log.warning("git push -u failed (attempt %d/3): %s", attempt, s2[:200])
            else:
                log.warning("git push failed (attempt %d/3): %s", attempt, stderr[:200])

        if attempt < 3:
            delay = attempt * 10
            log.info("Retrying git push in %ds...", delay)
            time.sleep(delay)

    return False


@contextmanager
def stash_context(repo_root: Path, label: str = "pdca-stash"):
    """Context manager that stashes dirty changes on enter and pops on exit.

    Only stashes if the working tree is actually dirty.  Always pops the
    stash on exit, even if the body raised an exception.
    """
    stashed = False
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        )
        if status.stdout.strip():
            subprocess.run(
                ["git", "stash", "push", "--include-untracked", "-m", label],
                cwd=str(repo_root), capture_output=True, timeout=30,
            )
            stashed = True
        yield
    finally:
        if stashed:
            try:
                subprocess.run(
                    ["git", "stash", "pop"],
                    cwd=str(repo_root), capture_output=True, timeout=30,
                )
            except Exception:
                pass


def git_commit_and_push(files: list[Path], message: str, repo_root: Path) -> bool:
    """Stage, commit, and push generated files. Returns True on full success."""
    # Stage the step output files
    try:
        subprocess.run(
            ["git", "add", "--"] + [str(f) for f in files],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr or "")
        log.error("git add failed: %s", stderr[:200])
        return False

    # Check if there are actually staged changes (locale-safe)
    diff_result = subprocess.run(
        ["git", "diff", "--staged", "--quiet"],
        cwd=str(repo_root), capture_output=True,
    )
    if diff_result.returncode == 0:
        log.warning("Nothing new to commit — files may not have changed (no staged changes)")
        # Return True so the runner doesn't treat this as a failure, but
        # the caller can distinguish "nothing changed" from "push OK" if needed.
        return True

    # Commit (disable GPG signing to avoid blocking for passphrase)
    try:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", message],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr or "")
        log.error("git commit failed: %s", stderr[:200])
        return False

    # Push — with retry for transient network failures
    if not _git_push_with_retry(repo_root):
        log.warning("git push failed after 3 attempts — files committed locally but not pushed")
        return False

    log.info("Git push successful")
    return True


# ── Issue processing ─────────────────────────────────────────────────────────


def _handle_lifecycle_tags(
    state: dict,
    tags: set[str],
    issue: dict,
    owner: str,
    repo: str,
    number: int,
    state_dir: Path,
    base_work_dir: Path,
    comments: list[dict] | None = None,
) -> bool:
    """Handle lifecycle tags (#pdca-close, #pdca-abort, #pdca-skip, #pdca-reset).

    Returns True if the issue was handled (caller should return immediately).
    """
    if "#pdca-close" in tags:
        if issue["state"] != "closed":
            _gh.close_issue(owner, repo, number)
            log.info("Issue #%d closed via #pdca-close tag", number)
        state["status"] = "closed"
        save_state(state_dir, number, state)
        _mt5 = get_metrics()
        if _mt5:
            _mt5.mark_issue_done(number)
        return True

    if "#pdca-abort" in tags:
        if state.get("status") != "aborted":
            _gh.add_comment(owner, repo, number, f"🛑 PDCA process aborted for issue #{number}.")
            log.info("Issue #%d aborted", number)
        state["status"] = "aborted"
        save_state(state_dir, number, state)
        _mt6 = get_metrics()
        if _mt6:
            _mt6.mark_issue_done(number)
        return True

    if "#pdca-skip" in tags:
        state["status"] = "skipped"
        save_state(state_dir, number, state)
        log.info("Issue #%d skipped", number)
        _mt7 = get_metrics()
        if _mt7:
            _mt7.mark_issue_done(number)
        return True

    if "#pdca-reset" in tags:
        # Only process #pdca-reset from NEW human comments — old/stale
        # reset comments that were already handled should not re-trigger.
        new_human, _ = get_new_human_comments(state, comments or [])
        reset_in_new = any(
            "#pdca-reset" in get_tags(c.get("body", ""))
            for c in new_human
        )
        if not reset_in_new:
            # No new reset request — don't re-process
            log.info("Issue #%d: #pdca-reset in old comments only, skipping (already handled)", number)
            return False

        # Reset issue memory if memory system is enabled
        global _memory
        if _memory is not None:
            issue_mem_path = _memory._issue_path(number)
            if issue_mem_path.exists():
                issue_mem_path.unlink()
                log.info("Issue #%d: memory cleared via #pdca-reset", number)

        # Clear Claude session for this issue
        reset_session(number, base_work_dir, state_dir)
        log.info("Issue #%d: Claude session cleared via #pdca-reset", number)

        # Reset state in-place and save — do NOT delete the state file
        # so subsequent polls don't re-process the same reset tag.
        state["completed_steps"] = []
        state["current_step"] = None
        state["status"] = "active"
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        save_state(state_dir, number, state)

        _gh.add_comment(
            owner, repo, number,
            f"🔄 **PDCA Reset** — Issue #{number} state and memory have been cleared. "
            f"Add a new pdca-start tag to begin a fresh PDCA cycle."
        )
        log.info("Issue #%d reset completed", number)
        return True

    if "#pdca-new-session" in tags:
        # Only process from NEW human comments — old/stale
        # new-session comments that were already handled should not re-trigger.
        new_human, _ = get_new_human_comments(state, comments or [])
        new_session_in_new = any(
            "#pdca-new-session" in get_tags(c.get("body", ""))
            for c in new_human
        )
        if not new_session_in_new:
            log.info("Issue #%d: #pdca-new-session in old comments only, skipping (already handled)", number)
            return False

        # 仅重置 Claude 会话，不清除状态和记忆
        reset_session(number, base_work_dir, state_dir)
        _gh.add_comment(
            owner, repo, number,
            f"🆕 **New Session** — Claude session for issue #{number} has been reset. "
            f"Next PDCA step will start with a fresh context."
        )
        log.info("Issue #%d: new Claude session requested", number)
        return True

    return False


# ── Act step artifact generation ──────────────────────────────────────────────


def _generate_act_artifacts(
    state: dict,
    decision: str,
    issue: dict,
    owner: str,
    repo: str,
    number: int,
    base_work_dir: Path,
    branch: str,
    target_branch: str,
) -> bool:
    """Generate Decision.md and (for Deploy) CodeDiff.md in the act folder,
    then commit and push them to the PDCA branch.

    Args:
        decision: One of "Deploy", "Fix", "Fallback"
        branch: The PDCA feature branch name
        target_branch: The deploy target branch (e.g. main)
    """
    title = issue.get("title", "")
    act_dir = step_output_dir(base_work_dir, number, title, "act")
    act_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    issue_url = issue.get("html_url", "")

    # ── 1. Generate Decision.md ──────────────────────────────────────────
    decision_body = (
        f"# PDCA Decision Report\n\n"
        f"## Issue\n\n"
        f"- **Issue**: [#{number}]({issue_url}) — {title}\n"
        f"- **Decision**: **{decision}**\n"
        f"- **Timestamp**: {timestamp}\n"
        f"- **PDCA Branch**: `{branch}`\n"
        f"- **Target Branch**: `{target_branch}`\n\n"
        f"## Decision Summary\n\n"
    )

    if decision == "Deploy":
        decision_body += (
            f"The PDCA cycle completed successfully. "
            f"Changes in `{branch}` have been merged into `{target_branch}` and pushed to the remote.\n\n"
            f"All review and test artifacts have been approved.\n"
        )
    elif decision == "Fix":
        decision_body += (
            f"The PDCA cycle requires additional changes. "
            f"The Do step will be re-entered with the feedback provided in the decision comment.\n\n"
            f"Previous Do and Check outputs have been discarded. Plan artifacts are retained.\n"
        )
    elif decision == "Fallback":
        decision_body += (
            f"The PDCA cycle has been rolled back. "
            f"Branch `{branch}` has been deleted and all changes have been reverted to `{target_branch}`.\n\n"
            f"No changes from this cycle remain in the repository.\n"
        )

    decision_body += (
        f"\n## PDCA Cycle Summary\n\n"
        f"- **Plan**: `docs/{number}-{slugify(title)}/plan/` (Design.md, Impact.md)\n"
        f"- **Do**: `docs/{number}-{slugify(title)}/do/` (Change.md)\n"
        f"- **Check**: `docs/{number}-{slugify(title)}/check/` (Review.md, Test.md)\n"
        f"- **Act**: This document\n\n"
        f"## Completed Steps\n\n"
    )
    for step in state.get("completed_steps", []):
        decision_body += f"- [x] {step.capitalize()}\n"

    decision_path = act_dir / "Decision.md"
    decision_path.write_text(decision_body)
    log.info("Issue #%d: generated Decision.md in %s", number, act_dir)

    # ── 2. Generate CodeDiff.md (Deploy only) ────────────────────────────
    diff_path = act_dir / "CodeDiff.md"
    if decision == "Deploy":
        diff_content = _generate_code_diff_report(
            branch, target_branch, base_work_dir, number, title, timestamp
        )
        diff_path.write_text(diff_content)
        log.info("Issue #%d: generated CodeDiff.md in %s", number, act_dir)
    else:
        # For Fix/Fallback, generate a minimal CodeDiff.md noting no merge occurred
        diff_content = (
            f"# Code Diff Report\n\n"
            f"**Decision**: {decision} — no merge performed.\n\n"
            f"- **Issue**: [#{number}]({issue_url}) — {title}\n"
            f"- **Timestamp**: {timestamp}\n"
        )
        diff_path.write_text(diff_content)

    # ── 3. Commit and push to the PDCA branch ────────────────────────────
    # We must be on the PDCA branch for the commit
    return _commit_act_artifacts(act_dir, branch, number, decision, base_work_dir)


def _generate_code_diff_report(
    branch: str,
    target_branch: str,
    base_work_dir: Path,
    number: int,
    title: str,
    timestamp: str,
) -> str:
    """Generate a CodeDiff.md report showing what was merged from the PDCA
    branch into the target branch."""
    diff_lines: list[str] = []
    diff_lines.append(f"# Code Diff Report — Deploy Merge\n")
    diff_lines.append(f"\n- **Issue**: #{number} — {title}")
    diff_lines.append(f"- **Source Branch**: `{branch}`")
    diff_lines.append(f"- **Target Branch**: `{target_branch}`")
    diff_lines.append(f"- **Timestamp**: {timestamp}\n")

    # Collect diff stats from the merge
    try:
        # git diff between target_branch and the PDCA branch (before merge)
        stat_result = subprocess.run(
            ["git", "diff", "--stat", f"{target_branch}...{branch}"],
            cwd=str(base_work_dir), capture_output=True, text=True, timeout=30,
        )
        if stat_result.returncode == 0 and stat_result.stdout.strip():
            diff_lines.append("## File Summary\n")
            diff_lines.append("```")
            diff_lines.append(stat_result.stdout.strip())
            diff_lines.append("```\n")
        else:
            diff_lines.append("## File Summary\n\n(No file changes detected between branches)\n")
    except Exception as e:
        log.warning("Issue #%d: git diff --stat failed: %s", number, e)
        diff_lines.append("## File Summary\n\n(Unable to compute diff stats)\n")

    # Collect file-level diff
    try:
        full_diff = subprocess.run(
            ["git", "diff", f"{target_branch}...{branch}"],
            cwd=str(base_work_dir), capture_output=True, text=True, timeout=60,
        )
        if full_diff.returncode == 0 and full_diff.stdout.strip():
            diff_lines.append("## Detailed Changes\n")
            diff_lines.append("```diff")
            diff_lines.append(full_diff.stdout.strip())
            diff_lines.append("```")
        else:
            diff_lines.append("## Detailed Changes\n\n(No detailed diff available)\n")
    except Exception as e:
        log.warning("Issue #%d: git diff failed: %s", number, e)
        diff_lines.append("## Detailed Changes\n\n(Unable to generate diff)\n")

    return "\n".join(diff_lines)


def _commit_act_artifacts(
    act_dir: Path,
    branch: str,
    number: int,
    decision: str,
    base_work_dir: Path,
) -> bool:
    """Commit and push the act artifacts (Decision.md, CodeDiff.md) to the
    PDCA branch on GitHub.

    Returns True if the artifacts were committed AND pushed successfully.
    Returns False if any step failed (the commit may still exist locally).
    """
    try:
        # Switch to the PDCA branch (only if not already on it)
        current_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(base_work_dir), capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if current_branch != branch:
            subprocess.run(
                ["git", "checkout", branch],
                cwd=str(base_work_dir), capture_output=True, timeout=30,
            )

        # Stage the act files
        generated = [act_dir / "Decision.md", act_dir / "CodeDiff.md"]
        existing = [f for f in generated if f.is_file()]
        if not existing:
            log.warning("Issue #%d: no act artifacts to commit", number)
            return True  # Nothing to do isn't a failure

        subprocess.run(
            ["git", "add", "--"] + [str(f) for f in existing],
            cwd=str(base_work_dir), check=True, capture_output=True,
        )

        # Check if there are staged changes
        diff_check = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=str(base_work_dir), capture_output=True,
        )
        if diff_check.returncode == 0:
            log.info("Issue #%d: no new act changes to commit", number)
            return True

        # Commit
        commit_msg = f"docs: PDCA Act for #{number} — decision {decision}"
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", commit_msg],
            cwd=str(base_work_dir), check=True, capture_output=True,
        )

        # Push
        if not _git_push_with_retry(base_work_dir, ["-u", "origin", "HEAD"]):
            log.warning("Issue #%d: act push failed after 3 attempts — commit exists locally but not on remote", number)
            return False

        log.info("Issue #%d: act artifacts pushed to %s", number, branch)
        return True
    except Exception as e:
        log.error("Issue #%d: failed to commit act artifacts: %s", number, e)
        return False


def _handle_decision_tag(
    state: dict,
    tags: set[str],
    issue: dict,
    owner: str,
    repo: str,
    number: int,
    state_dir: Path,
    base_work_dir: Path,
) -> bool:
    """Handle decision-phase tags (#Deploy, #Fix, #Fallback) after Check completes.

    Returns True if a decision was processed (caller should return immediately).
    """
    head_text = f"**Decision** — "

    if "#deploy" in tags:
        branch = state.get("pdca_branch", "")
        target = DEPLOY_BRANCH

        # If a previous deploy already merged but failed to push, don't
        # re-merge — just let the push_pending retry logic handle it on
        # the next poll cycle.
        if state.get("push_pending") and state.get("push_pending_type") == "deploy":
            log.info("Issue #%d: deploy already merged locally, push pending — skipping re-merge", number)
            return True

        # Detect uncommitted changes and stash them for the duration of deploy
        deploy_failed = False
        act_artifacts_ok = True

        try:
            with stash_context(base_work_dir, "pdca-deploy-stash"):
                # Try checking out the target branch; create from base/main if missing
                checkout = subprocess.run(
                    ["git", "checkout", target],
                    cwd=str(base_work_dir), capture_output=True, text=True,
                )
                if checkout.returncode != 0:
                    base = state.get("base_branch", DEPLOY_BRANCH)
                    log.info("Deploy: target '%s' not found, creating from '%s'", target, base)
                    subprocess.run(
                        ["git", "checkout", "-b", target, base],
                        cwd=str(base_work_dir), check=True, capture_output=True,
                    )
                subprocess.run(
                    ["git", "merge", branch, "--no-edit"],
                    cwd=str(base_work_dir), check=True, capture_output=True, timeout=30,
                )

                # Push with retry for transient network failures
                if not _git_push_with_retry(base_work_dir, ["-u", "origin", "HEAD"]):
                    raise RuntimeError("git push failed after 3 attempts — network connectivity issue")

                log.info("Issue #%d: deployed — merged %s into %s", number, branch, target)

                # Return to the pdca branch before stash pop
                subprocess.run(
                    ["git", "checkout", branch],
                    cwd=str(base_work_dir), capture_output=True, timeout=30,
                )

            # stash is popped — Generate Act artifacts (Decision.md + CodeDiff.md)
            # and push to the PDCA branch before updating state.
            try:
                act_artifacts_ok = _generate_act_artifacts(
                    state, "Deploy", issue, owner, repo, number,
                    base_work_dir, branch, target,
                )
            except Exception as act_err:
                log.error("Issue #%d: failed to generate act artifacts: %s", number, act_err)
                act_artifacts_ok = False

            if act_artifacts_ok:
                _gh.add_comment(
                    owner, repo, number,
                    f"{head_text}**Deploy** — merged `{branch}` into `{target}` and pushed.\n"
                    f"Act artifacts: `Decision.md` and `CodeDiff.md` generated in "
                    f"`docs/{number}-{slugify(issue.get('title', ''))}/act/`.",
                )
            else:
                _gh.add_comment(
                    owner, repo, number,
                    f"{head_text}**Deploy** — merged `{branch}` into `{target}` and pushed.\n"
                    f"Warning: act artifacts could not be generated (see logs).",
                )
        except Exception as e:
            log.error("Issue #%d: deploy failed — %s", number, e)
            # Try to recover: switch back to the PDCA branch (stash already popped)
            try:
                subprocess.run(
                    ["git", "checkout", branch],
                    cwd=str(base_work_dir), capture_output=True, timeout=30,
                )
            except Exception:
                pass
            deploy_failed = True
            _gh.add_comment(
                owner, repo, number,
                f"{head_text}**Deploy** failed — git push could not reach GitHub (network issue). "
                f"The merge is committed locally on `{target}`. Will retry push on next poll cycle.",
            )
        if not deploy_failed:
            state["phase"] = None
            if "act" not in state.get("completed_steps", []):
                state.setdefault("completed_steps", []).append("act")
            state["current_step"] = "act"
            state.pop("push_pending", None)
            state["last_check"] = datetime.now(timezone.utc).isoformat()
            save_state(state_dir, number, state)
            _mt8 = get_metrics()
            if _mt8:
                _mt8.mark_issue_done(number)
        else:
            # Deploy failed — mark as push_pending so next poll cycle retries
            state["push_pending"] = True
            state["push_pending_type"] = "deploy"
            state["last_check"] = datetime.now(timezone.utc).isoformat()
            save_state(state_dir, number, state)
            log.info("Issue #%d: deploy push pending — will retry on next poll cycle", number)
        return True

    if "#fix" in tags:
        branch = state.get("pdca_branch", "")
        target = DEPLOY_BRANCH

        # Generate Act artifacts (Decision.md + CodeDiff.md) documenting the Fix decision
        try:
            act_push_ok = _generate_act_artifacts(
                state, "Fix", issue, owner, repo, number,
                base_work_dir, branch, target,
            )
            if not act_push_ok:
                log.warning("Issue #%d: fix act artifacts committed locally but push failed", number)
        except Exception as act_err:
            log.error("Issue #%d: failed to generate act artifacts for Fix: %s", number, act_err)

        # Reset to re-enter the Do step — keep Plan but remove Do and Check
        state["completed_steps"] = ["plan"]
        state["current_step"] = None
        state["phase"] = None
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        reset_session(number, base_work_dir, state_dir)
        save_state(state_dir, number, state)
        if act_push_ok:
            _gh.add_comment(
                owner, repo, number,
                f"{head_text}**Fix** — cycle reset to Do step. "
                f"Review the feedback above, then add `#plan-approved` to re-enter the Do step.\n"
                f"Act artifact `Decision.md` generated in `docs/{number}-{slugify(issue.get('title', ''))}/act/`.",
            )
        else:
            _gh.add_comment(
                owner, repo, number,
                f"{head_text}**Fix** — cycle reset to Do step. "
                f"Review the feedback above, then add `#plan-approved` to re-enter the Do step.\n"
                f"Warning: act artifacts could not be generated or pushed (see logs).",
            )
        log.info("Issue #%d: fix cycle started (reset to Do step)", number)
        return True

    if "#fallback" in tags:
        branch = state.get("pdca_branch", "")
        base_branch = state.get("base_branch", "main")
        target = DEPLOY_BRANCH

        # Generate Act artifacts BEFORE deleting the branch (need the branch for diff)
        try:
            act_push_ok = _generate_act_artifacts(
                state, "Fallback", issue, owner, repo, number,
                base_work_dir, branch, target,
            )
            if not act_push_ok:
                log.warning("Issue #%d: fallback act artifacts committed locally but push failed", number)
        except Exception as act_err:
            log.error("Issue #%d: failed to generate act artifacts for Fallback: %s", number, act_err)

        try:
            # Discard the pdca branch and go back to base
            subprocess.run(
                ["git", "checkout", base_branch],
                cwd=str(base_work_dir), check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=str(base_work_dir), check=False, capture_output=True,
            )
            # Also delete remote branch if it exists
            subprocess.run(
                ["git", "push", "origin", "--delete", branch],
                cwd=str(base_work_dir), check=False, capture_output=True, timeout=30,
            )
            log.info("Issue #%d: fallback — reverted to %s, deleted %s", number, base_branch, branch)
            if act_push_ok:
                _gh.add_comment(
                    owner, repo, number,
                    f"{head_text}**Fallback** — reverted to `{base_branch}` and deleted `{branch}`.\n"
                    f"Act artifact `Decision.md` generated in `docs/{number}-{slugify(issue.get('title', ''))}/act/`.",
                )
            else:
                _gh.add_comment(
                    owner, repo, number,
                    f"{head_text}**Fallback** — reverted to `{base_branch}` and deleted `{branch}`.\n"
                    f"Warning: act artifacts could not be generated or pushed (see logs).",
                )
        except Exception as e:
            log.error("Issue #%d: fallback failed — %s", number, e)
            _gh.add_comment(
                owner, repo, number,
                f"{head_text}**Fallback** failed: {e}",
            )
        state["phase"] = None
        if "act" not in state.get("completed_steps", []):
            state.setdefault("completed_steps", []).append("act")
        state["current_step"] = "act"
        state["status"] = "fallback"
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        save_state(state_dir, number, state)
        _mt9 = get_metrics()
        if _mt9:
            _mt9.mark_issue_done(number)
        return True

    return False


def ensure_pdca_branch(
    state: dict,
    state_dir: Path,
    issue_number: int,
    title: str,
    repo_root: Path,
) -> str | None:
    """Get or create the PDCA feature branch for an issue.

    Always constructs the branch name from issue number + title,
    then checks local (and remote) existence before creating.
    Stores the name in state so all steps reuse the same branch.
    """
    branch = f"pdca/{issue_number}-{slugify(title)}"
    state["pdca_branch"] = branch

    try:
        # Check if branch exists locally
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        if result.returncode == 0:
            # Stash any dirty changes before checkout, pop after checkout.
            # The stash context manager handles both success and error paths.
            with stash_context(repo_root, f"pdca-auto-stash-{branch}"):
                checkout = subprocess.run(
                    ["git", "checkout", branch],
                    cwd=str(repo_root), capture_output=True, text=True,
                )
                if checkout.returncode != 0:
                    stderr = checkout.stderr.strip()
                    log.error("Failed to checkout branch %s: %s", branch, stderr[:200])
                    return None

            log.info("Reusing existing branch: %s", branch)
            return branch

        # Not found locally — try fetching from remote
        fetch = subprocess.run(
            ["git", "fetch", "origin", f"{branch}:{branch}"],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        if fetch.returncode == 0:
            subprocess.run(
                ["git", "checkout", branch],
                cwd=str(repo_root), check=True, capture_output=True,
            )
            log.info("Fetched and switched to branch: %s", branch)
            return branch

        # Doesn't exist anywhere — create fresh from current branch
        base_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        if base_result.returncode == 0:
            base = base_result.stdout.strip()
            state["base_branch"] = base
            log.info("Recording base branch: %s", base)

        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=str(repo_root), check=True, capture_output=True,
        )
        log.info("Created branch: %s", branch)

        # Push the new branch to remote so GitHub links resolve immediately
        try:
            subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=str(repo_root), check=True, capture_output=True,
                timeout=60,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                stdin=subprocess.DEVNULL,
            )
            log.info("Pushed new branch to remote: %s", branch)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr or "")
            log.warning("Failed to push branch %s to remote: %s", branch, stderr[:200])
            # Non-fatal: branch exists locally, user can push manually

        return branch
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr or "")
        log.error("Branch operation failed: %s", stderr[:200])
        return None


def _retry_pending_push(
    state: dict,
    owner: str,
    repo: str,
    number: int,
    base_work_dir: Path,
    state_dir: Path,
    issue: dict,
) -> bool:
    """Retry a pending git push from a previous cycle.

    Returns True if the push was handled and the caller should return
    (push failed definitively), or False if there was no pending push
    or the push succeeded (caller should continue processing).
    """
    if not state.get("push_pending"):
        return False

    push_pending_type = state.get("push_pending_type", "step")
    log.info("Issue #%d: retrying pending git push (type=%s)", number, push_pending_type)

    push_retry_ok = False

    if push_pending_type == "deploy":
        target = DEPLOY_BRANCH
        _retry_original_branch = None
        try:
            rev = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(base_work_dir), capture_output=True, text=True, timeout=10,
            )
            _retry_original_branch = rev.stdout.strip() if rev.returncode == 0 else None
        except Exception:
            pass

        with stash_context(base_work_dir, "pdca-push-retry-stash"):
            try:
                checkout = subprocess.run(
                    ["git", "checkout", target],
                    cwd=str(base_work_dir), capture_output=True, text=True, timeout=30,
                )
                if checkout.returncode != 0:
                    log.warning("Issue #%d: checkout to '%s' failed: %s", number, target, checkout.stderr.strip()[:200])
                    _gh.add_comment(
                        owner, repo, number,
                        f"**Deploy Push Retry Failed** — could not switch to `{target}` branch. "
                        f"Will retry on next poll cycle.",
                    )
                    state["last_check"] = datetime.now(timezone.utc).isoformat()
                    save_state(state_dir, number, state)
                    return True

                push_retry_ok = _git_push_with_retry(base_work_dir)

                if _retry_original_branch:
                    subprocess.run(
                        ["git", "checkout", _retry_original_branch],
                        cwd=str(base_work_dir), capture_output=True, timeout=30,
                    )
            except Exception:
                try:
                    if _retry_original_branch:
                        subprocess.run(
                            ["git", "checkout", _retry_original_branch],
                            cwd=str(base_work_dir), capture_output=True, timeout=30,
                        )
                except Exception:
                    pass
                raise
    else:
        push_retry_ok = _git_push_with_retry(base_work_dir)

    if push_retry_ok:
        log.info("Issue #%d: pending push succeeded", number)
        if push_pending_type == "deploy":
            if "act" not in state.get("completed_steps", []):
                state.setdefault("completed_steps", []).append("act")
            state["current_step"] = "act"
            state["phase"] = None
            _mt_retry = get_metrics()
            if _mt_retry:
                _mt_retry.mark_issue_done(number)
        state.pop("push_pending", None)
        state.pop("push_pending_type", None)
        save_state(state_dir, number, state)
        return False  # push succeeded — caller may continue
    else:
        _gh.add_comment(
            owner, repo, number,
            f"**Deploy Push Retry Failed**\n\n"
            f"Git push failed after 3 attempts — network connectivity issue. "
            f"The merge is committed locally. Will retry on the next poll cycle.",
        )
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        save_state(state_dir, number, state)
        return True  # caller should return

def _detect_refresh(state: dict, tags: set[str], comments: list[dict], completed: list[str]) -> bool:
    """Check if #pdca-refresh is present in new human comments.

    Returns True only when a refresh was explicitly requested in a new
    human comment and there is a step to refresh.
    """
    if "#pdca-refresh" not in tags:
        return False
    new_human, _ = get_new_human_comments(state, comments)
    refresh_in_new = any(
        "#pdca-refresh" in get_tags(c.get("body", ""))
        for c in new_human
    )
    if not refresh_in_new:
        return False
    if not completed and not state.get("current_step"):
        return False
    return True


def process_issue(
    owner: str, repo: str, number: int,
    state_dir: Path, base_work_dir: Path,
    auto_run: bool, use_session: bool,
) -> None:
    """Process a single issue: detect activity, resolve next step, execute it."""
    global _memory

    issue = _gh.get_issue(owner, repo, number)
    state = load_state(state_dir, number)
    comments = _gh.get_issue_comments(owner, repo, number)

    if _memory is not None:
        _memory.issue_processed(number)

    store_issue_snapshot(state, issue)

    # ── Retry pending git push ──────────────────────────────────────────
    if _retry_pending_push(state, owner, repo, number, base_work_dir, state_dir, issue):
        return

    _mt = get_metrics()

    # ── Re-opened issue ─────────────────────────────────────────────────
    if issue["state"] == "open" and state.get("status") in ("closed", "aborted", "skipped"):
        log.info("Issue #%d: re-opened, resuming PDCA process", number)
        state["status"] = "active"
        save_state(state_dir, number, state)

    # Only consider tags from human (non-runner) comments — skip
    # tags in the runner's own instruction messages like "add pdca-start tag..."
    human_comments = [c for c in comments if not _gh.is_pdca_runner_comment(c)]
    comment_text = " ".join(c.get("body", "") for c in human_comments)
    tags = get_tags(comment_text)
    completed = state.get("completed_steps", [])

    log.info("[Poll] Issue #%d — tags=%s, last_check=%s, comments_count=%d", number, tags, state.get("last_check"), len(comments))
    for i, c in enumerate(comments):
        body = c.get("body", "")
        ctags = get_tags(body)
        if ctags:
            log.info("[Poll]   comment[%d] at %s by %s: tags=%s", i, c.get("created_at"), c.get("user", {}).get("login", "?"), ctags)
        for kw in ("do-approved", "check-approved", "act-approved"):
            if kw in body.lower() and f"#{kw}" not in ctags:
                log.warning("[Poll]   comment[%d] contains '%s' but regex didn't match — raw snippet: %s",
                            i, kw, body[:300].replace("\n", "\\n"))

    # ── #pdca-refresh detection ───────────────────────────────────────────────
    refresh = _detect_refresh(state, tags, comments, completed)
    if refresh:
        log.info("[Refresh] Refresh triggered for issue #%d", number)

    # ── Lifecycle tags ───────────────────────────────────────────────────
    if _handle_lifecycle_tags(state, tags, issue, owner, repo, number, state_dir, base_work_dir, comments):
        return

    # ── Check-review phase ───────────────────────────────────────────────
    if state.get("phase") == "check-review":
        if "#check-approved" in tags:
            state["phase"] = "decision"
            state["last_check"] = datetime.now(timezone.utc).isoformat()
            save_state(state_dir, number, state)
            _gh.add_comment(
                owner, repo, number,
                "**Act Step — Decision Required**\n\n"
                "Check approved. Please add one of these tags in a new comment:\n"
                f"- `#Deploy` — Merge changes into the `{DEPLOY_BRANCH}` branch\n"
                "- `#Fix` — Review feedback and start a fix cycle\n"
                "- `#Fallback` — Revert all changes",
            )
            log.info("Issue #%d: Check approved, entered decision phase", number)
            return
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        save_state(state_dir, number, state)
        log.info("Issue #%d: awaiting #check-approved (phase=check-review)", number)
        return

    # ── Decision phase ───────────────────────────────────────────────────
    if state.get("phase") == "decision":
        if _handle_decision_tag(state, tags, issue, owner, repo, number, state_dir, base_work_dir):
            return
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        save_state(state_dir, number, state)
        log.info("Issue #%d: awaiting decision tag (Deploy / Fix / Fallback)", number)
        return

    # ── Fallback: decision tag after all steps completed ──────────────────
    if len(completed) >= len(PDCA_STEPS) and state.get("status") == "active":
        new_human, _ = get_new_human_comments(state, comments)
        new_tags = set()
        for c in new_human:
            new_tags |= get_tags(c.get("body", ""))
        decision_in_new = any(t in new_tags for t in ("#deploy", "#fix", "#fallback"))
        if decision_in_new:
            log.info("Issue #%d: post-completion decision tag in new comment — processing", number)
            if _handle_decision_tag(state, new_tags, issue, owner, repo, number, state_dir, base_work_dir):
                return
            state["last_check"] = datetime.now(timezone.utc).isoformat()
            save_state(state_dir, number, state)
            return

    if state.get("status") not in (None, "active"):
        return

    # ── Resolve which step to execute ────────────────────────────────────
    step_to_execute: str | None = None

    approval_step = resolve_next_step(tags, completed)
    if approval_step:
        step_to_execute = approval_step
        log.info("[Approval] Approval tag detected — overriding refresh, step=%s", step_to_execute)
    elif refresh:
        step_to_execute = state.get("current_step") or PDCA_STEPS[0]
        log.info("[Refresh] #pdca-refresh detected — current_step=%s, resolved to execute=%s",
                 state.get("current_step"), step_to_execute)
    else:
        step_to_execute = resolve_next_step(tags, completed)

    log.info(
        "Issue #%-5d tags=%s to_execute=%s completed=%s status=%s",
        number, tags, step_to_execute or "—", completed, state.get("status", "active"),
    )

    if not step_to_execute:
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        store_issue_snapshot(state, issue)
        save_state(state_dir, number, state)
        return

    # ── New-activity check ───────────────────────────────────────────────
    triggered, reason, new_comments = has_new_activity(state, issue, comments)

    if not triggered:
        step_changed = step_to_execute is not None and step_to_execute != state.get("current_step")
        if refresh or step_changed:
            triggered = True
            reason = f"tags request step '{step_to_execute}'"
            new_comments = []

    if not triggered:
        log.info("Issue #%d: %s", number, reason)
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        store_issue_snapshot(state, issue)
        save_state(state_dir, number, state)
        return

    log.info("Issue #%d: %s", number, reason)
    store_issue_snapshot(state, issue)

    _gh.add_comment(
        owner, repo, number,
        f"⚙️ **{step_to_execute.capitalize()}** step in progress — please wait...",
    )

    # ── Execute step ─────────────────────────────────────────────────────
    _execute_step(state, step_to_execute, tags, refresh, issue, owner, repo, number,
                  base_work_dir, state_dir, comments, auto_run, use_session, _mt,
                  comment_text, new_comments)


def _execute_step(
    state: dict,
    step_to_execute: str,
    tags: set[str],
    refresh: bool,
    issue: dict,
    owner: str,
    repo: str,
    number: int,
    base_work_dir: Path,
    state_dir: Path,
    comments: list[dict],
    auto_run: bool,
    use_session: bool,
    _mt,
    comment_text: str,
    new_comments: list[dict],
) -> None:
    """Execute a PDCA step: run AI skill, commit+publish, and post result comments."""
    title = issue["title"]
    step_dir = step_output_dir(base_work_dir, number, title, step_to_execute)

    if auto_run:
        skill = SKILL_NAMES[step_to_execute]
        log.info("Issue #%d: running %s → %s", number, skill, ", ".join(STEP_FILES[step_to_execute]))

        branch = ensure_pdca_branch(state, state_dir, number, title, base_work_dir)
        if not branch:
            log.error("Issue #%d: cannot proceed without PDCA branch", number)
            _gh.add_comment(
                owner, repo, number,
                f"❌ **{step_to_execute.capitalize()}** failed: could not create or switch to PDCA branch.",
            )
            return

        step_dir.mkdir(parents=True, exist_ok=True)

        if not refresh:
            skip_ai, skip_reason = should_skip_ai_execution(
                state, step_dir, step_to_execute, issue, comments
            )
            if skip_ai:
                log.info("Issue #%d: skipping %s — %s", number, skill, skip_reason)
                state["last_check"] = datetime.now(timezone.utc).isoformat()
                save_state(state_dir, number, state)
                return

        # Build consolidated context
        extra_context = ""
        if step_to_execute == "plan":
            if refresh:
                for fname in STEP_FILES[step_to_execute]:
                    fp = step_dir / fname
                    if fp.exists():
                        fp.unlink()
                        log.info("[Refresh] Deleted %s to force regeneration", fp)
                all_comment_ids = {c.get("id") for c in comments if not _gh.is_pdca_runner_comment(c)}
                extra_context = build_consolidated_context(issue, comments, all_comment_ids)
                extra_context += (
                    "\n\n─── IMPORTANT ───────────────────────────────────────────\n"
                    "The user has requested a REFRESH with #pdca-refresh.  You MUST\n"
                    "regenerate ALL required files from scratch, incorporating ALL\n"
                    "user comments and answers below.  Do NOT report that files are\n"
                    "\"up-to-date\" — the user explicitly wants a fresh generation.\n"
                )
                log.info("[Refresh] Passing %d human comments as context for Plan regeneration", len(all_comment_ids))
            else:
                new_comment_ids = {c.get("id") for c in new_comments} if new_comments else set()
                extra_context = build_consolidated_context(issue, comments, new_comment_ids)

        if step_to_execute != "plan" and refresh:
            for fname in STEP_FILES[step_to_execute]:
                fp = step_dir / fname
                if fp.exists():
                    fp.unlink()
                    log.info("[Refresh] Deleted %s to force regeneration", fp)
            all_comment_ids = {c.get("id") for c in comments if not _gh.is_pdca_runner_comment(c)}
            extra_context = build_consolidated_context(issue, comments, all_comment_ids)
            extra_context += (
                "\n\n─── IMPORTANT ───────────────────────────────────────────\n"
                "The user has requested a REFRESH with #pdca-refresh.  You MUST\n"
                "regenerate ALL required files from scratch, incorporating ALL\n"
                "user comments and answers below.  Do NOT report that files are\n"
                "\"up-to-date\" — the user explicitly wants a fresh generation.\n"
            )
            log.info("[Refresh] Passing %d human comments as context for %s regeneration",
                     len(all_comment_ids), step_to_execute)

        if step_to_execute == "do":
            skill_cwd = base_work_dir
            extra_context += f"\n\nPut Change.md into: {step_dir}"
        else:
            skill_cwd = step_dir

        new_session = (
            "#pdca-new-session" in tags
            or bool(NEW_SESSION_RE.search(comment_text))
            or refresh
        )
        conv_path = _conv_path(state_dir, number, step_to_execute)

        ok, _ = run_skill(
            skill, issue, skill_cwd, state_dir, extra_context, conv_path,
            new_session=new_session,
            use_session=use_session,
        )

        if ok and step_files_exist(step_dir, step_to_execute):
            generated = [step_dir / f for f in STEP_FILES[step_to_execute]]
            step_label = step_to_execute.capitalize()

            if step_to_execute == "do":
                changed = _find_changed_files(base_work_dir)
                if changed:
                    try:
                        subprocess.run(
                            ["git", "add", "--"] + changed,
                            cwd=str(base_work_dir), check=True, capture_output=True,
                        )
                    except subprocess.CalledProcessError:
                        pass

            slug = slugify(title)
            commit_msg = f"docs: PDCA {step_label} for #{number} — {title}"
            push_ok = git_commit_and_push(generated, commit_msg, base_work_dir)

            current_hash = context_hash(issue, comments)
            state[f"{step_to_execute}_context_hash"] = current_hash

            prev_step = state.get("current_step")
            state["current_step"] = step_to_execute
            completed = state.get("completed_steps", [])
            if step_to_execute not in completed:
                completed.append(step_to_execute)
            state["completed_steps"] = completed
            state["status"] = "active"
            state["last_check"] = datetime.now(timezone.utc).isoformat()
            state["step_completed_at"] = datetime.now(timezone.utc).isoformat()
            if not push_ok:
                state["push_pending"] = True
                log.warning("Issue #%d: push pending — will retry on next poll cycle", number)
            else:
                state.pop("push_pending", None)
            save_state(state_dir, number, state)

            if _mt:
                _mt.record_state_transition(
                    number, from_state=prev_step or "idle", to_state=step_to_execute,
                )

            existing = [f for f in STEP_FILES[step_to_execute] if (step_dir / f).is_file()]
            files = ", ".join(existing) if existing else ", ".join(STEP_FILES[step_to_execute])

            pdca_branch = state.get("pdca_branch") or f"pdca/{number}-{slug}"
            version_links = ""
            try:
                repo_result = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    cwd=str(base_work_dir), capture_output=True, text=True,
                )
                if repo_result.returncode == 0 and existing:
                    repo_root = Path(repo_result.stdout.strip())
                    step_rel = step_dir.resolve().relative_to(repo_root)
                    file_links = [
                        f"[`{f}`]"
                        f"(https://github.com/{owner}/{repo}/blob/{pdca_branch}/"
                        f"{step_rel}/{f})"
                        for f in existing
                    ]
                    commit_result = subprocess.run(
                        ["git", "rev-parse", "--short", "HEAD"],
                        cwd=str(base_work_dir), capture_output=True, text=True,
                    )
                    commit_short = commit_result.stdout.strip() if commit_result.returncode == 0 else ""
                    commit_info = f" (`{commit_short}`)" if commit_short else ""

                    if push_ok:
                        version_links = (
                            f"\nBranch: `{pdca_branch}`{commit_info} | "
                            f"{' · '.join(file_links)}"
                        )
                    else:
                        version_links = (
                            f"\nBranch: `{pdca_branch}` (local only — push failed){commit_info} | "
                            f"{' · '.join(file_links)}"
                        )
            except Exception:
                pass

            if not version_links:
                if not push_ok:
                    version_links = f"\nBranch: `{pdca_branch}` (local only — push failed)"

            plan_hint = ""
            if step_to_execute == "plan":
                plan_hint = (
                    "\n\nReview **Design.md** for outstanding questions. "
                    "Add a pdca-refresh tag after providing input to regenerate."
                )

            _gh.add_comment(
                owner, repo, number,
                f"✅ **{step_to_execute.capitalize()}** completed. "
                f"Generated: {files}\n"
                f"See `docs/{number}-{slug}/{step_to_execute}/`"
                f"{version_links}{plan_hint}",
            )
            log.info("Issue #%d: step '%s' done", number, step_to_execute)

            if step_to_execute == "check":
                state["phase"] = "check-review"
                state["last_check"] = datetime.now(timezone.utc).isoformat()
                save_state(state_dir, number, state)
                _gh.add_comment(
                    owner, repo, number,
                    "✅ **Check** completed — review pending.\n\n"
                    "Please review **Review.md** and **Test.md**, then add "
                    "`#check-approved` in a new comment to proceed to the Act step.",
                )
                log.info("Issue #%d: Check done, awaiting #check-approved", number)
        else:
            if not ok:
                reason = "AI execution failed"
            else:
                missing = [f for f in STEP_FILES[step_to_execute] if not (step_dir / f).is_file()]
                reason = f"files not generated: {', '.join(missing)}"
            _gh.add_comment(
                owner, repo, number,
                f"❌ **{step_to_execute.capitalize()}** step failed ({reason}). "
                f"Fix the issue then add a pdca-refresh tag to retry.",
            )
            log.error("Issue #%d: step '%s' failed — %s", number, step_to_execute, reason)
    else:
        if state.get("current_step") != step_to_execute or refresh:
            branch = ensure_pdca_branch(state, state_dir, number, title, base_work_dir)
            if not branch:
                log.error("Issue #%d: cannot proceed without PDCA branch", number)
                _gh.add_comment(
                    owner, repo, number,
                    f"❌ **{step_to_execute.capitalize()}** failed: could not create or switch to PDCA branch.",
                )
                return

            slug = slugify(title)
            files = ", ".join(STEP_FILES[step_to_execute])
            branch_url = f"https://github.com/{owner}/{repo}/tree/{branch}"
            output_dir = f"docs/{number}-{slug}/{step_to_execute}/"

            plan_hint = ""
            if step_to_execute == "plan":
                plan_hint = (
                    "\n\nReview **Design.md** for outstanding questions. "
                    "Add a pdca-refresh tag after providing input to regenerate."
                )

            _gh.add_comment(
                owner, repo, number,
                f"🔄 **{step_to_execute.capitalize()}** step ready.\n"
                f"Run the `{SKILL_NAMES[step_to_execute]}` skill to generate: {files}\n"
                f"Branch: [`{branch}`]({branch_url}) → `{output_dir}`{plan_hint}",
            )
            state["current_step"] = step_to_execute
            state["last_check"] = datetime.now(timezone.utc).isoformat()
            save_state(state_dir, number, state)


# ── Main ─────────────────────────────────────────────────────────────────────


def parse_repo(repo_str: str) -> tuple[str, str]:
    parts = repo_str.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise argparse.ArgumentTypeError(f"Invalid repo format: '{repo_str}'. Use 'owner/repo'.")
    return (parts[0], parts[1])


def parse_issue_url(url: str) -> tuple[str, str, int]:
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)", url)
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid issue URL: '{url}'")
    return (m.group(1), m.group(2), int(m.group(3)))


def main() -> None:
    global _running, DEPLOY_BRANCH, CLAUDE_MODEL, CLAUDE_BASE_URL, SKILL_DIR
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    script_dir = Path(__file__).parent

    # Load config.ini — all values have built-in defaults so the file is optional
    cfg = load_config(script_dir)
    DEPLOY_BRANCH = cfg.get("runner", "deploy_branch")
    CLAUDE_MODEL = cfg.get("ai", "model")
    CLAUDE_BASE_URL = cfg.get("ai", "base_url")
    SKILL_DIR = script_dir / cfg.get("paths", "skills_dir")

    parser = argparse.ArgumentParser(
        prog="pdclaw",
        description="PDClaw — GitHub Issue PDCA Cycle Automation"
    )
    parser.add_argument("--config", default=str(script_dir / "config.ini"),
                        help="Config file path (default: config.ini next to pdclaw.py)")
    parser.add_argument("--repo", type=parse_repo, help="GitHub repository (owner/repo)")
    parser.add_argument("--issue", type=parse_issue_url, help="Single GitHub issue URL to watch")
    parser.add_argument(
        "--interval",
        type=int,
        default=cfg.getint("runner", "interval"),
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--work-dir",
        default=cfg.get("paths", "work_dir"),
        help="Repo root directory",
    )
    parser.add_argument(
        "--deploy-branch",
        default=DEPLOY_BRANCH,
        help="Target branch for #Deploy decision",
    )
    parser.add_argument(
        "--model",
        default=CLAUDE_MODEL,
        help="AI model identifier",
    )
    parser.add_argument(
        "--base-url",
        default=CLAUDE_BASE_URL,
        help="AI API base URL",
    )
    parser.add_argument(
        "--state-dir",
        default=cfg.get("paths", "state_dir"),
        help="Directory for internal state tracking",
    )
    parser.add_argument(
        "--auto-run",
        action="store_true",
        help="Auto-execute Claude Code skills via claude CLI",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one polling cycle and exit",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    parser.add_argument(
        "--memory-dir",
        default=cfg.get("paths", "memory_dir"),
        help="Directory for PDCA memory storage",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Disable memory system",
    )
    parser.add_argument(
        "--use-session",
        action="store_true",
        default=None,
        help="Use stateful Claude sessions (default: True)",
    )
    parser.add_argument(
        "--no-session",
        action="store_true",
        help="Disable stateful sessions, use stateless mode",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        default=True,
        help="Enable local web dashboard (default: on)",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Disable local web dashboard",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=9191,
        help="Dashboard HTTP port (default: 9191)",
    )
    parser.add_argument(
        "--metrics-dir",
        default=cfg.get("paths", "metrics_dir", fallback=".pdca/metrics"),
        help="Metrics storage directory (default: .pdca/metrics)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not args.repo and not args.issue:
        parser.error("Either --repo or --issue is required")

    # CLI overrides for globally-resolved config values
    DEPLOY_BRANCH = args.deploy_branch
    CLAUDE_MODEL = args.model
    CLAUDE_BASE_URL = args.base_url

    if not _gh.check_auth():
        log.warning("GitHub API not authenticated. Set GITHUB_TOKEN env var.")

    work_dir = Path(args.work_dir).resolve()

    # Resolve state/memory/metrics directories relative to work_dir by default.
    # If a path is already absolute it stays as-is; relative paths are resolved
    # under work_dir so everything lives inside the managed repository.
    _resolve_under_work = lambda p: Path(p) if Path(p).is_absolute() else work_dir / p
    state_dir = _resolve_under_work(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Initialize memory system
    global _memory
    if not args.no_memory:
        memory_dir = _resolve_under_work(args.memory_dir)
        _memory = PDCAMemory(memory_dir)
        log.info("Memory system initialized at %s", memory_dir)
    else:
        log.info("Memory system disabled")

    # Initialize metrics collector
    metrics_dir = _resolve_under_work(args.metrics_dir)
    _metrics_collector = init_metrics(metrics_dir)
    log.info("Metrics collector initialized at %s", metrics_dir)

    # Start dashboard (background HTTP server)
    _dashboard = None
    if args.dashboard and not args.no_dashboard:
        _dashboard = start_dashboard(
            host="0.0.0.0",
            port=args.dashboard_port,
            get_snapshot=_metrics_collector.snapshot,
            get_issue_detail=_metrics_collector.issue_detail,
        )

    # 确定是否使用会话模式
    use_session = (args.use_session if args.use_session is not None else True) and not args.no_session
    log.info(
        "PDClaw started (interval=%ds, auto_run=%s, work_dir=%s, "
        "state_dir=%s, memory_dir=%s, metrics_dir=%s, "
        "deploy_branch=%s, model=%s, use_session=%s, dashboard=%s)",
        args.interval, args.auto_run, work_dir,
        state_dir, memory_dir if not args.no_memory else "disabled",
        metrics_dir, DEPLOY_BRANCH, CLAUDE_MODEL, use_session,
        f"http://localhost:{args.dashboard_port}" if _dashboard else "off",
    )

    while _running:
        try:
            if args.issue:
                owner, repo, number = args.issue
                process_issue(owner, repo, number, state_dir, work_dir, args.auto_run, use_session)
            elif args.repo:
                owner, repo = args.repo
                issues = _gh.list_open_issues(owner, repo)
                for iss in issues:
                    if not _running:
                        break
                    process_issue(
                        owner, repo, iss["number"], state_dir, work_dir, args.auto_run, use_session
                    )
        except requests.RequestException as e:
            log.error("GitHub API error: %s", e)
        except Exception:
            log.exception("Unexpected error in polling cycle")

        # Record poll cycle for dashboard
        if _metrics_collector:
            _metrics_collector.record_poll_cycle()

        if args.once or not _running:
            break

        log.debug("Sleeping %ds...", args.interval)
        for _ in range(args.interval):
            if not _running:
                break
            time.sleep(1)

    # Save metrics summary and stop dashboard
    if _metrics_collector:
        _metrics_collector.save_summary()
    if _dashboard:
        _dashboard.stop()

    log.info("PDClaw stopped")

    # 清理所有会话
    clear_all_sessions()

    
if __name__ == "__main__":
    main()
