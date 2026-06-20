# Working Model — Two-Machine Architecture

> *How PDClaw bridges a Product Owner and an AI Engineer into a single automated delivery loop.*

## Overview

PDClaw adopts a **two-role, two-machine** working model that separates the *what to build* from the *how to build it*. A Product Owner (PO) drives requirements and decisions from one machine, while an AI Engineer machine runs the PDClaw engine to execute the full Plan-Do-Check-Act cycle automatically.

At the heart of the model is the **PDCA continuous-improvement ring** — every requirement goes through four stages, each powered by AI and gated by a human approval tag:

```
          ┌──────────────────────────────────────┐
          │                                      │
          │         ┌──────────┐                 │
          │         │   PLAN   │                 │
          │         │ Design + │                 │
          │    ┌────│ Impact   │────┐            │
          │    │    └──────────┘    │            │
          │    │  #pdca-start      │            │
          │    │         #plan-approved          │
          │    ▼                    ▼            │
          │  ┌──────────┐    ┌──────────┐       │
          │  │   ACT    │    │    DO    │       │
          │  │ Deploy / │    │ Implement│       │
          │  │ Fix /    │    │ Change   │       │
          │  │ Fallback │    └──────────┘       │
          │  └──────────┘         │             │
          │       ▲        #do-approved         │
          │       │               ▼             │
          │       │         ┌──────────┐        │
          │       │         │  CHECK   │        │
          │       └─────────│ Review + │        │
          │    #Deploy /    │ Test     │        │
          │    #Fix /       └──────────┘        │
          │    #Fallback   #check-approved      │
          │                                      │
          └──────────────────────────────────────┘
                     PDCA Ring (Tag-Gated)
```

**GitHub serves as the central portal** — every requirement, every AI artifact, every human decision, and every deployment record lives in one place, accessible to teammates anywhere in the world.

```
                         ┌──────────────────────────────────────┐
                         │         GitHub (Central Portal)       │
                         │                                      │
                         │  Issues ← requirements & #tags       │
                         │  PRs    ← AI-generated docs & diffs  │
                         │  Repo   ← skills, memory, metrics    │
                         │                                      │
                         └────┬────────────────────────────┬────┘
                              │                            │
                    poll + comment                    browse + tag
                              │                            │
                              ▼                            ▼
┌──────────────────────────────────────┐      ┌──────────────────────────────────────┐
│                                      │      │                                      │
│     🤖  AI Engineer Machine           │      │     🧑‍💻  PO (Anywhere)                 │
│                                      │      │                                      │
│  ┌────────────────────────────────┐  │      │  • Browser or GitHub mobile app     │
│  │   pdclaw.py (Core Engine)       │  │      │  • Create issues                   │
│  │                                │  │      │  • Add #pdca-start                  │
│  │  • Poll GitHub for new tags    │  │      │  • Review AI output in PRs          │
│  │  • Parse tag → dispatch step   │  │      │  • Approve: #plan-approved          │
│  │  • Manage Git branches          │  │      │  • Decide: #Deploy / #Fix          │
│  │  • Post summary comments        │  │      │                                      │
│  │  • Serve local dashboard        │  │      │                                      │
│  └───────────┬────────────────────┘  │      │                                      │
│              │                       │      │                                      │
│              ▼                       │      │                                      │
│  ┌────────────────────────────────┐  │      │                                      │
│  │   Claude Code Agent             │  │      │                                      │
│  │   (claude CLI)                  │  │      │                                      │
│  │                                │  │      │                                      │
│  │  • Stateful sessions per issue │  │      │                                      │
│  │  • Loads skill definitions     │  │      │                                      │
│  │  • Parallel sub-agent execution │  │      │                                      │
│  │  • Memory-aware prompting      │  │      │                                      │
│  └───────────┬────────────────────┘  │      │                                      │
│              │                       │      │                                      │
│              ▼                       │      │                                      │
│  ┌────────────────────────────────┐  │      │                                      │
│  │   DeepSeek AI Engine            │  │      │                                      │
│  │   (deepseek-v4-flash)           │  │      │                                      │
│  │                                │  │      │                                      │
│  │  • Plan: Design.md + Impact.md │  │      │                                      │
│  │  • Do:   Change.md             │  │      │                                      │
│  │  • Check: Review.md + Test.md  │  │      │                                      │
│  │  • Act:   Decision.md          │  │      │                                      │
│  └────────────────────────────────┘  │      │                                      │
│                                      │      │                                      │
└──────────────────────────────────────┘      └──────────────────────────────────────┘
```

