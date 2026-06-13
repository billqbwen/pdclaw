---
description: PDCA Do with Memory — implement changes, generate Change.md, update memory
---

You are executing the **Do** step of the PDCA cycle. This is the only step that modifies code.

## Memory Context

{{GLOBAL_MEMORY}}

{{ISSUE_MEMORY}}

## Workflow

1. **Read Design.md and Impact.md** — Understand the plan
2. **Review previous decisions** — From memory context above
3. **Implement changes** — Follow the design precisely
4. **Generate Change.md** — Document all changes
5. **Update Memory** — Record implementation notes, complete todos

## Output File: Change.md

Generate exactly **one file** with:

- **Summary** — One-paragraph overview
- **Files Modified** — Each changed file with description
- **Files Created** — New files with purpose
- **Files Deleted** — Removed files with rationale
- **Design Deviations** — Any differences from Design.md with reasoning
- **Dependencies** — New packages/libraries
- **Implementation Notes** — Edge cases, configuration, migrations
- **Outstanding Items** — Deferred work, known limitations

## Memory Commands

```html
<!-- Complete todos from Plan step -->
<!-- memory:issue:complete_todo pattern="unit tests" -->

<!-- Record implementation decisions -->
<!-- memory:issue:add_decision step="do" decision="Used async/await instead of callbacks for better error handling" -->

<!-- Add new todos discovered during implementation -->
<!-- memory:issue:add_todo item="Performance test with 10k concurrent users" -->

<!-- Record learning -->
<!-- memory:issue:add_learning learning="Redis connection pooling requires explicit timeout configuration" -->

<!-- Update global patterns if applicable -->
<!-- memory:global:add_pattern name="Async Error Handling" description="Use async/await with try/catch instead of callback errors" -->
```

Do NOT generate any other files.
