#!/usr/bin/env python3
"""
PDClaw — GitHub Issue PDCA Cycle Automation

Polls GitHub issues for PDCA tags and executes Plan-Do-Check-Act cycles.

Tags in GitHub issue bodies:
  #pdca-start                — start PDCA process (maps to Plan step)
  #plan-approved             — trigger the Plan step
  #do-approved               — trigger the Do step
  #check-approved            — trigger the Check step
  #act-approved              — trigger the Act step
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
    "act": ["Decision.md"],
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


def get_step_from_tags(tags: set[str]) -> str | None:
    """Map a tag to its associated step regardless of completion status."""
    if "#plan-approved" in tags or "#pdca-start" in tags:
        return "plan"
    if "#do-approved" in tags:
        return "do"
    if "#check-approved" in tags:
        return "check"
    if "#act-approved" in tags:
        return "act"
    return None


def resolve_next_step(tags: set[str], completed_steps: list[str]) -> str | None:
    """Determine which PDCA step to execute based on approval chain and state.

    Semantics:
      #pdca-start          → execute Plan (initial trigger)
      #plan-approved       → Plan confirmed by user; advance to Do
      #do-approved         → Do confirmed by user; advance to Check
      #check-approved      → Check confirmed by user; advance to Act
      #act-approved        → Act confirmed; nothing left to execute

    Each -approved tag also implies the step itself should run if it hasn't yet.
    Sequential ordering is enforced: a step only runs if all predecessors are done.
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
        confirmed_order.append("check")

    if "#check-approved" in tags:
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

# Patterns indicating meaningful user input (answers, updates, feedback)
MEANINGFUL_INPUT_PATTERNS = re.compile(
    r"(?i)(answer|answered|here is|here's|updated|fix|fixed|resolved|yes|no|"
    r"#pdca-|\?|question|feedback|comment|response|reply|provided|added)",
    re.DOTALL,
)

# Comments starting with these prefixes will NOT be processed even if they contain tags
# Use to discuss tags without triggering actions: "what about #pdca-refresh?"
IGNORE_COMMENT_PREFIXES = ("[skip]", "<!--", "noreview:", "no-action:", "# no-trigger")
IGNORE_COMMENT_PATTERNS = re.compile(r"^\s*(\[skip\]|<!--|noreview:|no-action:|#\s*no-trigger)", re.IGNORECASE)

# Minimum content length to consider as meaningful (avoid empty/very short comments)
MIN_COMMENT_LENGTH = 10


def is_meaningful_comment(comment: dict, assignee_login: str | None = None) -> bool:
    """Check if a comment contains meaningful input that warrants AI processing.

    Filters out:
    - Comments from PDClaw itself (via hidden marker)
    - Very short/empty comments
    - Bot/auto-generated comments
    """
    body = comment.get("body") or ""
    author = comment.get("user", {}).get("login", "")

    # Skip comments from PDClaw itself
    if _gh.is_pdca_runner_comment(comment):
        return False

    # Skip comments with ignore prefix (allows discussing tags without triggering)
    if IGNORE_COMMENT_PATTERNS.search(body):
        return False

    # Skip very short comments
    clean_body = re.sub(r"\[.*?\]\(.*?\)|[#*`>_\-~]|```[\s\S]*?```", "", body).strip()
    if len(clean_body) < MIN_COMMENT_LENGTH:
        return False

    # Skip known bot accounts
    if author.endswith("[bot]") or author in ("web-flow", "github-actions[bot]"):
        return False

    # If author is the assignee, consider meaningful (they're providing updates)
    if assignee_login and author == assignee_login:
        return True

    # If comment contains PDCA tags, it's meaningful regardless of author
    if get_tags(body):
        return True

    # Otherwise, require meaningful content patterns
    return bool(MEANINGFUL_INPUT_PATTERNS.search(body))


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
    }