---

## Role 1: PO Machine (Human-in-the-Loop)

The Product Owner operates from their own machine — nothing more than a GitHub account and a web browser is required.

### Responsibilities

| Action | How | When |
|--------|-----|------|
| **Start requirement** | Create a GitHub issue describing the feature/bug/change | Any time |
| **Kick off PDCA** | Comment `#pdca-start` on the issue | After writing the issue |
| **Review Plan** | Read `Design.md` and `Impact.md` in the PR | After PDClaw completes the Plan step |
| **Approve Plan** | Comment `#plan-approved` | When design looks good |
| **Review Implementation** | Read `Change.md` diff summary | After the Do step |
| **Approve Do** | Comment `#do-approved` | When changes look correct |
| **Review Check** | Read `Review.md` and `Test.md` | After the Check step |
| **Make Decision** | Comment `#Deploy`, `#Fix`, or `#Fallback` | After Check passes |

### Key Benefit

The PO never touches code, never runs scripts, never manages infrastructure. They work entirely through **GitHub issue comments with hashtags**. This is the lowest possible barrier to entry for driving AI-powered development.

---

## Role 2: AI Engineer Machine (Automated Execution)

The AI Engineer machine runs as a background service and handles everything technical — no human babysitting required.

### Stack

```
pdclaw.py (Python)          ← Orchestrator: polling, state machine, Git ops
       │
       ├─▶ Claude Code Agent (claude CLI)   ← AI session manager & skill executor
       │         │
       │         └─▶ DeepSeek API (deepseek-v4-flash)   ← LLM inference engine
       │
       ├─▶ GitHub API                          ← Issue/PR reading & commenting
       ├─▶ Git (local repo)                    ← Branch creation, commit, push, merge
       ├─▶ Memory System (.pdca/memory/)       ← Persistent knowledge base
       ├─▶ Metrics (.pdca/metrics/)            ← Runtime telemetry
       └─▶ Dashboard (localhost:9191)          ← Real-time monitoring UI
```

### How It Works

1. **Polling loop** — `pdclaw.py` polls GitHub issues every N seconds (default: 180s)
2. **Tag detection** — When a new tag comment is found (e.g., `#pdca-start`), the state machine determines which PDCA step to run
3. **Skill loading** — The corresponding skill file (`skills/pdca-plan.md`, `pdca-do.md`, etc.) is loaded from disk
4. **Memory injection** — Global coding standards + issue-specific context are injected into the prompt
5. **AI execution** — The prompt is dispatched via the `claude` CLI (Claude Code Agent) which connects to DeepSeek as the AI engine
6. **Session persistence** — Stateful sessions preserve context across Plan → Do → Check for the same issue
7. **Artifact generation** — AI output is parsed into structured markdown files (`Design.md`, `Change.md`, etc.)
8. **Git commit & push** — Files are committed to a `pdca/<issue#>-<slug>` feature branch and pushed
9. **Summary comment** — PDClaw posts a summary comment on the issue, then waits for the next approval tag

### The Claude Code Agent ↔ DeepSeek Bridge

PDClaw uses **Claude Code** (`claude` CLI) as the AI agent framework, but routes all inference through **DeepSeek's API** (via the Anthropic-compatible endpoint). This means:

