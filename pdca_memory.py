#!/usr/bin/env python3
"""
PDCA Memory System — Persistent knowledge across PDCA cycles.

Supports:
- Global memory: project-wide knowledge, coding standards, patterns
- Issue memory: per-issue context, decisions, todos
- Step memory: per-step artifacts and learnings
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("pdca_memory")


class PDCAMemory:
    """Manages persistent memory for PDCA cycles."""

    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._global_cache: dict | None = None

    # ── Global Memory ─────────────────────────────────────────────────────────

    @property
    def global_memory_path(self) -> Path:
        return self.memory_dir / "global.json"

    def load_global(self) -> dict:
        """Load global project memory."""
        if self._global_cache is not None:
            return self._global_cache

        path = self.global_memory_path
        if path.exists():
            try:
                self._global_cache = json.loads(path.read_text())
                return self._global_cache
            except json.JSONDecodeError:
                log.warning("Corrupted global memory, starting fresh")

        # Initialize default global memory
        self._global_cache = {
            "version": "1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "project_context": {
                "description": "",
                "tech_stack": [],
                "architecture_patterns": [],
            },
            "coding_standards": {
                "style_guide": "",
                "naming_conventions": {},
                "file_organization": "",
            },
            "common_patterns": [],  # Reusable solutions
            "lessons_learned": [],  # Cross-issue learnings
            "frequent_issues": [],  # Common problems and solutions
        }
        self._save_global()
        return self._global_cache

    def _save_global(self) -> None:
        """Persist global memory to disk."""
        if self._global_cache is not None:
            self._global_cache["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.global_memory_path.parent.mkdir(parents=True, exist_ok=True)
            self.global_memory_path.write_text(
                json.dumps(self._global_cache, indent=2, sort_keys=True)
            )

    def update_global(self, updates: dict[str, Any]) -> None:
        """Update global memory with new information."""
        mem = self.load_global()
        self._deep_update(mem, updates)
        self._save_global()
        log.debug("Global memory updated")

    def get_global_context(self) -> str:
        """Get formatted global context for AI prompts."""
        mem = self.load_global()
        parts = [
            "## Project Context",
            f"Description: {mem['project_context']['description'] or 'Not set'}",
            f"Tech Stack: {', '.join(mem['project_context']['tech_stack']) or 'Not set'}",
            "",
            "## Coding Standards",
            mem['coding_standards']['style_guide'] or "Follow existing project conventions.",
            "",
        ]

        if mem['common_patterns']:
            parts.append("## Common Patterns")
            for p in mem['common_patterns'][-5:]:  # Last 5 patterns
                parts.append(f"- {p['name']}: {p['description']}")
            parts.append("")

        if mem['lessons_learned']:
            parts.append("## Recent Lessons Learned")
            for ll in mem['lessons_learned'][-3:]:
                parts.append(f"- {ll}")
            parts.append("")

        return "\n".join(parts)

    def add_pattern(self, name: str, description: str, issue_ref: str = "") -> None:
        """Add a reusable pattern to global memory."""
        mem = self.load_global()
        mem["common_patterns"].append({
            "name": name,
            "description": description,
            "issue_ref": issue_ref,
            "added_at": datetime.now(timezone.utc).isoformat(),
        })
        # Keep only last 20 patterns
        mem["common_patterns"] = mem["common_patterns"][-20:]
        self._save_global()

    def add_lesson(self, lesson: str, issue_ref: str = "") -> None:
        """Add a cross-issue lesson learned."""
        mem = self.load_global()
        mem["lessons_learned"].append(f"[{issue_ref}] {lesson}" if issue_ref else lesson)
        mem["lessons_learned"] = mem["lessons_learned"][-50:]  # Keep last 50
        self._save_global()

    # ── Issue Memory ──────────────────────────────────────────────────────────

    def _issue_path(self, issue_number: int) -> Path:
        return self.memory_dir / f"issue_{issue_number}.json"

    def load_issue(self, issue_number: int) -> dict:
        """Load memory for a specific issue."""
        path = self._issue_path(issue_number)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                log.warning(f"Corrupted issue memory for #{issue_number}")

        return {
            "issue_number": issue_number,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "context": {},  # Key facts about the issue
            "decisions": [],  # Decisions made during PDCA
            "todos": [],  # Outstanding items
            "learnings": [],  # Issue-specific learnings
            "related_issues": [],  # Related issue numbers
        }

    def _save_issue(self, issue_number: int, data: dict) -> None:
        """Persist issue memory."""
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        path = self._issue_path(issue_number)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, sort_keys=True)
        )

    def update_issue(self, issue_number: int, updates: dict[str, Any]) -> None:
        """Update issue memory."""
        mem = self.load_issue(issue_number)
        self._deep_update(mem, updates)
        self._save_issue(issue_number, mem)

    def get_issue_context(self, issue_number: int) -> str:
        """Get formatted issue context for AI prompts."""
        mem = self.load_issue(issue_number)
        parts = []

        if mem["context"]:
            parts.append("## Issue Context")
            for key, value in mem["context"].items():
                parts.append(f"- **{key}**: {value}")
            parts.append("")

        if mem["decisions"]:
            parts.append("## Previous Decisions")
            for d in mem["decisions"]:
                parts.append(f"- {d['step']}: {d['decision']}")
            parts.append("")

        if mem["todos"]:
            parts.append("## Outstanding Items")
            for t in mem["todos"]:
                status = "✓" if t.get("done") else "○"
                parts.append(f"- {status} {t['item']}")
            parts.append("")

        if mem["learnings"]:
            parts.append("## Learnings from This Issue")
            for l in mem["learnings"]:
                parts.append(f"- {l}")
            parts.append("")

        return "\n".join(parts) if parts else ""

    def add_decision(self, issue_number: int, step: str, decision: str) -> None:
        """Record a decision made during PDCA."""
        mem = self.load_issue(issue_number)
        mem["decisions"].append({
            "step": step,
            "decision": decision,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._save_issue(issue_number, mem)

    def add_todo(self, issue_number: int, item: str) -> None:
        """Add a todo item for an issue."""
        mem = self.load_issue(issue_number)
        mem["todos"].append({
            "item": item,
            "done": False,
            "added_at": datetime.now(timezone.utc).isoformat(),
        })
        self._save_issue(issue_number, mem)

    def complete_todo(self, issue_number: int, item_pattern: str) -> None:
        """Mark a todo as completed by pattern matching."""
        mem = self.load_issue(issue_number)
        for t in mem["todos"]:
            if not t["done"] and item_pattern.lower() in t["item"].lower():
                t["done"] = True
                t["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._save_issue(issue_number, mem)

    def add_learning(self, issue_number: int, learning: str) -> None:
        """Add an issue-specific learning."""
        mem = self.load_issue(issue_number)
        mem["learnings"].append(learning)
        self._save_issue(issue_number, mem)

    # ── Step Memory (Artifacts) ───────────────────────────────────────────────

    def save_step_artifact(
        self, issue_number: int, step: str, artifact_type: str, content: str
    ) -> Path:
        """Save a step artifact (extracted from AI output)."""
        step_dir = self.memory_dir / f"issue_{issue_number}" / step
        step_dir.mkdir(parents=True, exist_ok=True)

        path = step_dir / f"{artifact_type}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def load_step_artifact(self, issue_number: int, step: str, artifact_type: str) -> str:
        """Load a step artifact."""
        path = self.memory_dir / f"issue_{issue_number}" / step / f"{artifact_type}.md"
        if path.exists():
            return path.read_text()
        return ""

    # ── Memory Commands (for AI to update memory) ─────────────────────────────

    def parse_memory_commands(self, text: str) -> list[dict]:
        """Parse memory update commands from AI output.

        Commands format:
        <!-- memory:global:add_pattern name="..." description="..." -->
        <!-- memory:issue:add_decision step="plan" decision="..." -->
        <!-- memory:issue:add_todo item="..." -->
        <!-- memory:global:add_lesson lesson="..." -->
        <!-- memory:issue:complete_todo pattern="..." -->
        <!-- memory:issue:set_context key="..." value="..." -->
        """
        import re

        commands = []
        pattern = r'<!--\s*memory:(\w+):(\w+)\s+(.+?)\s*-->'

        for match in re.finditer(pattern, text, re.DOTALL):
            scope, action, attrs_str = match.groups()

            # Parse attributes
            attrs = {}
            attr_pattern = r'(\w+)="([^"]*)"'
            for attr_match in re.finditer(attr_pattern, attrs_str):
                attrs[attr_match.group(1)] = attr_match.group(2)

            commands.append({
                "scope": scope,
                "action": action,
                "attrs": attrs,
            })

        return commands

    def execute_commands(self, issue_number: int, commands: list[dict]) -> None:
        """Execute parsed memory commands."""
        for cmd in commands:
            scope = cmd["scope"]
            action = cmd["action"]
            attrs = cmd["attrs"]

            try:
                if scope == "global":
                    if action == "add_pattern":
                        self.add_pattern(
                            attrs.get("name", ""),
                            attrs.get("description", ""),
                            attrs.get("issue_ref", f"#{issue_number}"),
                        )
                    elif action == "add_lesson":
                        self.add_lesson(
                            attrs.get("lesson", ""),
                            attrs.get("issue_ref", f"#{issue_number}"),
                        )
                    elif action == "update_project":
                        self.update_global({
                            "project_context": {
                                attrs.get("key", "info"): attrs.get("value", "")
                            }
                        })

                elif scope == "issue":
                    if action == "add_decision":
                        self.add_decision(
                            issue_number,
                            attrs.get("step", "unknown"),
                            attrs.get("decision", ""),
                        )
                    elif action == "add_todo":
                        self.add_todo(issue_number, attrs.get("item", ""))
                    elif action == "complete_todo":
                        self.complete_todo(issue_number, attrs.get("pattern", ""))
                    elif action == "add_learning":
                        self.add_learning(issue_number, attrs.get("learning", ""))
                    elif action == "set_context":
                        mem = self.load_issue(issue_number)
                        mem["context"][attrs.get("key", "unknown")] = attrs.get("value", "")
                        self._save_issue(issue_number, mem)

                log.info(f"Memory command executed: {scope}:{action}")

            except Exception as e:
                log.warning(f"Failed to execute memory command {scope}:{action}: {e}")

    # ── Utility ───────────────────────────────────────────────────────────────

    def _deep_update(self, base: dict, updates: dict) -> None:
        """Recursively update nested dictionaries."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_update(base[key], value)
            else:
                base[key] = value

    def get_memory_summary(self, issue_number: int) -> str:
        """Get a summary of all memory for debugging."""
        lines = ["=== Global Memory ==="]
        global_mem = self.load_global()
        lines.append(f"Patterns: {len(global_mem.get('common_patterns', []))}")
        lines.append(f"Lessons: {len(global_mem.get('lessons_learned', []))}")

        lines.append(f"\n=== Issue #{issue_number} Memory ===")
        issue_mem = self.load_issue(issue_number)
        lines.append(f"Decisions: {len(issue_mem.get('decisions', []))}")
        lines.append(f"Todos: {len([t for t in issue_mem.get('todos', []) if not t.get('done')])} pending")
        lines.append(f"Learnings: {len(issue_mem.get('learnings', []))}")

        return "\n".join(lines)
