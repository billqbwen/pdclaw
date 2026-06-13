#!/usr/bin/env python3
"""
PDCA Memory CLI — Manage PDCA memory from command line.

Usage:
    pdca_memory_cli.py init                    # Initialize global memory
    pdca_memory_cli.py show [issue#]           # Show memory summary
    pdca_memory_cli.py global get <key>        # Get global value
    pdca_memory_cli.py global set <key> <val>  # Set global value
    pdca_memory_cli.py issue <#> add-todo "..."     # Add todo
    pdca_memory_cli.py issue <#> complete-todo "..." # Complete todo
    pdca_memory_cli.py issue <#> add-learning "..." # Add learning
    pdca_memory_cli.py issue <#> add-decision <step> "..." # Add decision
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pdca_memory import PDCAMemory


def cmd_init(memory: PDCAMemory, _args: argparse.Namespace) -> int:
    """Initialize global memory with interactive prompts."""
    print("Initializing PDCA Global Memory...")

    mem = memory.load_global()

    print("\n--- Project Context ---")
    desc = input("Project description: ").strip()
    if desc:
        mem["project_context"]["description"] = desc

    tech = input("Tech stack (comma-separated): ").strip()
    if tech:
        mem["project_context"]["tech_stack"] = [t.strip() for t in tech.split(",")]

    print("\n--- Coding Standards ---")
    style = input("Style guide reference (or enter conventions): ").strip()
    if style:
        mem["coding_standards"]["style_guide"] = style

    memory._save_global()
    print("\n✓ Global memory initialized!")
    return 0


def cmd_show(memory: PDCAMemory, args: argparse.Namespace) -> int:
    """Show memory contents."""
    if args.issue:
        print(memory.get_memory_summary(args.issue))
        print("\n--- Issue Context ---")
        print(memory.get_issue_context(args.issue) or "(empty)")
    else:
        print("=== Global Memory ===")
        mem = memory.load_global()
        print(f"Project: {mem['project_context']['description'] or 'Not set'}")
        print(f"Tech Stack: {', '.join(mem['project_context']['tech_stack']) or 'Not set'}")
        print(f"Patterns: {len(mem['common_patterns'])}")
        print(f"Lessons: {len(mem['lessons_learned'])}")

        if mem['common_patterns']:
            print("\n--- Recent Patterns ---")
            for p in mem['common_patterns'][-5:]:
                print(f"  • {p['name']}: {p['description'][:60]}...")

        if mem['lessons_learned']:
            print("\n--- Recent Lessons ---")
            for ll in mem['lessons_learned'][-5:]:
                print(f"  • {ll[:80]}...")
    return 0


def cmd_global(memory: PDCAMemory, args: argparse.Namespace) -> int:
    """Manage global memory."""
    if args.action == "get":
        mem = memory.load_global()
        keys = args.key.split(".")
        value = mem
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                print(f"Key not found: {args.key}")
                return 1
        print(json.dumps(value, indent=2))

    elif args.action == "set":
        try:
            # Try to parse as JSON
            value = json.loads(args.value)
        except json.JSONDecodeError:
            # Treat as string
            value = args.value

        keys = args.key.split(".")
        updates = {}
        current = updates
        for k in keys[:-1]:
            current[k] = {}
            current = current[k]
        current[keys[-1]] = value

        memory.update_global(updates)
        print(f"✓ Set {args.key} = {value}")

    elif args.action == "add-pattern":
        memory.add_pattern(args.name, args.description)
        print(f"✓ Added pattern: {args.name}")

    elif args.action == "add-lesson":
        memory.add_lesson(args.lesson)
        print(f"✓ Added lesson")

    return 0


def cmd_issue(memory: PDCAMemory, args: argparse.Namespace) -> int:
    """Manage issue memory."""
    issue_num = args.issue_number

    if args.action == "add-todo":
        memory.add_todo(issue_num, args.item)
        print(f"✓ Added todo to issue #{issue_num}")

    elif args.action == "complete-todo":
        memory.complete_todo(issue_num, args.pattern)
        print(f"✓ Completed todo matching '{args.pattern}'")

    elif args.action == "add-learning":
        memory.add_learning(issue_num, args.learning)
        print(f"✓ Added learning to issue #{issue_num}")

    elif args.action == "add-decision":
        memory.add_decision(issue_num, args.step, args.decision)
        print(f"✓ Added decision to issue #{issue_num}")

    elif args.action == "set-context":
        mem = memory.load_issue(issue_num)
        mem["context"][args.key] = args.value
        memory._save_issue(issue_num, mem)
        print(f"✓ Set context {args.key} = {args.value}")

    elif args.action == "show":
        print(memory.get_issue_context(issue_num) or "(empty)")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PDCA Memory CLI")
    parser.add_argument(
        "--memory-dir",
        default=".pdca/memory",
        help="Memory directory (default: .pdca/memory)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init
    subparsers.add_parser("init", help="Initialize global memory")

    # show
    show_parser = subparsers.add_parser("show", help="Show memory contents")
    show_parser.add_argument("issue", type=int, nargs="?", help="Issue number")

    # global
    global_parser = subparsers.add_parser("global", help="Manage global memory")
    global_parser.add_argument("action", choices=["get", "set", "add-pattern", "add-lesson"])
    global_parser.add_argument("key", nargs="?", help="Key path (e.g., project_context.description)")
    global_parser.add_argument("value", nargs="?", help="Value to set")
    global_parser.add_argument("--name", help="Pattern name")
    global_parser.add_argument("--description", help="Pattern description")
    global_parser.add_argument("--lesson", help="Lesson text")

    # issue
    issue_parser = subparsers.add_parser("issue", help="Manage issue memory")
    issue_parser.add_argument("issue_number", type=int, help="Issue number")
    issue_parser.add_argument(
        "action",
        choices=["add-todo", "complete-todo", "add-learning", "add-decision", "set-context", "show"],
    )
    issue_parser.add_argument("--item", help="Todo item")
    issue_parser.add_argument("--pattern", help="Todo pattern to complete")
    issue_parser.add_argument("--learning", help="Learning text")
    issue_parser.add_argument("--step", help="PDCA step (plan/do/check/act)")
    issue_parser.add_argument("--decision", help="Decision text")
    issue_parser.add_argument("--key", help="Context key")
    issue_parser.add_argument("--value", help="Context value")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    memory = PDCAMemory(Path(args.memory_dir))

    commands = {
        "init": cmd_init,
        "show": cmd_show,
        "global": cmd_global,
        "issue": cmd_issue,
    }

    if args.command in commands:
        return commands[args.command](memory, args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