- **Claude Code** provides: session management, sub-agent parallelization, skill/tool orchestration, and a mature agent runtime
- **DeepSeek** provides: cost-efficient LLM inference with competitive code generation quality

The bridge is configured via environment variables and `config.ini`:

```ini
[ai]
model = deepseek-v4-flash
base_url = https://api.deepseek.com/anthropic
```

```bash
export DEEPSEEK_API_KEY=sk_your_key_here
# Claude Code sends requests to ANTHROPIC_BASE_URL → DeepSeek's Anthropic-compatible endpoint
```

---

## Key Benefits of This Model

### 1. Clear Separation of Concerns

| Concern | Owner | Tool |
|---------|-------|------|
| What to build | PO | GitHub Issues |
| How to build it | AI Engineer | PDClaw + Claude Code + DeepSeek |
| Is it correct? | PO | Review generated docs, approve |
| Should it ship? | PO | `#Deploy` decision tag |

No role confusion. The PO defines requirements and makes decisions. The machine executes.

### 2. Zero Infrastructure for the PO

The PO needs only:
- A GitHub account
- A web browser
- Knowledge of ~10 hashtags

No IDE, no terminal, no Python, no API keys. This makes AI-powered development accessible to product managers, designers, QA engineers, and domain experts who don't write code.

### 3. Human-in-the-Loop by Design

PDClaw **never auto-approves**. Every step transition (`#plan-approved` → `#do-approved` → `#check-approved`) requires an explicit human tag. The deployment decision (`#Deploy` / `#Fix` / `#Fallback`) is always manual. This ensures:

- Quality gates are enforced at every stage
- The PO retains full control over what gets merged
- Bad AI output is caught before it reaches production

### 4. Cost-Efficient AI via DeepSeek

By routing through DeepSeek's API instead of Anthropic's native endpoint, the model achieves:

- Significantly lower per-token cost for code generation tasks
- Comparable code quality for most PDCA step outputs
- The Claude Code agent framework still provides session management and orchestration

### 5. Git-Native, Audit-Ready

All AI-generated artifacts live in your repository on feature branches. Every change is:

- **Traceable** — `git log` shows who approved what and when
- **Reviewable** — standard PR review workflow on generated docs
- **Reversible** — `#Fallback` deletes the branch and reverts everything
- **CI-compatible** — generated test files can trigger your existing CI pipeline

### 6. Asynchronous, Non-Blocking

The PO creates an issue and tags `#pdca-start`. PDClaw picks it up on the next poll cycle (within 3 minutes by default). The PO can work on other tasks while the AI Engineer machine processes the step. When ready, PDClaw posts a comment — the PO reviews at their convenience. This is true asynchronous collaboration between human and machine.

### 7. Scalable Without Adding Headcount

One AI Engineer machine can handle multiple issues concurrently (each in its own isolated session). As the team grows and more issues are created, PDClaw scales linearly — more issues just means more polling cycles. No need to hire more engineers for repetitive code review, testing, or documentation tasks.

### 8. Learning System via Memory

The Memory System captures lessons from completed cycles into global memory. Subsequent issues benefit from past learnings:

```
Issue #10: "Always use async/await for API calls"
  → Stored in global memory
  → Injected into prompt for Issue #42
  → AI automatically applies the pattern
```

This creates a compounding knowledge effect — the system gets smarter with every completed PDCA cycle.

### 9. GitHub as the Single Source of Truth — Centralized AI Delivery Architecture

All AI software delivery artifacts are **centrally managed in GitHub** — no fragmented toolchain, no scattered dashboards, no tribal knowledge silos.

