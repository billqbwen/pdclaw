---
description: PDCA Plan with Memory — analyze codebase, generate Design.md and Impact.md, update memory
---

You are executing the **Plan** step of the PDCA cycle.

## Memory Context

The following context has been accumulated from previous PDCA cycles and project history:

{{GLOBAL_MEMORY}}

{{ISSUE_MEMORY}}

## Workflow

1. **Read existing codebase** — Understand architecture, patterns, conventions
2. **Analyze requirements** — From issue description and comments
3. **Generate Design.md and Impact.md** — Document the plan
4. **Update Memory** — Record decisions, todos, and patterns

## Regeneration / Refresh

If this is a **regeneration** (the user has provided feedback, answered
outstanding questions, or added a #pdca-refresh tag), you MUST:

1. **Read the existing Design.md and Impact.md** first to understand what
   was already generated.
2. **Process all user comments** in the context — especially answers to
   outstanding questions (e.g. "A: for now, let's proceed free-text").
3. **Update the files** to incorporate the feedback:
   - Move answered questions from "Outstanding Questions" into the relevant
     design sections.
   - Remove or update the "Outstanding Questions" section to reflect only
     remaining unanswered items.
   - If all questions are answered, remove the section entirely or mark it
     as "All questions resolved".
   - Update Impact.md if the decisions change the scope of changes.

## Output Files

Generate exactly **two files** in the current working directory:

### 1. Design.md

Cover:
- **Functional Design** — What functionality changes
- **Architecture Design** — Component relationships
- **Technical Design** — Approach, libraries, data flow
- **UI Design** — Interface changes
- **Other Considerations** — Security, performance, observability
- **Outstanding Questions** — Unclear requirements needing input

### 2. Impact.md

List each file likely to change, grouped by module.

## Memory Commands

After generating files, include memory update commands as HTML comments:

```html
<!-- Record key decisions -->
<!-- memory:issue:add_decision step="plan" decision="Use Strategy pattern for payment processing" -->

<!-- Add todos for later steps -->
<!-- memory:issue:add_todo item="Verify error handling in edge cases" -->
<!-- memory:issue:add_todo item="Add unit tests for new service" -->

<!-- Set issue context -->
<!-- memory:issue:set_context key="primary_module" value="payment-service" -->

<!-- Record reusable pattern -->
<!-- memory:global:add_pattern name="Payment Strategy" description="Use Strategy pattern when multiple payment providers needed" issue_ref="#123" -->

<!-- Add cross-issue lesson -->
<!-- memory:global:add_lesson lesson="Always validate webhook signatures before processing" issue_ref="#123" -->
```

Do NOT generate any other files.
