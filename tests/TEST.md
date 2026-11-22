# TEST.md — Toolkit Test Plan (Part 1) + Results (Part 2)

## 1. Test inventory plan

| # | Area | Count (target) | Status |
|---|------|----------------|--------|
| 1 | analyzer (Phase 0 + Phase 1) | 10 | implemented |
| 2 | generator (Phases 2-3, 6.5, 7) | 12 | implemented |
| 3 | validator (8 categories) | 8 | implemented |
| 4 | lifecycle (list / refine / test) | 9 | implemented |
| 5 | skill_generator (AST + Jinja2/simple) | 12 | implemented |
| 6 | preview_bundle (producer + consumer) | 10 | implemented |
| 7 | repl_skin | 8 | implemented |
| 8 | cli_hub (registry + install + matrix) | 10 | implemented |
| 9 | E2E (synthetic target → venv → workflows) | 6 | implemented |
| 10 | CLI subprocess (both console scripts) | 8 | implemented |

## 2. Unit test plans

### analyzer.py (synthetic data only)

- Acquire local path: accepts a directory with a source file; slug from
  the directory name; files_read returned.
- Acquire rejects a bare name (no path separator, not a URL) with a
  clear error; rejects a nonexistent path.
- Acquire GitHub URL: clones with `git clone --depth 1` — tested only
  against a local `file://` URL to avoid network dependence.
- Analyse a synthetic target: detects the backend engine by keyword
  hints; returns entry_points and apis; detects shape `project` for a
  file-based tool and `service` for an HTTP-client target.
- `write_sop` writes the SOP doc with the audit list and the Alfred
  notes register.

### generator.py (synthetic data only)

- `design()` returns the design with shape, groups, and commands.
- `build_harness` with `run_tests=False, install=False`: creates the
  `agent-harness/` layout, SOP, setup.py, README, and generated
  `cli_anything/<pkg>/` modules; the generated CLI parses via AST.
- `_render` replaces `__SOFTWARE__` / `__PKG__` / `__DISPLAY__` /
  `__VERSION__` tokens.
- Project shape gets project/session/export core; service shape gets
  client.py + service CLI + service test templates (no project/export).
- Phase 6.5 (skill generation) runs only after tests pass — or is marked
  unverified when `run_tests=False`.
- `run_generated_tests` creates the harness venv and runs pytest.
- `append_test_results` writes the results into TEST.md.

### validator.py

- A generated harness validates PASS (project shape and service shape).
- Deleting a required file makes validation FAIL with the right
  category named.
- `--shape` auto-detection: SOP `**Shape:**` line, else CLI-file
  heuristics.
- The "all tests pass" check really executes pytest (300 s timeout).

### lifecycle.py

- `list_clis` finds installed distributions (importlib.metadata) and
  generated CLIs (glob over a search path); installed wins on merge.
- `refine` without `--yes` proposes gaps (no files changed); with
  `--yes` implements structural gaps only and never removes commands.
- `run_tests` appends to TEST.md on 100% pass only; on failure it keeps
  the previous results and prints suggestions.

### skill_generator.py

- Version quoting: `1.0` → `"1.0"`, `"1.2.3"` → `"1.2.3"`, bare
  `2.0.0` → `"2.0.0"` (YAML-safe quoting).
- Intro extraction: docstring, README, description, fallback.
- Groups/commands extraction: nested groups discovered, full_command
  built from the group path.
- No catastrophic backtracking on a large file (times out at 1 s
  internally — the regexes are linear).
- Missing harness raises a clear error.
- The generated description contains the software name.

### preview_bundle.py

- `create_bundle` writes the bundle; a second `create_bundle` with the
  same inputs hits the cache (same bundle id, `cached: True`).
- `latest_bundle` returns the newest bundle; `inspect_bundle` reads
  summary + warnings.
- `render_html` writes a report file.
- Live sessions: `live_start` / `push` / `status` / `stop` round-trip;
  `watch_session` snapshots.

### repl_skin.py

- Banner contains the software name and version; prompt degrades to
  `"> "` without color; `NO_COLOR` disables color; table prints;
  `strip_ansi` / `visible_len` handle ANSI correctly.

### cli_hub.py

- Registry fetch: local file URL; 1-hour TTL cache; stale-cache fallback
  on fetch failure.
- `list` / `search` / `info` find the go-bot entry from registry.json.
- `matrix` commands exit 2 with the deferred message.
- `can` is NOT implemented: the command does not exist.

## 3. E2E test plans (build a trial harness on a small synthetic target)

- Build a project-shape harness on a synthetic target, install it in a
  venv, `--help` lists all groups, `--json info` returns the software
  name and version, and a full workflow runs: project new → add →
  save → reopen → undo → redo → export JSON preset → file exists.
- True-backend E2E: a fake executable on PATH satisfies the backend
  check; the export default preset produces a non-empty output file.
- Build a service-shape harness; `--help` lists server/info/list/status;
  live commands fail gracefully when unreachable (non-zero exit, clear
  error, no stack trace).
- `cli-anything-muse validate` on the generated harness returns PASS.
- `cli-anything-muse test` on the generated harness appends to TEST.md.
- `cli-anything-muse list` (subprocess, installed mode) finds the
  installed toolkit itself.

## 4. CLI subprocess test plans

