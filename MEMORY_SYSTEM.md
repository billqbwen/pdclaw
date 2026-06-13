# PDCA Memory System

Persistent memory for PDClaw — shares context across issues and cycles.

## Features

- **Global Memory**: Cross-issue knowledge — project context, coding standards, patterns, lessons learned
- **Issue Memory**: Per-issue context — decisions, todos, learnings
- **Memory Commands**: AI can update memory via HTML comments in its output
- **CLI Management**: Command-line tools to manage memory content

## Quick Start

### 1. Initialize Memory

```bash
# Interactive initialization
python pdca_memory_cli.py init

# Or manually create
mkdir -p .pdca/memory
cat > .pdca/memory/global.json << 'EOF'
{
  "version": "1.0",
  "project_context": {
    "description": "Your project description",
    "tech_stack": [],
    "architecture_patterns": []
  },
  "coding_standards": {
    "style_guide": "Follow existing conventions",
    "naming_conventions": {},
    "file_organization": "Group by feature"
  },
  "common_patterns": [],
  "lessons_learned": [],
  "frequent_issues": []
}
EOF
```

### 2. Run PDClaw (auto-uses memory)

```bash
# Memory enabled by default
python pdclaw.py --repo owner/repo --auto-run

# Disable memory
python pdclaw.py --repo owner/repo --auto-run --no-memory

# Custom memory directory
python pdclaw.py --repo owner/repo --auto-run --memory-dir ./my-memory
```

### 3. View Memory

```bash
# Global memory summary
python pdca_memory_cli.py show

# Specific issue memory
python pdca_memory_cli.py show 123
```

## Memory Commands

AI uses HTML comments in output to update memory:

```html
<!-- memory:global:add_pattern
     name="Strategy Pattern"
     description="Use when multiple algorithms need to be interchangeable"
     issue_ref="#123" -->

<!-- memory:global:add_lesson
     lesson="Always validate webhook signatures before processing"
     issue_ref="#123" -->

<!-- memory:issue:add_decision
     step="plan"
     decision="Use Redis for session storage" -->

<!-- memory:issue:add_todo
     item="Add unit tests for payment service" -->

<!-- memory:issue:complete_todo
     pattern="unit tests" -->

<!-- memory:issue:add_learning
     learning="Redis connection pooling requires explicit timeout" -->

<!-- memory:issue:set_context
     key="primary_module"
     value="payment-service" -->
```

## CLI Reference

### Global Memory

```bash
python pdca_memory_cli.py global get project_context.description
python pdca_memory_cli.py global set project_context.description "New description"
python pdca_memory_cli.py global add-pattern --name "Pattern Name" --description "Description"
python pdca_memory_cli.py global add-lesson --lesson "Lesson learned"
```

### Issue Memory

```bash
python pdca_memory_cli.py issue 123 add-todo --item "Review error handling"
python pdca_memory_cli.py issue 123 complete-todo --pattern "error handling"
python pdca_memory_cli.py issue 123 add-decision --step plan --decision "Use async/await"
python pdca_memory_cli.py issue 123 add-learning --learning "New insight"
python pdca_memory_cli.py issue 123 set-context --key module --value auth
python pdca_memory_cli.py issue 123 show
```

## File Structure

```
.pdca/
├── memory/
│   ├── global.json           # Global project memory
│   ├── issue_123.json        # Issue #123 memory
│   └── 123/                  # Issue #123 artifacts
│       ├── plan/
│       └── do/
└── skills/                   # Enhanced skill files
    ├── pdca-plan.md
    ├── pdca-do.md
    ├── pdca-check.md
    └── pdca-act.md
```

## AI Integration

Memory is automatically injected into skill files:

1. **Global Memory** → Project context, coding standards, patterns
2. **Issue Memory** → Previous decisions, todos, learnings

Skill file placeholder example:

```markdown
## Memory Context

The following context has been accumulated from previous PDCA cycles:

{{GLOBAL_MEMORY}}

{{ISSUE_MEMORY}}
```

Placeholders are replaced at runtime with actual memory content.

## Best Practices

1. **Review global memory regularly** — Clean up outdated patterns and lessons
2. **Complete todos promptly** — Update todo status after Do/Check steps
3. **Record reusable patterns** — Add general solutions during Plan/Act steps
4. **Cross-issue learning** — Archive learnings to global memory during Act step

## Troubleshooting

### Memory not taking effect

Check logs for: `Memory system initialized at .pdca/memory`

### Skill files not found

Ensure `skills/` directory exists with `.md` files.

### Memory commands not executed

Verify AI output contains correct HTML comment format.
