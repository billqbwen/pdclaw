---
description: PDCA Act with Memory — final assessment, generate Decision.md, archive learnings
---

You are executing the **Act** step of the PDCA cycle. **Do NOT modify code** — assessment only.

## Memory Context

{{GLOBAL_MEMORY}}

{{ISSUE_MEMORY}}

## Workflow

1. **Read all PDCA artifacts** — Design.md, Impact.md, Change.md, Review.md, Test.md
2. **Evaluate the cycle** — Did it achieve its goal?
3. **Generate Decision.md** — Final decision
4. **Update Memory** — Archive learnings, update global patterns

## Output File: Decision.md

- **Executive Summary** — One-paragraph verdict
- **Cycle Recap** — Summary of plan, implementation, review, test
- **Go/No-Go Decision** — Go / No-Go / Conditional Go
- **Suggested Decision** — Your recommendation with reasoning
- **Rationale** — Supporting evidence
- **Lessons Learned** — What went well, what to improve
- **Next Steps** — Deployment plan or rollback guidance
- **Sign-Off** — Cycle complete confirmation

## Memory Commands

```html
<!-- Complete all remaining todos -->
<!-- memory:issue:complete_todo pattern="all" -->

<!-- Record final decision -->
<!-- memory:issue:add_decision step="act" decision="Go - changes approved for production deployment" -->

<!-- Archive key learnings to global memory -->
<!-- memory:global:add_lesson lesson="PDCA cycle effective for complex refactoring; Plan step took 2 iterations" issue_ref="#{{ISSUE_NUMBER}}" -->

<!-- Update project context if architecture changed -->
<!-- memory:global:update_project key="architecture" value="Microservices with event-driven communication" -->

<!-- Record reusable pattern discovered -->
<!-- memory:global:add_pattern name="Event Sourcing Pattern" description="Use event store for audit-critical operations" issue_ref="#{{ISSUE_NUMBER}}" -->
```

Do NOT generate any other files.
