# CLI-Anything Methodology Reference (Muse-Native)

This document is the single source of truth for generating, testing, validating, and refining `cli-anything-<software>` harnesses.

## Phase Map (0–7 + 6.5)

| Phase | Name | Required Output | Gate Check |
|-------|------|-----------------|------------|
| 0 | Source acquisition | Verified local source tree + `software` slug | Reject bare names; clone GitHub URLs first |
| 1 | Codebase analysis | Backend, APIs, data model, shape (`project` or `service`), inspected files list | Real backend identified; no silent reimplementation |
| 2 | Architecture design | `<SOFTWARE>.md` SOP + PEP 420 directory skeleton | `cli_anything/` has NO `__init__.py` |
| 3 | Implementation | Core/client modules, backend wrapper, Click CLI + REPL, locked session, auto-save | Every command supports `--json`; REPL is default |
| 4 | Test planning | `tests/TEST.md` Part 1 | Plan written BEFORE test code exists |
| 5 | Test implementation | `tests/test_core.py` + `tests/test_full_e2e.py` | Uses `_resolve_cli`, `CLI_ANYTHING_FORCE_INSTALLED=1`, no skips |
| 6 | Test documentation | `tests/TEST.md` Part 2 | 100% pass required before appending |
| 6.5 | Skill generation | Canonical + packaged `SKILL.md` | AST-parsed commands, no Claude Code refs |
| 7 | Packaging & verify | `setup.py`, `.venv` editable install, namespace check | Forced-installed tests pass from any `cwd` |

## Two Supported Harness Shapes

1. **Stateful Project CLI (`project`):**
   - Modules: `core/project.py`, `core/session.py`, `core/export.py`.
   - Global options: `--json`, `--project <path>`, `--dry-run`, `--version`.
   - Persistence: JSON session saves use exclusive `fcntl` file locking (open `r+` without truncating, lock, truncate inside lock, write, flush, unlock; unlocked fallback where `fcntl` is unavailable).
   - Undo/redo: Snapshot stack up to 50 levels deep.
   - Auto-save: `@cli.result_callback()` saves modified projects after one-shot commands unless in REPL mode, `--dry-run`, or error path.

2. **Stateless API / Service Client (`service`):**
   - Modules: `core/session.py` (connection config), `core/client.py` (stdlib `urllib.request` first).
   - Global options: `--json`, `--host`, `--port`, `--scheme`, `--token`, `--version` (env vars `<SOFTWARE>_HOST`, etc., overridden by flags).
   - No project file or undo/redo machinery forced onto the service client.

## Cross-Cutting Rules

- **Session File Locking:** Never use `open(path, "w")` directly on shared session JSON before acquiring an exclusive lock; truncation must occur *inside* the lock.
- **Timecode Precision:** Non-integer FPS (e.g., 29.97) uses `round()` rather than `int()` truncation, integer math for display, and ±1 frame verification tolerance.
- **Output Verification:** Check magic bytes, archive entries, media duration/frames, or JSON payload validity—exit code 0 alone is insufficient.
- **Preview Protocol (`preview-bundle/v1`):** Separate producer (`cli-anything-<software> preview ...`) from read-only consumer (`cli-hub previews ...`). Persist immutable bundles, mutable `session.json`, and append-only `trajectory.json`.
- **Verification & Alfred Notes:** Keep an explicit open-questions register in `<SOFTWARE>.md`; never promote unverified assumptions to confirmed facts.