# Impact Analysis — PDClaw Code Review

Files likely to change for each proposed enhancement, grouped by module.

---

## Core Engine — `pdclaw.py`

| # | Enhancement | Files & Lines |
|---|-------------|---------------|
| 1 | **Skill file loading** — Add `SkillLoader` class with proper read/missing-file handling | `pdclaw.py` — lines 740–760 (loading logic), 890–900 (skill resolve) |
| 2 | **Atomic state writes** — `save_state()` to write tmp file then rename | `pdclaw.py` — lines 664–668 |
| 3 | **t0 safety** — Replace `'t0' in dir()` with proper `try/finally` | `pdclaw.py` — ~line 912 |
| 4 | **Double metric recording** — Fix session→stateless fallback so metrics record only once | `pdclaw.py` — lines 860–915 |
| 5 | **Git commit/push split** — Separate commit and push into distinct phases | `pdclaw.py` — lines 1038–1116 |
| 6 | **TAG_RE simplification** — Consider dedicated parser for branch tag extraction | `pdclaw.py` — lines 82–87 |
| 7 | **Add `.gitignore`** — New file at repo root | `.gitignore` — new file (repo root) |

---

## Session Manager — `pdca_claude_session.py`

| # | Enhancement | Files & Lines |
|---|-------------|---------------|
| 1 | **Path config for reset_session** — Use configured `state_dir` instead of hardcoded `.pdca_state` | `pdca_claude_session.py` — lines 229–233 |
| 2 | **Conversation file growth** — Add rotation for conversation logs to prevent unbounded growth | `pdca_claude_session.py` — new rotation logic (~20 lines near the conversation file write section) |

---

## Memory System — `pdca_memory.py`

| # | Enhancement | Files & Lines |
|---|-------------|---------------|
| 1 | **File write safety** — Add atomic writes (tmp + rename) for memory JSON files | `pdca_memory.py` — lines 71–77 and 158–163 |

### No changes expected
- `pdca_memory_cli.py` — Thin CLI wrapper; no modifications required.

---

## Metrics — `pdca_metrics.py`

| # | Enhancement | Files & Lines |
|---|-------------|---------------|
| 1 | **JSONL rotation** — Add rotation for `ai_calls.jsonl` to cap file size | `pdca_metrics.py` — lines 241–247 |
| 2 | **Metric dedup** — Ensure metrics are recorded through a single code path | `pdca_metrics.py` — lines 86–116, 150–168, 249–263 |

---

## Dashboard — `pdca_dashboard.py`

| # | Enhancement | Files & Lines |
|---|-------------|---------------|
| 1 | **Bind address** — Change default bind from `0.0.0.0` to `127.0.0.1` | `pdca_dashboard.py` — lines 481, 517–518 |

### No other changes expected
The dashboard is well-architected; no further modifications appear necessary.

---

## Skills — `skills/` (new files)

| # | Enhancement | Files & Lines |
|---|-------------|---------------|
| 1 | **Plan step skill** | `skills/pdca-plan.md` — new file |
| 2 | **Do step skill** | `skills/pdca-do.md` — new file |
| 3 | **Check step skill** | `skills/pdca-check.md` — new file |
| 4 | **Act step skill** | `skills/pdca-act.md` — new file |

---

## Configuration

| # | Enhancement | Files & Lines |
|---|-------------|---------------|
| 1 | **`.gitignore`** | `.gitignore` — new file at repo root |
| 2 | **`config.ini`** | Minimal or no changes expected |

---

## Documentation

| # | Enhancement | Files & Lines |
|---|-------------|---------------|
| 1 | **`README.md`** | Update if stateless mode is deprecated (~lines documenting stateless operation) |
| 2 | **`SESSION_MODE.md`** | No changes expected |

---

## Tests — `tests/` (new directory)

| # | Enhancement | Files & Lines |
|---|-------------|---------------|
| 1 | **Core logic tests** | `tests/test_pdclaw.py` — new file |
| 2 | **State management tests** | `tests/test_state.py` — new file |
| 3 | **Memory system tests** | `tests/test_memory.py` — new file |
| 4 | **Metrics tests** | `tests/test_metrics.py` — new file |