```
                         ┌─────────────────────────────────────────┐
                         │             GitHub Repository            │
                         │                                         │
     PO in Tokyo ───────▶│  Issues    ← requirements & decisions   │
     PO in London ──────▶│  Comments  ← #pdca-start, #plan-approved│
     PO in SF ──────────▶│  PRs       ← AI-generated docs & diffs  │
                         │  Branches  ← pdca/<issue#>-<slug>       │
     AI Machine #1 ─────▶│  Memory    ← global knowledge base      │
     AI Machine #2 ─────▶│  Skills    ← pdca-{plan,do,check,act}   │
                         │  Metrics   ← .pdca/metrics/             │
                         │                                         │
                         └─────────────────────────────────────────┘
```

**Everything lives in one place:**

| Artifact | Where in GitHub | Who Consumes It |
|----------|----------------|-----------------|
| Requirements & user stories | Issues (body + comments) | AI Engineer (prompt context) |
| PDCA workflow state | Issue comments (`#tags`) | PDClaw state machine |
| AI-generated design docs | PR files (`Design.md`, `Impact.md`) | PO (browser review) |
| AI-generated implementation | PR files (`Change.md`) + code diff | PO + reviewers |
| AI-generated test plans | PR files (`Review.md`, `Test.md`) | PO + CI pipeline |
| Deployment decisions | Issue comments (`#Deploy`, `#Fix`) | PDClaw + Git history |
| Organizational knowledge | `pdca/memory/` (global memory) | All future AI cycles |
| Skill definitions | `skills/pdca-*.md` | PDClaw engine |
| Delivery metrics | `.pdca/metrics/` | Dashboard + team retrospectives |

**Key architectural advantages:**

- **Zero tool sprawl** — No Jira, no Confluence, no separate CI dashboard. GitHub is the requirements tool, the review tool, the deployment log, and the knowledge base — all in one.
- **Built-in portal** — The GitHub repository itself becomes the team portal. Any teammate anywhere in the world opens the repo and immediately sees: active PDCA cycles, pending approvals, AI output history, and deployment records.
- **Universal accessibility** — GitHub is available on every device (browser, mobile app, API). PO in Tokyo reviews `Design.md` on their phone during a commute; PO in London approves `#plan-approved` from a tablet; AI Machine in AWS polls the same repo. Zero VPN, zero intranet dependency.
- **Permission model built-in** — GitHub's existing org/team permissions control who can create issues, who can approve, who can merge. No custom RBAC to build or maintain.
- **Full audit trail** — Every requirement, every AI-generated file, every human decision, every deployment is permanently recorded in Git history. Compliance and retrospectives are trivial.

### 10. Scales Across Teammates and Locations — No Ceiling

The two-machine model is designed to scale **horizontally** — add more people, more machines, more repos without architectural changes.

```
Week 1:  1 PO, 1 Repo, 1 AI Machine

    🧑‍💻 Tokyo ──#pdca-start──▶ [repo/frontend] ──▶ 🤖 Machine A


Week 4:  5 POs across 3 timezones, 3 Repos, 2 AI Machines

    🧑‍💻 Tokyo  ──#pdca-start──▶ [repo/frontend] ──▶ 🤖 Machine A
    🧑‍💻 London ──#pdca-start──▶ [repo/backend]  ──▶ 🤖 Machine B
    🧑‍💻 SF     ──#pdca-start──▶ [repo/mobile]   ──▶ 🤖 Machine A
    🧑‍💻 Sydney ──#plan-approved▶ [repo/frontend]      (review only)
    🧑‍💻 Berlin ──#Deploy───────▶ [repo/backend]       (decision only)


Week 12: 20+ POs, 10 Repos, N AI Machines — same architecture

    • Each repo is self-contained (issues + skills + memory + state)
    • Each AI Machine can watch multiple repos
    • No cross-repo coordination needed — GitHub is the sync point
    • Timezone differences become an advantage: 24-hour delivery pipeline
```

**Scaling properties:**