def save_state(state_dir: Path, issue_number: int, state: dict) -> None:
    state_file = state_dir / str(issue_number) / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True))


# ── Path / slug utilities ────────────────────────────────────────────────────


def slugify(text: str, max_len: int = 80) -> str:
    """Convert text to a filesystem-safe slug."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return s[:max_len]


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


def run_skill(
    skill_name: str,
    issue: dict,
    work_dir: Path,
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
        reset_session(issue_number)
        log.info("[AI] Session reset for issue #%d (new_session=True)", issue_number)

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
            session = get_session(issue_number, work_dir, CLAUDE_MODEL, CLAUDE_BASE_URL)
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
            _mt = get_metrics()
            if _mt:
                _mt.record_ai_call(
                    issue_number=issue_number,
                    step=step_name,
                    success=success,
                    elapsed_sec=elapsed,
                    model=CLAUDE_MODEL,
                    output_chars=output_len,
                )

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
            _mt2 = get_metrics()
            if _mt2:
                _mt2.record_ai_call(
                    issue_number=issue_number,
                    step=step_name,
                    success=False,
                    elapsed_sec=time.time() - t0 if 't0' in dir() else 0,
                    model=CLAUDE_MODEL,
                    output_chars=0,
                )
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
        _mt3 = get_metrics()
        if _mt3:
            _mt3.record_ai_call(
                issue_number=issue_number,
                step=step_name,
                success=(result.returncode == 0),
                elapsed_sec=elapsed,
                model=CLAUDE_MODEL,
                output_chars=output_len,
            )

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
        _mt4 = get_metrics()
        if _mt4:
            _mt4.record_ai_call(
                issue_number=issue_number,
                step=step_name,
                success=False,
                elapsed_sec=timeout,
                model=CLAUDE_MODEL,
                output_chars=0,
            )
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
        log.info("Nothing new to commit (no staged changes)")
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

    # Push
    try:
        subprocess.run(
            ["git", "push"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            timeout=120,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL,
        )
        log.info("Git push successful")
    except subprocess.TimeoutExpired:
        log.warning("git push timed out after 120s — files committed locally but not pushed")
        return False
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr or "")
        # New branch needs upstream set
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
                log.info("Git push successful (new branch)")
            except subprocess.TimeoutExpired:
                log.warning("git push timed out after 120s")
                return False
            except subprocess.CalledProcessError as e2:
                s2 = e2.stderr.decode() if isinstance(e2.stderr, bytes) else str(e2.stderr or "")
                log.warning("git push failed (non-fatal): %s", s2[:200])
                return False
        else:
            log.warning("git push failed (non-fatal): %s", stderr[:200])
            return False

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
        reset_session(number)
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
        # 仅重置 Claude 会话，不清除状态和记忆
        reset_session(number)
        state["new_session"] = True  # 标记下次执行使用新会话
        save_state(state_dir, number, state)
        _gh.add_comment(
            owner, repo, number,
            f"🆕 **New Session** — Claude session for issue #{number} has been reset. "
            f"Next PDCA step will start with a fresh context."
        )
        log.info("Issue #%d: new Claude session requested", number)
        return True

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
    head_text = f"## Decision: "

    if "#deploy" in tags:
        branch = state.get("pdca_branch", "")
        target = DEPLOY_BRANCH
        # Detect uncommitted changes that would block git checkout
        _dirty = False
        _stashed = False
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(base_work_dir), capture_output=True, text=True, timeout=10,
            )
            _dirty = bool(status.stdout.strip())

            if _dirty:
                subprocess.run(
                    ["git", "stash", "push", "--include-untracked", "-m", "pdca-deploy-stash"],
                    cwd=str(base_work_dir), capture_output=True, timeout=30,
                )
                _stashed = True

            # Try checking out the target branch; create from base/main if missing
            checkout = subprocess.run(
                ["git", "checkout", target],
                cwd=str(base_work_dir), capture_output=True, text=True,
            )
            if checkout.returncode != 0:
                base = state.get("base_branch", "main")
                log.info("Deploy: target '%s' not found, creating from '%s'", target, base)
                subprocess.run(
                    ["git", "checkout", "-b", target, base],
                    cwd=str(base_work_dir), check=True, capture_output=True,
                )
            subprocess.run(
                ["git", "merge", branch, "--no-edit"],
                cwd=str(base_work_dir), check=True, capture_output=True, timeout=30,
            )
            subprocess.run(
                ["git", "push", "-u", "origin", "HEAD"],
                cwd=str(base_work_dir), check=True, capture_output=True, timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                stdin=subprocess.DEVNULL,
            )
            log.info("Issue #%d: deployed — merged %s into %s", number, branch, target)

            # Return to the pdca branch and restore work-in-progress
            subprocess.run(
                ["git", "checkout", branch],
                cwd=str(base_work_dir), capture_output=True, timeout=30,
            )
            if _stashed:
                subprocess.run(
                    ["git", "stash", "pop"],
                    cwd=str(base_work_dir), capture_output=True, timeout=30,
                )

            _gh.add_comment(
                owner, repo, number,
                f"{head_text}**Deploy** — merged `{branch}` into `{target}` and pushed.",
            )
        except Exception as e:
            log.error("Issue #%d: deploy failed — %s", number, e)
            _gh.add_comment(
                owner, repo, number,
                f"{head_text}**Deploy** failed: {e}",
            )
        state["phase"] = None
        if "act" not in state.get("completed_steps", []):
            state.setdefault("completed_steps", []).append("act")
        state["current_step"] = "act"
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        save_state(state_dir, number, state)
        _mt8 = get_metrics()
        if _mt8:
            _mt8.mark_issue_done(number)
        return True

    if "#fix" in tags:
        # Reset to re-enter the Do step — keep Plan but remove Do and Check
        state["completed_steps"] = ["plan"]
        state["current_step"] = None
        state["phase"] = None
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        reset_session(number)
        save_state(state_dir, number, state)
        _gh.add_comment(
            owner, repo, number,
            f"{head_text}**Fix** — cycle reset to Do step. "
            f"Review the feedback above, then add `#plan-approved` to re-enter the Do step.",
        )
        log.info("Issue #%d: fix cycle started (reset to Do step)", number)
        return True

    if "#fallback" in tags:
        branch = state.get("pdca_branch", "")
        base_branch = state.get("base_branch", "main")
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
            _gh.add_comment(
                owner, repo, number,
                f"{head_text}**Fallback** — reverted to `{base_branch}` and deleted `{branch}`.",
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
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state_dir, issue_number, state)

    try:
        # Check if branch exists locally
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        if result.returncode == 0:
            subprocess.run(
                ["git", "checkout", branch],
                cwd=str(repo_root), check=True, capture_output=True,
            )
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
        return branch
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr or "")
        log.error("Branch operation failed: %s", stderr[:200])
        return None


def process_issue(
    owner: str,
    repo: str,
    number: int,
    state_dir: Path,
    base_work_dir: Path,
    auto_run: bool,
    use_session: bool = True,
) -> None:
    """Evaluate and act on a single issue's PDCA state."""
    try:
        issue = _gh.get_issue(owner, repo, number)
    except requests.RequestException as e:
        log.warning("Cannot fetch issue #%d: %s", number, e)
        return

    # Fetch comments — tags are only read from comments, not issue body
    try:
        comments = _gh.get_issue_comments(owner, repo, number)
    except requests.RequestException:
        comments = []

    state = load_state(state_dir, number)
    log.info("[Poll] Issue #%d — state: current_step=%s, completed=%s, status=%s",
             number, state.get("current_step"), state.get("completed_steps"), state.get("status"))

    # Track issue in metrics
    _mt = get_metrics()
    if _mt:
        _mt.ensure_issue(number, issue.get("title", ""))

    # 新 Issue: 没有 PDCA 状态 → 确保全新会话
    is_new_issue = state.get("status") is None and not state.get("completed_steps")
    if is_new_issue:
        log.info("Issue #%d: new issue detected, ensuring fresh session", number)
        reset_session(number)

    # Re-opened issue: GitHub is open but PDCA state says closed → resume
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
        # Diagnostic: check for approval keywords even if regex didn't match
        for kw in ("do-approved", "check-approved", "act-approved"):
            if kw in body.lower() and f"#{kw}" not in ctags:
                log.warning("[Poll]   comment[%d] contains '%s' but regex didn't match — raw snippet: %s",
                            i, kw, body[:300].replace("\n", "\\n"))

    # #pdca-refresh: detect from comments.  We use three strategies:
    #   1. New human comment with #pdca-refresh (standard path)
    #   2. Any #pdca-refresh comment whose timestamp is newer than
    #      last_check — catches the case where last_check was bumped
    #      by a no-op cycle.
    #   3. If #pdca-refresh is present but no approval tag can advance
    #      the state, force refresh — the user's only intent is to
    #      re-run the current step.
    refresh = False
    if "#pdca-refresh" in tags:
        new_human, _ = get_new_human_comments(state, comments)
        log.info("[Refresh] #pdca-refresh in tags — new_human_comments=%d", len(new_human))
        refresh = any(
            "#pdca-refresh" in get_tags(c.get("body", ""))
            for c in new_human
        )
        # Fallback: check timestamps
        if not refresh and state.get("last_check"):
            last_check_ts = _parse_ts(state["last_check"])
            for c in comments:
                created_at = c.get("created_at", "")
                if not created_at:
                    continue
                comment_ts = _parse_ts(created_at)
                if comment_ts > last_check_ts and "#pdca-refresh" in get_tags(c.get("body", "")):
                    refresh = True
                    log.info("[Refresh] Found #pdca-refresh in comment at %s (after last_check %s)", created_at, state["last_check"])
                    break
        # Last resort: #pdca-refresh is present but strategies 1 & 2
        # didn't match.  This can happen because last_check was bumped
        # past the comment.  We force refresh ONLY if the approval chain
        # also can't advance — i.e., the user's only actionable tag is
        # #pdca-refresh.  If an approval tag can still drive a step
        # transition, that takes priority and we don't force refresh.
        # However, if all approval-driven steps are already completed,
        # the #pdca-refresh is stale (it was consumed by a previous
        # cycle) and we should NOT re-trigger it.
        if not refresh:
            approval_next = resolve_next_step(tags, completed)
            if approval_next:
                # An approval tag can advance — it will be handled in
                # the main flow; no need to force refresh.
                log.info("[Refresh] #pdca-refresh found in tags but approval tag will advance to '%s' (no force needed)", approval_next)
            else:
                # No approval can advance.  Check whether current_step
                # is actually waiting for #pdca-refresh (i.e. the step
                # was just executed and the user hasn't added any new
                # #pdca-refresh since).  If last_check is newer than all
                # #pdca-refresh comments, the tag is stale.
                all_refresh_ts = []
                for c in comments:
                    if "#pdca-refresh" in get_tags(c.get("body", "")):
                        ts = _parse_ts(c.get("created_at", ""))
                        if ts:
                            all_refresh_ts.append(ts)
                last_check_ts = _parse_ts(state["last_check"]) if state.get("last_check") else None
                if all_refresh_ts and last_check_ts and max(all_refresh_ts) < last_check_ts:
                    log.info("[Refresh] #pdca-refresh comments are all older than last_check — stale, not forcing")
                else:
                    refresh = True
                    log.info("[Refresh] #pdca-refresh in tags, no approval can advance, and refresh is recent — forcing refresh")

    # ── Lifecycle tags ───────────────────────────────────────────────────
    if _handle_lifecycle_tags(state, tags, issue, owner, repo, number, state_dir, base_work_dir, comments):
        return

    # ── Decision phase ───────────────────────────────────────────────────
    # After Check completes, the runner asks for a decision instead of
    # proceeding to the Act step.  Decision tags (#deploy, #fix, #fallback)
    # are handled here, before step resolution, so they short-circuit the
    # normal approval chain.
    if state.get("phase") == "decision":
        if _handle_decision_tag(state, tags, issue, owner, repo, number, state_dir, base_work_dir):
            return
        # No valid decision tag yet — wait for user input
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        save_state(state_dir, number, state)
        log.info("Issue #%d: awaiting decision tag (Deploy / Fix / Fallback)", number)
        return

    # ── Fallback: decision tag after all steps completed ──────────────────
    # If all PDCA steps are done but a decision tag (#Deploy/#Fix/#Fallback)
    # appears in a new comment, process it directly.  This covers the case
    # where the Act step ran before the decision phase was implemented
    # (backward compatibility).
    if len(completed) >= len(PDCA_STEPS) and state.get("status") == "active":
        new_human, _ = get_new_human_comments(state, comments)
        new_tags = set()
        for c in new_human:
            new_tags |= get_tags(c.get("body", ""))
        decision_in_new = any(t in new_tags for t in ("#deploy", "#fix", "#fallback"))
        if decision_in_new:
            log.info("Issue #%d: post-completion decision tag in new comment — processing", number)
            if _handle_decision_tag(state, tags, issue, owner, repo, number, state_dir, base_work_dir):
                return
            # Tag exists but handler couldn't process it — still update last_check
            state["last_check"] = datetime.now(timezone.utc).isoformat()
            save_state(state_dir, number, state)
            return

    if state.get("status") not in (None, "active"):
        return

    # ── Resolve which step to execute ────────────────────────────────────
    step_to_execute: str | None = None

    # Approval tags take priority over #pdca-refresh: if the user wrote
    # both #do-approved and #pdca-refresh in the same comment, the
    # intention is to advance to the next step (check), not to re-run
    # the current one.
    approval_step = resolve_next_step(tags, completed)
    if approval_step:
        step_to_execute = approval_step
        log.info("[Approval] Approval tag detected — overriding refresh, step=%s", step_to_execute)
    elif refresh:
        # #pdca-refresh: re-execute the current step with new user input to
        # refine the output.  "Current step" is simply state["current_step"],
        # i.e. the step the issue is actually on.  If current_step is unset
        # (brand-new issue), fall back to the first PDCA step.
        step_to_execute = state.get("current_step") or PDCA_STEPS[0]
        log.info("[Refresh] #pdca-refresh detected — current_step=%s, resolved to execute=%s",
                 state.get("current_step"), step_to_execute)
    else:
        # Normal flow: follow the approval chain
        step_to_execute = resolve_next_step(tags, completed)

    log.info(
        "Issue #%-5d tags=%s to_execute=%s completed=%s status=%s",
        number,
        tags,
        step_to_execute or "—",
        completed,
        state.get("status", "active"),
    )

    if not step_to_execute:
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        store_issue_snapshot(state, issue)
        save_state(state_dir, number, state)
        return

    # ── New-activity check ───────────────────────────────────────────────
    triggered, reason, new_comments = has_new_activity(state, issue, comments)

    # Tags that drive a valid step transition count as activity even without
    # new comments.  This covers the case where the user added a tag comment
    # in a previous cycle but last_check was bumped past it (e.g. a no-op
    # poll cycle ran in between).
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

    # Post a "processing" comment so the user knows the runner is working
    step_label = step_to_execute.capitalize()
    _gh.add_comment(
        owner, repo, number,
        f"⚙️ **{step_label}** step in progress — please wait...",
    )

    # ── Execute step ─────────────────────────────────────────────────────
    title = issue["title"]
    step_dir = step_output_dir(base_work_dir, number, title, step_to_execute)
    step_dir.mkdir(parents=True, exist_ok=True)

    if auto_run:
        # Skip the context-hash check when refresh=True because the user
        # explicitly wants a re-run (e.g., #pdca-refresh).  The hash will
        # differ due to the new comment, but we still want to execute.
        if not refresh:
            # Check if we can skip AI execution (no context changes, files exist)
            skip_ai, skip_reason = should_skip_ai_execution(
                state, step_dir, step_to_execute, issue, comments
            )
            if skip_ai:
                log.info(
                    "Issue #%d: skipping %s — %s",
                    number,
                    SKILL_NAMES[step_to_execute],
                    skip_reason,
                )
                state["last_check"] = datetime.now(timezone.utc).isoformat()
                save_state(state_dir, number, state)
                return

        skill = SKILL_NAMES[step_to_execute]
        log.info(
            "Issue #%d: running %s → %s",
            number,
            skill,
            ", ".join(STEP_FILES[step_to_execute]),
        )

        # Create (Plan) or checkout (Do/Check/Act) the PDCA feature branch
        branch = ensure_pdca_branch(state, state_dir, number, title, base_work_dir)
        if not branch:
            log.error("Issue #%d: cannot proceed without PDCA branch", number)
            _gh.add_comment(
                owner, repo, number,
                f"❌ **{step_to_execute.capitalize()}** failed: could not create or switch to PDCA branch.",
            )
            return

        # Build consolidated context with emphasis on new comments
        extra_context = ""
        if step_to_execute == "plan":
            new_comment_ids = {c.get("id") for c in new_comments} if new_comments else set()
            extra_context = build_consolidated_context(issue, comments, new_comment_ids)

        # Determine working directory and output location
        # Do runs from project root so it can modify code;
        # other steps only generate docs so they run inside step_dir.
        if step_to_execute == "do":
            skill_cwd = base_work_dir
            extra_context += (
                f"\n\nPut Change.md into: {step_dir}"
            )
        else:
            skill_cwd = step_dir

        # Conversation continuity — reuse the same Claude session across
        # poll cycles for the same (issue, step) so the AI doesn't redo work.
        # /new-refresh or #pdca-new-session force a fresh start.
        new_session = "#pdca-new-session" in tags or bool(NEW_SESSION_RE.search(comment_text))
        conv_path = _conv_path(state_dir, number, step_to_execute)

        # 使用会话模式执行（同一 Issue 的步骤间保持上下文）
        ok, _ = run_skill(
            skill, issue, skill_cwd, extra_context, conv_path,
            new_session=new_session,
            use_session=use_session,  # 使用传入的会话设置
        )

        if ok and step_files_exist(step_dir, step_to_execute):
            prev_step = state.get("current_step")
            state["current_step"] = step_to_execute
            if step_to_execute not in completed:
                completed.append(step_to_execute)
            state["completed_steps"] = completed
            state["status"] = "active"
            state["last_check"] = datetime.now(timezone.utc).isoformat()
            save_state(state_dir, number, state)

            # Record state transition for metrics
            if _mt:
                _mt.record_state_transition(
                    number,
                    from_state=prev_step or "idle",
                    to_state=step_to_execute,
                )

            # Git commit + push for step output files
            generated = [step_dir / f for f in STEP_FILES[step_to_execute]]
            step_label = step_to_execute.capitalize()

            # For Do step, stage code changes before committing docs
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

            # Determine which output files actually exist — only generate
            # hyperlinks for files that were just created, not stale files
            # from a previous execution.
            existing = [f for f in STEP_FILES[step_to_execute] if (step_dir / f).is_file()]
            files = ", ".join(existing) if existing else ", ".join(STEP_FILES[step_to_execute])

            # Build version lineage: branch name + GitHub permalink per output file.
            # Derive the path relative to the git repo root — base_work_dir may
            # be a subdirectory of the repo (e.g. "ai-crm/docs/..." not "docs/...").
            # Use the PDCA branch name for a stable URL that points to the latest
            # version. Only include version links when git push succeeded — without
            # a successful push neither the commit nor the branch exist on GitHub.
            version_links = ""
            if push_ok:
                try:
                    pdca_branch = state.get("pdca_branch") or f"pdca/{number}-{slug}"
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
                        version_links = (
                            f"\nBranch: `{pdca_branch}` | "
                            f"{' · '.join(file_links)}"
                        )
                except Exception:
                    pass

            # For Plan step, add hint about outstanding questions
            plan_hint = ""
            if step_to_execute == "plan":
                plan_hint = (
                    "\n\nReview **Design.md** for outstanding questions. "
                    "Add a pdca-refresh tag after providing input to regenerate."
                )

            _gh.add_comment(
                owner,
                repo,
                number,
                f"✅ **{step_to_execute.capitalize()}** completed. "
                f"Generated: {files}\n"
                f"See `docs/{number}-{slug}/{step_to_execute}/`"
                f"{version_links}"
                f"{plan_hint}",
            )
            log.info("Issue #%d: step '%s' done", number, step_to_execute)

            # After Check step succeeds, enter decision phase asking user
            # for Deploy / Fix / Fallback instead of proceeding to Act.
            if step_to_execute == "check":
                state["phase"] = "decision"
                state["last_check"] = datetime.now(timezone.utc).isoformat()
                save_state(state_dir, number, state)
                _gh.add_comment(
                    owner, repo, number,
                    "**Act Step — Decision Required**\n\n"
                    "Please add one of these tags in a new comment:\n"
                    f"- `#Deploy` — Merge changes into the `{DEPLOY_BRANCH}` branch\n"
                    "- `#Fix` — Review feedback and start a fix cycle\n"
                    "- `#Fallback` — Revert all changes",
                )
                log.info("Issue #%d: Check done, entered decision phase", number)
        else:
            _gh.add_comment(
                owner,
                repo,
                number,
                f"❌ **{step_to_execute.capitalize()}** step failed. "
                f"Fix the issue then add a pdca-refresh tag to retry.",
            )
            log.error("Issue #%d: step '%s' failed", number, step_to_execute)
    else:
        # Manual mode — notify user once
        if state.get("current_step") != step_to_execute or refresh:
            files = ", ".join(STEP_FILES[step_to_execute])
            _gh.add_comment(
                owner,
                repo,
                number,
                f"🔄 **{step_to_execute.capitalize()}** step ready.\n"
                f"Run the `{SKILL_NAMES[step_to_execute]}` skill to generate: {files}",
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
        default=True,
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

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Initialize memory system
    global _memory
    if not args.no_memory:
        _memory = PDCAMemory(Path(args.memory_dir))
        log.info("Memory system initialized at %s", args.memory_dir)
    else:
        log.info("Memory system disabled")
    work_dir = Path(args.work_dir).resolve()

    # Initialize metrics collector
    metrics_dir = Path(args.metrics_dir)
    _metrics_collector = init_metrics(metrics_dir)
    log.info("Metrics collector initialized at %s", metrics_dir)

    # Start dashboard (background HTTP server)
    _dashboard = None
    if args.dashboard and not args.no_dashboard:
        _dashboard = start_dashboard(
            host="0.0.0.0",
            port=args.dashboard_port,
            get_snapshot=lambda: _metrics_collector.snapshot(),
        )

    # 确定是否使用会话模式
    use_session = args.use_session and not args.no_session
    log.info(
        "PDClaw started (interval=%ds, auto_run=%s, work_dir=%s, "
        "deploy_branch=%s, model=%s, use_session=%s, dashboard=%s)",
        args.interval, args.auto_run, work_dir,
        DEPLOY_BRANCH, CLAUDE_MODEL, use_session,
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
