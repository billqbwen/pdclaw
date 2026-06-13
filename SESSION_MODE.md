# Session Mode

PDClaw supports **Stateful Session Mode** to maintain context across Plan → Do → Check → Act steps for the same issue.

## Key Features

### 1. Issue Isolation
- Each issue has its own independent session
- New issues automatically create fresh sessions with no cross-contamination

### 2. Cross-Step Context

```
Issue #42 session flow:
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│  Plan   │ ──→ │   Do    │ ──→ │  Check  │ ──→ │   Act   │
│ (step 1)│     │ (step 2)│     │ (step 3)│     │ (step 4)│
└─────────┘     └─────────┘     └─────────┘     └─────────┘
     │                │                │                │
     └────────────────┴────────────────┴────────────────┘
                    Shared session context
```

### 3. Control Tags

| Tag | Function |
|------|------|
| `#pdca-new-session` | Reset current issue's session — next run uses fresh context |
| `#pdca-reset` | Full reset — state + memory + session |

## Usage

### Default Mode (Recommended)

```bash
# Session mode enabled by default
python pdclaw.py --repo owner/repo --auto-run
```

### Disable Session Mode

```bash
# Use traditional stateless mode
python pdclaw.py --repo owner/repo --auto-run --no-session
```

### Control via GitHub Issue

**Reset current issue session:**
```markdown
Need to restart analysis with a fresh session.
#pdca-new-session
```

**Full reset (state + memory + session):**
```markdown
Completely reset this issue.
#pdca-reset
```

## Session Storage

```
.pdca_state/
└── {issue_number}/
    ├── state.json           # PDCA state
    ├── claude_session.json  # AI session history
    └── conversation.jsonl   # Full conversation log
```

## Comparison

| Feature | Stateless Mode | Session Mode |
|---------|---------------|-------------|
| Step continuity | ❌ Independent calls | ✅ Maintains context |
| Token efficiency | Low (repeated context) | High (progressive) |
| Cross-issue isolation | ✅ Natural | ✅ Explicit |
| Debug difficulty | Simple | Moderate (session files) |
| Complexity | Low | Medium |

## Troubleshooting

**Session not working?**
1. Check `.pdca_state/{issue}/claude_session.json` exists
2. Look for "Loaded session for issue #X" in logs
3. Use `#pdca-new-session` to force reset

**Need full cleanup?**
```bash
rm -rf .pdca_state/*/claude_session.json
```