| Dimension | How It Scales |
|-----------|---------------|
| **Teammates** | Any number of POs can create issues and tag approvals. GitHub's collaboration model handles this natively — no user licenses, no seat limits beyond repo access. |
| **Repositories** | One AI Machine can poll N repos (each configured via `--repo`). Multi-repo setups are just multiple `pdclaw.py` processes with different `config.ini` files. |
| **AI Machines** | Stateless design means machines are interchangeable. Run 1, 2, or 10 AI Machines behind a load balancer — they all read/write the same GitHub repo. Session state lives on disk per-machine; memory is shared via Git. |
| **Locations** | GitHub is the universal portal. PO in Tokyo starts a cycle, PO in London reviews it 8 hours later, PO in SF deploys it. The AI Machine doesn't care where the tag came from. |
| **Timezones** | Asynchronous by nature. PO in Asia tags `#pdca-start` at end of day → AI Machine runs overnight → PO in Europe reviews `Design.md` in the morning. True follow-the-sun delivery. |
| **Onboarding** | New teammate = GitHub invite + 10 hashtags to learn. No IDE setup, no environment config, no "works on my machine" issues. Ready to drive AI-powered development in under 10 minutes. |

**The GitHub repo IS the delivery portal:**

- Open the repo → see every active PDCA cycle on the Issues tab
- Click an issue → see the full conversation: requirement → AI output → human decisions
- Open a PR → review AI-generated code and docs side by side
- Check Git history → trace every deployment back to its original issue and approver

No separate portal to build, no dashboard to sync, no SSO to configure. GitHub already solved identity, access control, notifications, search, and mobile access — PDClaw inherits all of it for free.

---

## Comparison: Traditional vs. PDClaw Model

| Aspect | Traditional Workflow | PDClaw Two-Machine Model |
|--------|---------------------|--------------------------|
| Requirement intake | Jira/Trello + sync meetings | GitHub Issue + `#pdca-start` |
| Design review | PR review meetings | PO reads `Design.md` in browser |
| Implementation | Engineer writes code | AI generates via `#do-approved` |
| Code review | Async PR review | AI generates `Review.md` + `Test.md` |
| Testing | Manual or CI-only | AI-generated test plan, human-reviewed |
| Deployment decision | Release manager approval | PO tags `#Deploy` |
| Rollback | Ops intervention | PO tags `#Fallback` |
| Knowledge retention | Tribal knowledge / wikis | Persistent Memory System |
| **Central portal** | Jira + Confluence + GitHub + Slack + CI dashboard | **GitHub only** — one repo, one URL, zero tool sprawl |
| **Scaling model** | Hire more engineers per project | Add more POs + AI Machines; architecture unchanged |
| **Cross-location** | Sync meetings, timezone pain | Async tags, follow-the-sun, GitHub is always on |

---

## Getting Started with the Two-Machine Model

### PO Machine Setup

1. Ensure you have write access to the target GitHub repository
2. Learn the [PDCA Workflow Tags](./README.md#pdca-workflow-tags)
3. Start creating issues and tagging `#pdca-start`

### AI Engineer Machine Setup

```bash
# 1. Clone the repo and install dependencies
git clone <your-repo>
cd pdclaw
pip install -r requirements.txt

# 2. Set credentials (never in config.ini)
export GITHUB_TOKEN=ghp_your_token_here
export DEEPSEEK_API_KEY=sk_your_key_here

# 3. Install Claude Code CLI
# https://claude.ai/code

# 4. Run as a background service
nohup python pdclaw.py --repo owner/repo --auto-run --interval 180 \
  > pdca.log 2>&1 &

# 5. Open dashboard to monitor
open http://localhost:9191
```

---

## See Also

- [README.md](./README.md) — Full PDClaw documentation and tag reference
- [SESSION_MODE.md](./SESSION_MODE.md) — How stateful AI sessions work
- [MEMORY_SYSTEM.md](./MEMORY_SYSTEM.md) — Persistent knowledge base details
- [config.ini](./config.ini) — AI engine and polling configuration
