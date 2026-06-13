---
description: PDCA Check with Memory — review implementation, generate Review.md and Test.md
---

You are executing the **Check** step of the PDCA cycle. **Do NOT modify code** — review only.

## Memory Context

{{GLOBAL_MEMORY}}

{{ISSUE_MEMORY}}

## Workflow

1. **Read all artifacts** — Design.md, Impact.md, Change.md
2. **Review code changes** — Inspect modified files
3. **Compare against design** — Check adherence
4. **Generate Review.md and Test.md** — Document findings
5. **Update Memory** — Record review findings, update todos

## Output Files

### 1. Review.md

- **Overview** — What was reviewed
- **Design Adherence** — Matches Design.md? Note deviations
- **Code Quality** — Readability, maintainability, error handling
- **Findings** — Issues classified as: Critical, Major, Minor, Suggestion
- **Security & Performance** — Concerns or regressions
- **Outstanding Items** — Unaddressed issues
- **Overall Assessment** — Approved / Needs Changes / Rejected

### 2. Test.md

- **Scope** — What was tested
- **Coverage** — Adequate? Untested paths?
- **Edge Cases** — Verified scenarios
- **Issues Found** — Bugs or regressions
- **Results** — Pass/fail counts

## Memory Commands

```html
<!-- Record review findings as decisions -->
<!-- memory:issue:add_decision step="check" decision="Code approved with minor style suggestions" -->

<!-- Add todos for required changes -->
<!-- memory:issue:add_todo item="Fix error handling in payment callback" -->
<!-- memory:issue:add_todo item="Add input validation for negative amounts" -->

<!-- Complete todos that are now verified -->
<!-- memory:issue:complete_todo pattern="error handling" -->

<!-- Record pattern if code demonstrates good practice -->
<!-- memory:global:add_pattern name="Input Validation" description="Validate at API boundary using Zod schemas" -->
```

Do NOT generate any other files.
