---
name: "cli-anything"
description: "Muse-native methodology and toolkit for turning real software codebases or services into stateful, agent-ready Click CLIs with REPL, JSON output, tests, and SKILL.md."
---

# cli-anything (Muse Custom Skill)

Turn a real software target (local source path or GitHub repository URL) into an installable Python harness package (`cli-anything-<software>`) that drives the **real software**—never a reimplementation.

## Prerequisites

- **Python >= 3.10**
- **Virtual environments mandatory (PEP 668):** Never run `pip install` against system Python. Always create and activate a venv (`python3 -m venv <dir>/.venv`).
- **Target software installed locally** for true-backend E2E verification (no Docker containers).
- **Toolkit installed in venv:** `pip install -e <repo-root>` gives `cli-anything-muse` and `cli-hub`.

## Non-Negotiable Rules

1. **Use the real software:** Generated harnesses wrap the real binary, library, or service API. Missing backends raise clear install instructions and never silently fake behavior.
2. **Mind the rendering gap:** Prefer native rendering first, explicit filter translation second, and generated render scripts last. Document any project-only effects explicitly.
3. **No claim without a run:** Do not report a harness as working until tests actually execute and pass inside its `.venv`. State plainly what could not run (e.g., unreachable live service).
4. **Alfred Notes Register:** Track open questions, ambiguities, assumptions to test, and §8 gaps in `<SOFTWARE>.md` during generation and review them before closing.

## Skill Procedures (Replacing Slash Commands)

### 1. Build (`cli-anything-muse build <path-or-url>`)
Runs Phases 0–7 + 6.5 in strict order:
- **Phase 0:** Acquire source (clone GitHub URL or verify local directory; reject bare names).
- **Phase 1:** Analyze codebase (backend engine, GUI/API actions, data model, existing CLI tools, undo/command system) and classify shape (`project` vs `service`).
- **Phase 2:** Design CLI architecture, write `<SOFTWARE>.md` SOP (including inspected-file audit list and rendering-gap table), create PEP 420 skeleton (`cli_anything/` with **no** `__init__.py`).
- **Phase 3:** Implement data/client layer, mandatory `info`/`list`/`status` commands, backend wrapper, locked JSON session + 50-level undo/redo (`project` shape) or env-backed connection session (`service` shape), auto-save + `--dry-run`, and `ReplSkin` default REPL.
- **Phase 4:** Write `tests/TEST.md` Part 1 (test inventory, unit plan, E2E plan, realistic workflows) **before** any test code.
- **Phase 5:** Implement 4-layer tests (`test_core.py`, `test_full_e2e.py` with `_resolve_cli` and `CLI_ANYTHING_FORCE_INSTALLED=1`, no `cwd` override, no skipping on missing backend).
- **Phase 6:** Execute tests in `.venv` and append full output + summary to `TEST.md` Part 2 (only on 100% pass).
- **Phase 6.5:** Generate `skills/cli-anything-<software>/SKILL.md` (canonical) and `cli_anything/<software>/skills/SKILL.md` (packaged), plus optional install into `~/workspace/custom-skills/`.
- **Phase 7:** Build `.venv`, `pip install -e agent-harness/`, verify command on `.venv/bin`, check PEP 420 namespace coexistence, run tests with `CLI_ANYTHING_FORCE_INSTALLED=1`.

### 2. List (`cli-anything-muse list [search-path] [--max-depth N] [--json]`)
Discovers installed `cli-anything-*` distributions and generated `agent-harness/cli_anything/*/__init__.py` trees.

### 3. Refine (`cli-anything-muse refine <local-path> [--focus AREA] [--yes]`)
Local paths only. Inventories covered CLI/core/test functions via AST, scans target source for uncovered capabilities, presents a prioritized gap proposal for confirmation, adds new commands/tests without removing existing ones, and re-runs tests.

### 4. Test (`cli-anything-muse test <path-or-url>`)
Runs `pytest -v` with `CLI_ANYTHING_FORCE_INSTALLED=1`. Appends timestamped results to `TEST.md` on 100% pass; preserves previous `TEST.md` on failure.

### 5. Validate (`cli-anything-muse validate <harness-path> [--shape auto|project|service] [--json]`)
Audits all 8 methodology categories (directory structure, required files, CLI standards, core standards, test standards, documentation standards, PyPI packaging, code quality) and reports per-category PASS/FAIL.

## Muse Skill Discovery Paths

- Generator skill: `~/workspace/custom-skills/cli-anything/SKILL.md`
- Generated harness canonical skill: `<repo>/skills/cli-anything-<software>/SKILL.md`
- Installed Muse harness skill copy: `~/workspace/custom-skills/cli-anything-<software>/SKILL.md`
- Packaged fallback inside wheel/editable package: `cli_anything/<software>/skills/SKILL.md`