- `_resolve_cli` prefers the installed command when present, falls back
  to `python -m` with a printed note, and raises under
  `CLI_ANYTHING_FORCE_INSTALLED=1` when the command is missing.
- `cli-anything-muse --help` and `cli-hub --help` exit 0.
- `cli-anything-muse --json list` returns valid JSON.
- `cli-hub list --json` returns valid JSON containing the go-bot entry.
- `cli-hub search go-bot --json` returns the entry.
- `cli-hub matrix list` exits 2 with the deferred message.
- Tests FAIL, never skip, when a required backend is missing (the fake
  executable is absent from PATH in the missing-backend test, and the
  export command must fail, not skip).

## 5. Workflow scenarios (E2E, project shape)

1. `project new` → `project add` → `project save` → reopen in a new
   process → `session undo` → `session redo` → `export json` → output
   file exists and parses.
2. `--dry-run` prints the command without running it.
3. `--json` on every command produces valid JSON on stdout.
4. REPL: `cli-anything-muse` with no subcommand starts the REPL; typing
   `help` prints the group help; `quit` exits 0.

## 6. Gaps (honest)

- The go-bot trial (real target) is NOT run here; it lives in
  `GO_BOT_TRIAL_RUNBOOK.md` and is blocked on the model fix and the
  Private Codespace port.
- Live Docker/network tests are not part of this suite (sandbox limits).
- The `matrix` subsystem is surface-only (spec section 8.2) and is
  tested only for its deferred behaviour.
- The `can` command is not implemented (spec section 8.3) and is
  tested only for its absence.

## Part 3 — Fix round 2026-11-22 (suite to green)

Baseline before this round (verified 2026-11-22 16:54): **79 passed,
8 failed, 3 errors** (90 total, ~160 s). The stale `lastfailed` entry
`TestAnalyzer::test_acquire_github_url_from_local_file_url` does not
exist in the current test file and was not a real failure.

### Failures and fixes (implementation only — no test was edited)

1. `TestLifecycle::test_run_tests_appends_test_md_on_pass` —
   `resolver_reports_installed` was False. Cause: pytest captures the
   generated tests' stdout by default, so their `_resolve_cli` line
   ("[_resolve_cli] Using installed command: ...") never reached the
   output `lifecycle.run_tests` checks. Fix (`generator.py`,
   `run_generated_tests`): run pytest with `-s` (no capture), as the
   spec requires ("runs the test runner verbosely with output shown",
   spec section 5, `test` command).
2. `TestE2EProject` × 7 (all) — `cli-anything-demo-tool` not on PATH.
   Cause: `install_harness` installed the harness only into the
   harness-local `.venv`, never into the environment running the
   toolkit, so the command never landed on PATH. Fix (`generator.py`,
   `install_harness`): after the isolated-venv install, also run an
   editable `pip install -e` into the current environment
   (`sys.executable`) and verify with `shutil.which(command)`; the
   result now reports `on_path` and `ok` requires it. This is the
   spec's Phase 7 ("editable pip install from `agent-harness/`",
   check: "command on PATH", spec sections 2 and 5).
3. `TestE2EService` × 3 (ERROR at fixture setup) — the generated
   service harness's own test file failed collection with
   `NameError: name 'Path'`. Cause: `TEST_E2E_SERVICE_TEMPLATE`
   uses `Path` (`HARNESS_ROOT = Path(...)`) but never imported it
   (the project template does import it). Fix (`generator.py`):
   added `from pathlib import Path` to the service E2E template.
4. `TestE2EService` fixture, second failure (found after fix 3) —
   generated `test_live_command_fails_gracefully_when_unreachable`
   failed: `server status` exited 0 with "Reachable: False".
   Cause: the generated `cmd_status` treated unreachable as a report
   instead of an error (`client.health()` swallows the failure into a
   `{"reachable": False}` dict). Fix (`generator.py`,
   CLI service template `cmd_status`): raise `click.ClickException`
   ("service unreachable at ...") when `reachable` is False, so live
   commands fail gracefully with a clear message and a non-zero exit
   (spec section 6.3). No traceback; `--json` reachable case unchanged.
5. `TestLifecycle::test_list_clis_finds_generated` — regression found
   in the second full run, caused by fix 2: an earlier test's editable
   install of `cli-anything-demo-tool` (different tmp harness) merged
   by name with the fresh install=False harness, so `generated_only`
   dropped to 0. Cause: `list_clis` merged by software name even when
   the installed distribution and the generated harness are different
   harnesses. Fix (`lifecycle.py`): `_installed_clis` now records the
   source path from `direct_url.json` (editable installs); `list_clis`
   merges only when the installed source path matches the generated
   harness path (or either is unknown, keeping the old merge-by-name
   behaviour); same-name different-harness entries are listed
   separately instead of one swallowing the other.

### Final result (2026-11-22, full suite)

- `python -m pytest tests/ -q` → **90 passed in 185.57 s**, exit 0.
- `cli-anything-muse --help` → exit 0.
- `cli-hub --help` → exit 0.
- `registry.json` already contained the go-bot and docker entries
  (filled before this fix round; verified: `cli-hub list --json`
  contains the go-bot entry, `cli-hub search go-bot --json` returns it).
- No test was weakened, skipped, or edited. All fixes are in
  `cli_anything_muse/generator.py` (3) and
  `cli_anything_muse/lifecycle.py` (1).
