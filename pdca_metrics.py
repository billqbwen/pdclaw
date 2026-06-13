#!/usr/bin/env python3
"""
PDCA Metrics Collector — 运行时指标收集与持久化

Tracks:
- AI call latency per step
- AI call token estimates
- Step success/failure rates
- PDCA cycle duration per issue
- GitHub API rate-limit usage
- Git operation history
- Issue state transitions
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("pdca_metrics")


@dataclass
class AICallRecord:
    timestamp: str
    issue_number: int
    step: str
    success: bool
    elapsed_sec: float
    model: str
    output_chars: int
    estimated_tokens: int  # rough: chars / 4

    def to_dict(self) -> dict:
        return {
            "ts": self.timestamp,
            "issue": self.issue_number,
            "step": self.step,
            "ok": self.success,
            "elapsed": round(self.elapsed_sec, 1),
            "model": self.model,
            "chars": self.output_chars,
            "tokens": self.estimated_tokens,
        }


@dataclass
class IssueLifecycle:
    issue_number: int
    title: str
    created_at: str
    completed_at: str = ""
    transitions: list[dict] = field(default_factory=list)
    total_ai_calls: int = 0
    total_ai_sec: float = 0.0


class MetricsCollector:
    """Singleton metrics collector, safe for concurrent access."""

    def __init__(self, metrics_dir: Path):
        self.metrics_dir = Path(metrics_dir)
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        # In-memory counters (reset on restart)
        self.ai_calls: list[AICallRecord] = []
        self.step_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"success": 0, "failure": 0, "total_sec": 0}
        )
        self.issues: dict[int, IssueLifecycle] = {}
        self.git_ops: list[dict] = []
        self.state_changes: list[dict] = []
        self.rate_limits: list[dict] = []
        self.poll_cycles: int = 0
        self.start_time = datetime.now(timezone.utc)

        # Load persisted summary if any
        self._load_summary()

    # ── Record methods ────────────────────────────────────────────────────

    def record_ai_call(
        self,
        issue_number: int,
        step: str,
        success: bool,
        elapsed_sec: float,
        model: str,
        output_chars: int,
    ) -> None:
        record = AICallRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            issue_number=issue_number,
            step=step,
            success=success,
            elapsed_sec=elapsed_sec,
            model=model,
            output_chars=output_chars,
            estimated_tokens=max(1, output_chars // 4),
        )
        with self._lock:
            self.ai_calls.append(record)
            key = "success" if success else "failure"
            self.step_stats[step][key] += 1
            self.step_stats[step]["total_sec"] += int(elapsed_sec)
            if issue_number in self.issues:
                self.issues[issue_number].total_ai_calls += 1
                self.issues[issue_number].total_ai_sec += elapsed_sec
        self._append_jsonl("ai_calls.jsonl", record.to_dict())

    def record_state_transition(
        self, issue_number: int, from_state: str, to_state: str
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "issue": issue_number,
            "from": from_state,
            "to": to_state,
        }
        with self._lock:
            self.state_changes.append(entry)
            if issue_number in self.issues:
                self.issues[issue_number].transitions.append(entry)

    def record_git_op(self, op: str, result: str, detail: str = "") -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": op,
            "result": result,
            "detail": detail,
        }
        with self._lock:
            self.git_ops.append(entry)

    def record_rate_limit(self, remaining: int, limit: int) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "remaining": remaining,
            "limit": limit,
        }
        with self._lock:
            self.rate_limits.append(entry)

    def record_poll_cycle(self) -> None:
        with self._lock:
            self.poll_cycles += 1

    def ensure_issue(self, issue_number: int, title: str) -> None:
        with self._lock:
            if issue_number not in self.issues:
                self.issues[issue_number] = IssueLifecycle(
                    issue_number=issue_number,
                    title=title,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )

    def mark_issue_done(self, issue_number: int) -> None:
        with self._lock:
            if issue_number in self.issues:
                self.issues[issue_number].completed_at = (
                    datetime.now(timezone.utc).isoformat()
                )

    # ── Query methods ─────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a complete snapshot for the dashboard."""
        with self._lock:
            recent_calls = [
                c.to_dict() for c in self.ai_calls[-20:]
            ]
            active_issues = {
                str(num): {
                    "title": iss.title,
                    "created": iss.created_at,
                    "completed": iss.completed_at or None,
                    "calls": iss.total_ai_calls,
                    "total_sec": round(iss.total_ai_sec, 1),
                    "last_transition": (
                        iss.transitions[-1] if iss.transitions else None
                    ),
                }
                for num, iss in self.issues.items()
                if not iss.completed_at
            }
            completed_issues = {
                str(num): {
                    "title": iss.title,
                    "created": iss.created_at,
                    "completed": iss.completed_at,
                    "calls": iss.total_ai_calls,
                    "total_sec": round(iss.total_ai_sec, 1),
                }
                for num, iss in self.issues.items()
                if iss.completed_at
            }
            return {
                "uptime_sec": int(
                    (datetime.now(timezone.utc) - self.start_time).total_seconds()
                ),
                "poll_cycles": self.poll_cycles,
                "active_issues": active_issues,
                "completed_issues": completed_issues,
                "recent_calls": recent_calls,
                "step_stats": {
                    step: dict(stats) for step, stats in self.step_stats.items()
                },
                "rate_limits": self.rate_limits[-5:],
            }

    def issue_detail(self, issue_number: int) -> dict[str, Any] | None:
        """Return detailed info for a single issue."""
        with self._lock:
            iss = self.issues.get(issue_number)
            if not iss:
                return None
            issue_calls = [
                c.to_dict() for c in self.ai_calls
                if c.issue_number == issue_number
            ]
            return {
                "number": iss.issue_number,
                "title": iss.title,
                "created": iss.created_at,
                "completed": iss.completed_at or None,
                "calls": iss.total_ai_calls,
                "total_sec": round(iss.total_ai_sec, 1),
                "transitions": iss.transitions,
                "ai_calls": issue_calls,
            }

    # ── Persistence ───────────────────────────────────────────────────────

    def _append_jsonl(self, filename: str, record: dict) -> None:
        try:
            path = self.metrics_dir / filename
            with open(path, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _load_summary(self) -> None:
        path = self.metrics_dir / "summary.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self.poll_cycles = data.get("poll_cycles", 0)
            except Exception:
                pass

    def save_summary(self) -> None:
        try:
            path = self.metrics_dir / "summary.json"
            path.write_text(json.dumps(self.snapshot(), indent=2, ensure_ascii=False))
        except Exception:
            pass


# Module-level singleton placeholder — set by pdclaw.py at startup
_metrics: MetricsCollector | None = None


def get_metrics() -> MetricsCollector | None:
    return _metrics


def init_metrics(metrics_dir: Path) -> MetricsCollector:
    global _metrics
    _metrics = MetricsCollector(metrics_dir)
    return _metrics
