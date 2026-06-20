# PDClaw 🦞

> *Like a precision claw machine — grabbing issues one by one — PDClaw automates **Plan-Do-Check-Act** cycles on GitHub issues using a tag-driven workflow and AI-powered skill execution.*

## Design Philosophy

PDClaw brings the PDCA (Plan-Do-Check-Act) continuous improvement methodology to software development. Instead of manually coordinating code reviews, testing, and deployment, you interact with GitHub issues through simple hashtag comments — and PDClaw orchestrates AI agents to execute each phase.

### Core Principles

- **Tag-driven**: No dashboards, no webhooks, no extra infrastructure. You comment `#pdca-start` on a GitHub issue and PDClaw picks it up.
- **AI-native**: Every PDCA step is executed by an AI agent with a dedicated skill definition (`skills/pdca-*.md`), producing structured output files.
- **Git-first**: All generated artifacts live in your repo (`docs/` directory) on a feature branch (`pdca/<issue#>-<slug>`). You review, approve, and merge just like any other code change.
- **Human in the loop**: PDClaw never auto-approves. You explicitly tag each step (`#plan-approved`, `#do-approved`, `#check-approved`) to advance. Decisions (`#Deploy`, `#Fix`, `#Fallback`) are always manual.
- **Separation of concerns**: Sensitive credentials stay in environment variables. Everything else uses CLI flags or `config.ini` — predictable, auditable, easy to onboard.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      GitHub Issue                           │
│  Comments: #pdca-start → #plan-approved → ... → #Deploy     │
└──────────────────────┬──────────────────────────────────────┘
                       │ polling
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                     pdclaw.py                               │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  State Mgmt  │  │  Tag Parser  │  │  Step Dispatcher │  │
│  │ (.pdca_state)│  │ (lifecycle + │  │  (plan/do/check/ │  │
│  │              │  │  step tags)  │  │   act/decision)  │  │
│  └──────────────┘  └──────────────┘  └────────┬─────────┘  │
│                                                │            │
│                     ┌──────────────────────────┘            │
│                     ▼                                       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                  AI Execution                         │  │
│  │  ┌─────────────────┐  ┌────────────────────────────┐ │  │
│  │  │  Session Mode   │  │  Stateless Mode (fallback) │ │  │
│  │  │  (stateful,     │  │  (one-shot claude call)   │ │  │
│  │  │   cross-step    │  │                            │ │  │
│  │  │   context)      │  │                            │ │  │
│  │  └─────────────────┘  └────────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────┘  │
│                     │                                       │
│  ┌──────────────────┴──────────────────────────────────┐   │
│  │              Supporting Systems                      │   │
│  │  ┌──────────────┐  ┌──────────────────────────────┐ │   │
│  │  │  Memory      │  │  Skill Definitions            │ │   │
│  │  │  (global +   │  │  skills/pdca-{plan,do,        │ │   │
│  │  │   per-issue) │  │  check,act}.md               │ │   │
│  │  └──────────────┘  └──────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    Git Repository                           │
│  pdca/<issue#>-<slug>/  ← feature branch                    │
│  └── docs/<issue#>-<slug>/<step>/  ← generated artifacts    │
└─────────────────────────────────────────────────────────────┘
```

### Component Map

| Component | File | Role |
|---|---|---|
| **Core Engine** | `pdclaw.py` | Polls GitHub, parses tags, dispatches PDCA steps, manages Git branches and state |
| **Memory System** | `pdca_memory.py` | Persistent global + per-issue knowledge base injected into AI prompts |
| **Memory CLI** | `pdca_memory_cli.py` | Command-line tools to inspect and manage memory |
| **Session Manager** | `pdca_claude_session.py` | Stateful AI sessions preserving context across steps for the same issue |
| **Metrics Collector** | `pdca_metrics.py` | Runtime metrics — AI call latency, step success rates, state transitions |
| **Dashboard** | `pdca_dashboard.py` | Local HTTP server + HTML UI for real-time monitoring |
| **Skill Definitions** | `skills/*.md` | Markdown templates defining how AI executes each PDCA step |
| **Configuration** | `config.ini` | Project-level settings (intervals, paths, model, deploy branch, dashboard) |

### Data Flow

```
Issue #pdca-start
  → Tag detected by polling loop
  → State machine resolves current step (plan)
  → Skill file loaded (skills/pdca-plan.md)
  → Memory injected (global + issue-specific context)
  → AI executes via claude CLI (session or stateless)
  → Output parsed, files written to docs/<issue#>-<slug>/plan/
  → Git commit + push to pdca/<issue#>-<slug> branch
  → PDClaw posts comment with summary, waits for next tag
```

### State Machine

```
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  ▼                                                              │
 [idle] ──#pdca-start──▶ [plan] ──#plan-approved──▶ [do]        │
   ▲                         │                        │          │
   │                         │ #pdca-refresh          │ #do-approved
   │                         ▼                        ▼          │
   │ #pdca-abort          [plan] (re-run)          [check]       │
   │ #pdca-reset             │                        │          │
   │                         │              #check-approved      │
   │                         │              #pdca-refresh         │
   │                         │                        ▼          │
   │                         │                  [check] (re-run) │
   │                         │                        │          │
   │                         │         Check success → decision  │
   │                         │              │         │          │
   │                         │     #Deploy  #Fix   #Fallback     │
   │                         │         │       │        │        │
   │                         │         ▼       │        │        │
   │                         │    [deploy]     │        ▼        │
   │                         │         │       │   [revert]      │
   │                         │         ▼       ▼        │        │
   │                         └──── [done] ◀── [do] ◀────┘        │
   │                                     │                       │
   └─────────────────────────────────────┘                       │
                              #pdca-close                        │

  Note: In normal flow, the Act step is replaced by the Decision
  Phase (#Deploy / #Fix / #Fallback). The #act-approved tag and
  pdca-act skill exist for scenarios where explicit Act execution
  is needed, but they are rarely used in practice.
```

---

## Prerequisites

- **Python 3.10+**
- **`requests`** library (`pip install requests`)
- **[Claude Code](https://claude.ai/code)** CLI installed and available as `claude` on PATH
- **GitHub token** with repo access → `GITHUB_TOKEN` or `GH_TOKEN`
- **DeepSeek API key** → `DEEPSEEK_API_KEY`

---

## Quick Start

```bash
# 1. Install
pip install requests

# 2. Set credentials
export GITHUB_TOKEN=ghp_your_token_here
export DEEPSEEK_API_KEY=sk_your_key_here

# 3. Run (recommend testing on a single issue first)
python pdclaw.py --issue https://github.com/owner/repo/issues/42 --once --auto-run --verbose
```

> Once verified, drop `--issue` and `--once` to poll the entire repo:
> ```bash
> python pdclaw.py --repo owner/repo --auto-run
> ```

---

## How It Works

### PDCA Workflow Tags

Add these tags as comments on a GitHub issue. PDClaw detects them and advances the cycle:

| Tag | Triggers Step | AI Skill | Generated Files |
|---|---|---|---|
| `#pdca-start` | **Plan** | `pdca-plan` | `Design.md`, `Impact.md` |
| `#plan-approved` | **Do** | `pdca-do` | `Change.md` |
| `#do-approved` | **Check** | `pdca-check` | `Review.md`, `Test.md` |
| `#check-approved` | **Check** | `pdca-check` | Runs Check, then enters decision phase |
| `#act-approved` | **Act** | `pdca-act` | `Decision.md`, `CodeDiff.md` (rarely used — decision phase replaces this) |

> **Why is Act rarely used?** After the Check step succeeds, PDClaw enters a *decision phase* that asks you to choose `#Deploy`, `#Fix`, or `#Fallback`. This manual decision replaces the automated Act step in normal flow. The `#act-approved` tag and `pdca-act` skill exist for backward compatibility and edge cases where explicit Act execution is desired.

### Decision Phase Tags

After a successful Check, PDClaw posts a comment asking for a decision. Reply with one of:

| Tag | Effect | Act Artifacts |
|---|---|---|
| `#Deploy` | Merge `pdca/<issue#>-<slug>` branch into the configured deploy branch | `Decision.md` + `CodeDiff.md` (with full diff report) |
| `#Fix` | Reset to Do step — include feedback in your comment as context for the next iteration | `Decision.md` |
| `#Fallback` | Revert all changes and delete the PDCA feature branch | `Decision.md` |

All decision tags generate `Decision.md` in `docs/<issue#>-<slug>/act/`. The `#Deploy` tag additionally generates `CodeDiff.md` containing the full code diff of what was merged.

### Control Tags

Available at any time during a cycle:

| Tag | Action |
|---|---|
| `#pdca-refresh` | Re-run the current step |
| `#pdca-abort` | Stop processing this issue |
| `#pdca-close` | Close the GitHub issue |
| `#pdca-skip` | Mark the issue as skipped |
| `#pdca-reset` | Clear all state and start fresh |
| `#pdca-new-session` | Reset AI session only (keep state and memory) |

### Step-by-Step Walkthrough

1. **Start**: Create a GitHub issue describing the change. Add a comment with `#pdca-start`.
2. **Plan**: PDClaw detects the tag → loads `skills/pdca-plan.md` → AI analyzes the codebase → generates `Design.md` and `Impact.md` → commits to `pdca/<issue#>-<slug>`.
3. **Review & Approve**: Check the generated docs. If satisfied, comment `#plan-approved`.
4. **Do**: PDClaw loads `skills/pdca-do.md` → AI implements changes → generates `Change.md` with a diff summary.
5. **Check**: Comment `#do-approved` → AI generates `Review.md` and `Test.md` for verification.
6. **Decide**: Comment `#check-approved` → Check runs again → PDClaw asks for final decision (`#Deploy` / `#Fix` / `#Fallback`).

All generated files are placed under `docs/<issue#>-<slug>/<step>/` on a `pdca/<issue#>-<slug>` feature branch. Nothing touches your main branch until you deploy.

---

## Configuration

### Priority Rule

```
CLI flags  >  config.ini  >  built-in defaults

Environment variables are for SENSITIVE CREDENTIALS ONLY.
```

### config.ini

Located next to `pdclaw.py`. All keys have sensible defaults — the file is optional.

```ini
[runner]
; Target branch for #Deploy decision (CLI --deploy-branch > this)
deploy_branch = main
; Polling interval in seconds (CLI --interval > this)
interval = 180

[paths]
; Working directory — your local git repo root (CLI --work-dir > this)
work_dir = .
; Internal state tracking (CLI --state-dir > this)
state_dir = .pdca_state
; Memory storage (CLI --memory-dir > this)
memory_dir = .pdca/memory
; Skill definitions relative to pdclaw.py
skills_dir = skills

[ai]
; Model identifier (CLI --model > this)
model = deepseek-v4-flash
; API endpoint (CLI --base-url > this)
base_url = https://api.deepseek.com/anthropic
```

Use `--config` to point to a different file:

```bash
python pdclaw.py --repo owner/repo --auto-run --config /path/to/production.ini
```

### CLI Reference

| Flag | Default | Description |
|---|---|---|
| `--config` | `config.ini` | Path to config file |
| `--repo` | — | GitHub repository `owner/repo` (required unless `--issue`) |
| `--issue` | — | Single issue URL to process |
| `--interval` | `180` | Polling interval in seconds |
| `--deploy-branch` | `main` | Target branch for `#Deploy` decision |
| `--model` | `deepseek-v4-flash` | AI model identifier |
| `--base-url` | `https://api.deepseek.com/anthropic` | AI API base URL |
| `--work-dir` | `.` | Local git repository root |
| `--state-dir` | `.pdca_state` | State tracking directory |
| `--memory-dir` | `.pdca/memory` | Memory storage directory |
| `--auto-run` | off | Auto-execute `claude` CLI (skip = manual mode) |
| `--once` | off | Run one polling cycle and exit |
| `--verbose` / `-v` | off | Enable debug-level logging |
| `--no-memory` | off | Disable the memory system |
| `--use-session` | on | Enable stateful AI sessions |
| `--no-session` | off | Force stateless mode |
| `--dashboard` | on | Enable local web dashboard |
| `--no-dashboard` | off | Disable dashboard |
| `--dashboard-port` | `9191` | Dashboard HTTP port |
| `--metrics-dir` | `.pdca/metrics` | Metrics storage directory |

### Environment Variables

**Only for credentials.** Everything else goes through CLI or `config.ini`.

| Variable | Description |
|---|---|
| `GITHUB_TOKEN` / `GH_TOKEN` | GitHub personal access token with repo scope |
| `DEEPSEEK_API_KEY` | DeepSeek API key for AI model access |

---

## Features

### Session Mode (default)

Stateful AI sessions preserve context across PDCA steps for the same issue. The Plan step's analysis feeds into Do, Do's implementation informs Check, and so on — without re-sending the entire conversation each time.

- Each issue gets an isolated session (no cross-contamination)
- Sessions persist on disk at `.pdca_state/<issue#>/claude_session.json`
- Use `--no-session` to fall back to stateless one-shot calls
- Tag `#pdca-new-session` to reset a session mid-cycle

See [SESSION_MODE.md](./SESSION_MODE.md) for details.

### Memory System

Persistent knowledge base that injects project context and historical learnings into every AI prompt.

- **Global memory** — coding standards, architecture patterns, lessons learned (shared across all issues)
- **Issue memory** — per-issue decisions, todos, context key-values
- AI can self-update memory via HTML comments in its output
- Managed through `pdca_memory_cli.py` or directly via the CLI

See [MEMORY_SYSTEM.md](./MEMORY_SYSTEM.md) for the full reference.

### Git Integration

Every PDCA cycle operates on its own feature branch:

```
pdca/42-add-payment-gateway/
└── docs/
    └── 42-add-payment-gateway/
        ├── plan/
        │   ├── Design.md
        │   └── Impact.md
        ├── do/
        │   └── Change.md
        ├── check/
        │   ├── Review.md
        │   └── Test.md
        └── act/
            ├── Decision.md
            └── CodeDiff.md    (Deploy only)
```

- PDClaw auto-creates the branch from the deploy branch
- Each step's output is committed and pushed
- `#Deploy` merges the PDCA branch back to the deploy branch
- `#Fallback` deletes the branch and reverts everything

### Local Dashboard

A built-in web dashboard provides real-time visibility into PDClaw's state — no external monitoring tools needed.

```
http://localhost:9191/          → Dashboard home
http://localhost:9191/api/status → JSON snapshot
```

**What you can see:**
- Active/completed issue counts
- Per-issue progress (which step, how many AI calls, total AI time)
- Recent AI call history (step, latency, success/failure, estimated tokens)
- Per-step success rates with progress bars
- Poll cycle count and uptime

```bash
# Dashboard enabled by default
python pdclaw.py --repo owner/repo --auto-run
# → Open http://localhost:9191

# Custom port
python pdclaw.py --repo owner/repo --auto-run --dashboard-port 8888

# Disable dashboard
python pdclaw.py --repo owner/repo --auto-run --no-dashboard
```

### Metrics Collection

All AI call timing, success rates, and state transitions are persisted to `.pdca/metrics/`:

```
.pdca/metrics/
├── ai_calls.jsonl     # Every AI call with latency, tokens, model
├── summary.json       # Rolling snapshot for the dashboard
└── daily_2026-06-13.json  # Daily archive (future)
```

- Zero overhead — metrics are collected in-memory and flushed to disk periodically
- Thread-safe — no impact on the polling loop
- Queryable via `http://localhost:9191/api/status`

---

## Best Practices

### Getting Started

1. **Initialize memory first** — run `python pdca_memory_cli.py init` to set up project context, coding standards, and common patterns. This gives the AI essential domain knowledge from day one.
2. **Start small** — test with `--issue <url> --once --auto-run --verbose` on a single well-scoped issue before enabling repository-wide polling.
3. **Review Plan output carefully** — the Plan step sets direction. Bad `Design.md` = bad implementation. Iterate with `#pdca-refresh` if needed.

### Daily Workflow

4. **Use `#Fix` liberally** — if the Do or Check output isn't right, use `#Fix` with specific feedback rather than aborting. The AI gets your feedback as context for the next attempt.
5. **Keep memory up to date** — review global memory periodically (`python pdca_memory_cli.py show`). Remove outdated patterns. Add new lessons learned from completed cycles.
6. **One issue, one concern** — each GitHub issue should address a single feature or fix. This keeps PDCA branches focused and mergeable.

### Production Setup

7. **Run as a background service** — use systemd, supervisor, or a simple `nohup` to keep PDClaw polling:

   ```bash
   nohup python pdclaw.py --repo owner/repo --auto-run --interval 300 \
     > pdca.log 2>&1 &
   ```

8. **Use a dedicated GitHub token** — create a machine account or fine-grained PAT with minimal scopes (repo read/write on the target repository only).
9. **Configure deploy branch** — set `deploy_branch` to your integration branch (e.g., `develop`) rather than `main` for an extra safety layer.
10. **Monitor the logs** — PDClaw logs every step, tag detection, and AI execution. Run with `-v` during initial setup to verify everything.

### Security

11. **Never commit credentials** — `GITHUB_TOKEN` and `DEEPSEEK_API_KEY` must always be environment variables. Never put them in `config.ini`.
12. **Review generated code** — AI output is a starting point, not the final answer. Always review `Change.md` and the actual code changes before deploying.
13. **Use `--no-memory` for sensitive repos** — if your codebase contains proprietary information, consider disabling memory to prevent cross-issue leakage.

---

## Project Structure

```
pdca-open-source/
├── pdclaw.py                # Core engine — polling, state machine, Git ops, AI dispatch
├── pdca_memory.py           # Persistent memory — global + per-issue knowledge base
├── pdca_memory_cli.py       # Memory management CLI
├── pdca_claude_session.py   # Stateful AI session manager
├── pdca_metrics.py          # Runtime metrics collector
├── pdca_dashboard.py        # Local web dashboard (HTTP + HTML UI)
├── config.ini               # Project configuration (optional, has defaults)
├── skills/                  # AI skill definitions (Markdown)
│   ├── pdca-plan.md         #   Plan step — analyze & design
│   ├── pdca-do.md           #   Do step — implement changes
│   ├── pdca-check.md        #   Check step — review & test
│   └── pdca-act.md          #   Act step — decision execution
├── MEMORY_SYSTEM.md         # Memory system documentation
├── SESSION_MODE.md          # Session mode documentation
├── CONTRIBUTING.md          # Contributing guide
└── LICENSE                  # License
```

---

## License

See [LICENSE](./LICENSE).
