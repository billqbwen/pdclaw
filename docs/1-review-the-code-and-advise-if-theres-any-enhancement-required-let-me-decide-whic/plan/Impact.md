# Impact Analysis — PDClaw Codebase Enhancement

## Files Likely to Change

### Core Engine (pdclaw.py)
| File | Change Type | Enhancement |
|------|------------|-------------|
| `pdclaw.py` | **Modularize** (#7) | Split into package structure. File becomes thin re-export wrapper or removed. |
| `pdclaw.py:908` | **Fix** (#2, #5) | Replace `if 't0' in dir()` with `t0 = 0.0` before try block. |
| `pdclaw.py:938-939` | **Fix** (#4) | Construct minimal env dict for subprocess instead of `os.environ.copy()`. |
| `pdclaw.py:89` | **Doc/Remove** (#11) | Document `/new-refresh` command or remove `NEW_SESSION_RE`. |
| `pdclaw.py:346-349` | **Tune** (#13) | Refine `MEANINGFUL_INPUT_PATTERNS` regex to reduce false positives. |
| `pdclaw.py:134` | **Add** (#14) | Handle `SIGHUP` for graceful reload in daemon mode. |
| `pdclaw.py:1016-1032` | **Fix** (#4) | `_find_changed_files` swallows all exceptions silently — log the error. |

### Memory System (pdca_memory.py)
| File | Change Type | Enhancement |
|------|------------|-------------|
| `pdca_memory.py:66` | **Remove** (#6) | Delete dead `frequent_issues` field from global memory template. |
| `pdca_memory.py:145` | **Fix** (#9) | Replace f-string in `log.warning` with `%s`-style lazy formatting. |

### Dashboard (pdca_dashboard.py)
| File | Change Type | Enhancement |
|------|------------|-------------|
| `pdca_dashboard.py:26-196` | **Extract** (#1) | Move `_DASHBOARD_HTML` to `pdca_dashboard.html` template file. |
| `pdca_dashboard.py:249-256` | **Enhance** (#18) | Implement SSE log streaming for `/api/log` endpoint. |
| `pdca_dashboard.py` | **Refactor** (incidental) | Server reads template from file with fallback to embedded default. |

### Session Manager (pdca_claude_session.py)
| File | Change Type | Enhancement |
|------|------------|-------------|
| `pdca_claude_session.py:1-10` | **Refactor** (#10) | Translate Chinese docstrings to English. |
| `pdca_claude_session.py:230` | **Fix** (#15) | Use `Path` consistently instead of string concatenation. |
| `pdca_claude_session.py:168` | **Tune** (#19) | Make summary truncation width configurable. |

### Metrics Collector (pdca_metrics.py)
| File | Change Type | Enhancement |
|------|------------|-------------|
| `pdca_metrics.py:1-10` | **Refactor** (#10) | Translate Chinese docstrings to English. |

### Configuration & Entry Points
| File | Change Type | Enhancement |
|------|------------|-------------|
| `config.ini` | **Unchanged** | All config validation is done in code, no format changes needed. |
| `pdca_memory_cli.py` | **Unchanged** | Already well-structured, no changes needed. |
| `requirements.txt` | **Add** (#3, #17) | Add `pytest` (optional), possibly `python-dotenv` and `responses`. |

### New Files
| File | Purpose |
|------|---------|
| `tests/test_state_machine.py` | Tests for `resolve_next_step`, `get_step_from_tags` |
| `tests/test_tag_engine.py` | Tests for `get_tags`, tag extraction from text |
| `tests/test_activity.py` | Tests for `has_new_activity`, `is_meaningful_comment`, `get_new_human_comments` |
| `tests/test_github_client.py` | Tests for `_GitHubClient` with mock HTTP |
| `tests/test_context.py` | Tests for `context_hash`, `build_consolidated_context` |
| `tests/test_config.py` | Tests for `load_config` with missing/partial config files |
| `pdca_dashboard.html` | Dashboard HTML template extracted from `pdca_dashboard.py` (Tier 1, #1) |

### Skills (skills/*.md)
| File | Change Type | Enhancement |
|------|------------|-------------|
| All `skills/pdca-*.md` | **Unchanged** | Skill definitions are well-structured and not part of this review. |

---

## Modules Not Affected

- `.github/workflows/ci.yml` — CI workflow unchanged unless tests are added
- `.gitignore` — unchanged (current ignores already correct)
- `README.md`, `README_CN.md`, `CONTRIBUTING.md`, `LICENSE` — documentation updates TBD
- `SESSION_MODE.md`, `MEMORY_SYSTEM.md` — unchanged (documentation for user-facing features)

---

## Dependency Changes

| Dependency | Current | Proposed | Reason |
|-----------|---------|----------|--------|
| `requests` | ✓ Required | ✓ Required | No change |
| `pytest` | — | Optional / dev | Testing (#3) |
| `responses` or `pytest-httpserver` | — | Optional / dev | Mocking HTTP in tests (#3) |
| `python-dotenv` | — | Optional | `.env` support (#17, question still open) |

---

## Risk Assessment Per File

| File | Risk | Notes |
|------|------|-------|
| `pdclaw.py` → package structure | **Medium** | Import paths change. Mitigate with backward-compat re-exports. |
| `pdca_dashboard.py` → template extraction | **Low** | File-reading fallback, embedded string kept as default. |
| `pdca_claude_session.py` → docstring language | **None** | Cosmetic only. |
| `config.ini` | **None** | No format changes. |

---

## Implementation Effort Estimates

| Phase | Enhancement(s) | Estimated Effort |
|-------|---------------|-----------------|
| Phase 1 | Quick wins (Tier 1, #1-6) | 1-2 hours |
| Phase 2 | Modularization + TypedDicts (Tier 2, #7-14) | 4-6 hours |
| Phase 3 | Test suite setup (Tier 1, #3 + Tier 2) | 3-5 hours |
| Phase 4 | Polish items (Tier 3, #15-20) | 2-4 hours |
| **Total** | | **10-17 hours** |
