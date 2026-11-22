"""Phases 2-3 (design + build), 6.5 (SKILL.md generation), 7 (packaging).

Implements spec sections 2 (Phases 2-7), 3 (CLI patterns), 4 (skill
generation) and the gap choices in spec section 8: Phase 1 depth is
auditable via the SOP (8.1), the entry point is standardized on the
``main`` callable (8.6), both project and service shapes are supported
(8.7), phases are numbered 0-7 + 6.5 (8.8).

Generated harness files are rendered from string templates below with
``__SOFTWARE__`` (hyphen slug), ``__PKG__`` (underscore package name),
``__DISPLAY__`` (title case) and ``__VERSION__`` placeholders. Plain
``str.replace`` is used on purpose: the templates contain Python code
full of braces, so format-style substitution would corrupt them.

Clean-room: written from REA_SPEC.md only. No upstream source was read.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any

from . import __version__ as TOOLKIT_VERSION
from .analyzer import AnalysisError, analyse, acquire, software_slug, write_sop
from .skill_generator import generate_skill

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

MAX_HISTORY = 50  # undo/redo depth, per spec section 3.3.


class GeneratorError(Exception):
    """Raised when a harness cannot be designed, built, or installed."""


def _render(template: str, software: str, version: str = "1.0.0") -> str:
    """Substitute the four template placeholders (no brace formatting)."""
    pkg = software.replace("-", "_")
    display = software.replace("-", " ").title()
    return (
        template.replace("__SOFTWARE__", software)
        .replace("__PKG__", pkg)
        .replace("__DISPLAY__", display)
        .replace("__VERSION__", version)
    )


def _write(path: Path, content: str) -> Path:
    """Write a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Phase 2 — CLI architecture design.
# ---------------------------------------------------------------------------


def design(analysis: dict[str, Any]) -> dict[str, Any]:
    """Design the CLI architecture from a Phase 1 analysis.

    Returns the interaction model (both: subcommand CLI + REPL), the
    command groups per domain, the state model, and the output format.
    """
    shape = analysis["shape"]
    groups = list(analysis["groups"])
    return {
        "software": analysis["software"],
        "shape": shape,
        "interaction_model": "both",  # subcommand CLI + REPL (recommended).
        "groups": groups,
        "state_model": (
            "In-memory in the REPL; JSON session files (lock-protected "
            "saves, undo/redo up to 50 levels) for one-shot invocations."
            if shape == "project"
            else "Connection parameters from environment variables "
            "(<SOFTWARE>_HOST etc.), CLI options override them; no "
            "persistence beyond that."
        ),
        "output_format": "Human-readable by default; machine-readable JSON "
        "with --json on every command.",
        "mandatory_commands": ["info", "list", "status"],
    }


# ---------------------------------------------------------------------------
# Phase 4 — test plan (TEST.md part 1, written BEFORE any test code).
# ---------------------------------------------------------------------------

TEST_MD_PLAN = """\
# Test plan — __DISPLAY__

## 1. Test inventory plan

- `test_core.py` — unit tests (synthetic data only, no external deps).
- `test_full_e2e.py` — E2E tests (intermediate files, true backend,
  CLI subprocess tests, workflows).

## 2. Unit test plan

Per core module: functions, edge cases (invalid inputs, boundary
conditions, error handling), expected counts — see `test_core.py`.

- `core/project.py`: create/open/save/info round-trips, invalid paths.
- `core/session.py`: undo/redo depth, snapshot isolation, locked saves.
- `core/export.py`: preset validation, missing backend error.
- `utils/__PKG___backend.py`: executable discovery, missing-software error.

## 3. E2E test plan

Real-world scenarios on real files; output properties verified; format
validation (magic bytes, archive structure) where the backend produces
files. True-backend tests invoke the real software and FAIL (never skip)
when it is not installed.

## 4. Realistic workflow scenarios

- Multi-step editing, montage-style assembly, undo/redo stress,
  save/load round-trips, iterative refinement (add, modify, remove,
  re-add).

## Results

(Results are appended here after a passing run: full output, summary
statistics, coverage notes/gaps.)
"""


# ---------------------------------------------------------------------------
# Phase 3 — implementation: harness file templates.
# ---------------------------------------------------------------------------
# NOTE: the templates live in the second half of this module (appended
# below): SETUP_PY_TEMPLATE, README_TEMPLATE, CLI_TEMPLATE,
# PROJECT_TEMPLATE, SESSION_TEMPLATE, EXPORT_TEMPLATE, BACKEND_TEMPLATE,
# MAIN_TEMPLATE, INIT_TEMPLATE, TEST_CORE_TEMPLATE, TEST_E2E_TEMPLATE,
# EXAMPLE_TEMPLATE. They are referenced by build_harness().
# ---------------------------------------------------------------------------


def _skeleton(root: Path, software: str) -> dict[str, Path]:
    """Create the harness directory skeleton (spec section 1.4)."""
    pkg = software.replace("-", "_")
    harness = root / "agent-harness"
    subpkg = harness / "cli_anything" / pkg
    paths = {
        "root": root,
        "harness": harness,
        "subpkg": subpkg,
        "core": subpkg / "core",
        "utils": subpkg / "utils",
        "skills": subpkg / "skills",
        "tests": subpkg / "tests",
        "examples": root / "examples",
        "canonical_skills": root / "skills" / f"cli-anything-{software}",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _copy_toolkit_module(module_name: str, dest: Path) -> Path:
    """Copy a toolkit module (repl_skin, preview_bundle) into a harness.

    The copy is verbatim: the harness must be standalone and must not
    depend on the generator package being installed.
    """
    source = Path(__file__).resolve().parent / f"{module_name}.py"
    if not source.is_file():
        raise GeneratorError(f"toolkit module not found: {source}")
    return Path(shutil.copy2(source, dest))


def _write_harness_files(
    paths: dict[str, Path], analysis: dict[str, Any], design_doc: dict[str, Any]
) -> list[str]:
    """Write every harness file (Phases 2-3). Returns the file list."""
    software = analysis["software"]
    pkg = software.replace("-", "_")
    written: list[str] = []

    # -- SOP document (Phase 2 output) ------------------------------------
    sop_path = paths["root"] / f"{software.upper()}.md"
    write_sop(analysis, sop_path)
    written.append(str(sop_path))

    # -- Packaging (Phase 7 artefacts, written here so tests can run) ------
    written.append(str(_write(paths["harness"] / "setup.py", _render(SETUP_PY_TEMPLATE, software))))
    written.append(str(_write(paths["subpkg"] / "README.md", _render(README_TEMPLATE, software))))
    written.append(str(_write(paths["subpkg"] / "__init__.py", _render(INIT_TEMPLATE, software))))
    written.append(str(_write(paths["subpkg"] / "__main__.py", _render(MAIN_TEMPLATE, software))))
    # (The CLI entry module is written in the shape branch below.)

    # -- Core modules -------------------------------------------------------
    written.append(str(_write(paths["core"] / "__init__.py", _render(CORE_INIT_TEMPLATE, software))))
    if design_doc["shape"] == "service":
        # Service shape (spec section 8.7): HTTP client + connection
        # session; no project file, no undo/redo, no export core.
        written.append(str(_write(paths["core"] / "client.py", _render(CLIENT_TEMPLATE, software))))
        written.append(str(_write(paths["core"] / "session.py", _render(SESSION_TEMPLATE, software))))
        written.append(str(_write(paths["subpkg"] / f"{pkg}_cli.py", _render(CLI_SERVICE_TEMPLATE, software))))
    else:
        written.append(str(_write(paths["core"] / "project.py", _render(PROJECT_TEMPLATE, software))))
        written.append(str(_write(paths["core"] / "session.py", _render(SESSION_TEMPLATE, software))))
        written.append(str(_write(paths["core"] / "export.py", _render(EXPORT_TEMPLATE, software))))
        written.append(str(_write(paths["subpkg"] / f"{pkg}_cli.py", _render(CLI_TEMPLATE, software))))

    # -- Utils: backend wrapper + copied toolkit modules --------------------
    written.append(str(_write(paths["utils"] / "__init__.py", _render(UTILS_INIT_TEMPLATE, software))))
    if design_doc["shape"] == "service":
        written.append(str(_write(paths["utils"] / f"{pkg}_backend.py", _render(BACKEND_SERVICE_TEMPLATE, software))))
    else:
        written.append(str(_write(paths["utils"] / f"{pkg}_backend.py", _render(BACKEND_TEMPLATE, software))))
    _copy_toolkit_module("repl_skin", paths["utils"] / "repl_skin.py")
    written.append(str(paths["utils"] / "repl_skin.py"))
    if design_doc["shape"] == "project":
        # Preview producer needs the bundle module inside the harness.
        _copy_toolkit_module("preview_bundle", paths["utils"] / "preview_bundle.py")
        written.append(str(paths["utils"] / "preview_bundle.py"))

    # -- Tests: TEST.md part 1 FIRST, then test code (Phase 4, then 5) -----
    written.append(str(_write(paths["tests"] / "TEST.md", _render(TEST_MD_PLAN, software))))
    if design_doc["shape"] == "service":
        written.append(str(_write(paths["tests"] / "test_core.py", _render(TEST_CORE_SERVICE_TEMPLATE, software))))
        written.append(str(_write(paths["tests"] / "test_full_e2e.py", _render(TEST_E2E_SERVICE_TEMPLATE, software))))
    else:
        written.append(str(_write(paths["tests"] / "test_core.py", _render(TEST_CORE_TEMPLATE, software))))
        written.append(str(_write(paths["tests"] / "test_full_e2e.py", _render(TEST_E2E_TEMPLATE, software))))

    # -- Examples ------------------------------------------------------------
    written.append(str(_write(paths["examples"] / "basic_workflow.py", _render(EXAMPLE_TEMPLATE, software))))
    return written


# ---------------------------------------------------------------------------
# Phases 0-7 orchestration.
# ---------------------------------------------------------------------------


def build_harness(
    target: str,
    out_dir: str | Path | None = None,
    run_tests: bool = True,
    install: bool = True,
    muse_install: bool = False,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run Phases 0-7 (+ 6.5) for a target path or GitHub URL.

    Returns a report dict. Honest failure modes: when the target's real
    software is not installed, the generated true-backend tests fail
    (never skip) and the report says so plainly; TEST.md keeps its plan
    only and the build is marked unverified.
    """
    source = acquire(target, work_dir=work_dir)
    analysis = analyse(source)
    design_doc = design(analysis)
    software = analysis["software"]

    root = Path(out_dir) if out_dir else Path.cwd() / software
    root.mkdir(parents=True, exist_ok=True)
    paths = _skeleton(root, software)
    written = _write_harness_files(paths, analysis, design_doc)

    report: dict[str, Any] = {
        "software": software,
        "shape": design_doc["shape"],
        "root": str(root.resolve()),
        "harness": str(paths["harness"].resolve()),
        "files_written": written,
        "tests_run": False,
        "tests_passed": False,
        "installed": False,
        "verified": False,
        "notes": [],
    }

    # -- Phase 6: execute the generated tests in a venv. -------------------
    test_result: dict[str, Any] | None = None
    if run_tests:
        test_result = run_generated_tests(paths["harness"])
        report["tests_run"] = True
        report["tests_passed"] = bool(test_result.get("passed"))
        report["test_output"] = test_result.get("output", "")
        if test_result.get("passed"):
            append_test_results(paths["tests"] / "TEST.md", test_result)
        else:
            report["notes"].append(
                "Generated tests did not pass; TEST.md keeps the plan only "
                "(previous passing results preserved). The harness is NOT "
                "verified. Most likely cause: the target's real software is "
                "not installed on this host."
            )

    # -- Phase 6.5: SKILL.md generation, only after tests pass (spec
    #    section 2). When tests were skipped by explicit request the
    #    skill is still generated, but the report marks the harness
    #    unverified. ---------------------------------------------------------
    if (not run_tests) or report["tests_passed"]:
        try:
            canonical = generate_skill(
                paths["harness"], install_muse_skill=muse_install
            )
            report["skill"] = str(canonical)
            written.append(str(canonical))
        except Exception as exc:  # pragma: no cover - defensive
            report["notes"].append(f"SKILL.md generation failed: {exc}")
    else:
        report["notes"].append(
            "SKILL.md not generated: the skill is produced only after "
            "tests pass (spec section 2, Phase 6.5)."
        )

    # -- Phase 7: editable install + verification. -------------------------
    if install:
        try:
            install_result = install_harness(paths["harness"])
            report["installed"] = bool(install_result.get("ok"))
            report["install"] = install_result
        except Exception as exc:
            report["notes"].append(f"Install failed: {exc}")

    report["verified"] = bool(
        report["tests_passed"] and report["installed"]
    )
    if not report["verified"]:
        report["notes"].append(
            "Not verified: the harness is reported as working only after "
            "its tests execute and pass in the venv (see skill rule 3)."
        )
    return report


def _find_tests_dir(harness: Path) -> Path:
    """Locate the harness test directory (cli_anything/<pkg>/tests/)."""
    ns_dir = harness / "cli_anything"
    if ns_dir.is_dir():
        for tests in sorted(ns_dir.glob("*/tests")):
            if tests.is_dir():
                return tests
    return harness / "tests"


def run_generated_tests(harness_path: str | Path) -> dict[str, Any]:
    """Create a venv for the harness, install it, run pytest verbosely.

    Returns a dict with passed/failed counts and the full output.
    The venv lives at <harness>/.venv (never system pip, PEP 668).
    """
    harness = Path(harness_path).resolve()
    venv_dir = harness / ".venv"
    if not venv_dir.exists():
        venv.create(venv_dir, with_pip=True)
    python = venv_dir / "bin" / "python"
    if not python.exists():  # Windows layout, kept for completeness.
        python = venv_dir / "Scripts" / "python.exe"

    def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, **kwargs)

    _run([str(python), "-m", "pip", "install", "-q", "-e", str(harness)])
    _run([str(python), "-m", "pip", "install", "-q", "pytest", "click"])
    env = dict(os.environ)
    env["CLI_ANYTHING_FORCE_INSTALLED"] = "1"
    bin_dir = venv_dir / "bin"
    if bin_dir.is_dir():
        # So _resolve_cli finds the installed command on PATH.
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    tests_dir = _find_tests_dir(harness)
    # -s (no capture): the generated tests' resolver prints
    # ("[_resolve_cli] Using installed command: ...") must reach the
    # captured output, so lifecycle.run_tests can verify the installed
    # command was used (spec section 5, `test` command: "runs the test
    # runner verbosely with output shown"). With pytest's default
    # capture, passing tests' prints are swallowed and the check can
    # never see them.
    result = _run(
        [str(python), "-m", "pytest", str(tests_dir), "-v", "-s"],
        cwd=str(harness),
        env=env,
    )
    output = (result.stdout or "") + (result.stderr or "")
    passed = result.returncode == 0
    # Parse the pytest summary line ("22 passed" / "2 failed, 20 passed").
    summary = re.search(r"(?:(\d+) failed, )?(\d+) passed", output)
    passed_count = int(summary.group(2)) if summary else 0
    failed_count = int(summary.group(1)) if summary and summary.group(1) else 0
    return {
        "passed": passed,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "total": passed_count + failed_count,
        "returncode": result.returncode,
        "output": output,
        "venv": str(venv_dir),
    }


def append_test_results(test_md: str | Path, test_result: dict[str, Any]) -> Path:
    """Append the full test output + summary to TEST.md (Phase 6)."""
    path = Path(test_md)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    passed = test_result.get("passed", False)
    output = test_result.get("output", "")
    lines = [
        "",
        f"## Run {stamp}",
        "",
        f"Result: {'PASS' if passed else 'FAIL'}",
        "",
        "```",
        output.rstrip(),
        "```",
        "",
    ]
    path.write_text(text.rstrip() + "\n" + "\n".join(lines), encoding="utf-8")
    return path


def install_harness(
    harness_path: str | Path, venv_dir: str | Path | None = None
) -> dict[str, Any]:
    """Phase 7: editable install + verification checks.

    Installs twice: once into the harness-local venv (the isolated
    verification environment) and once into the environment running
    the toolkit, so the command lands on PATH (spec section 2).
    Verifies: the command is on PATH and --help works; the PEP 420
    namespace resolves; the test suite passes with installed mode forced.
    """
    harness = Path(harness_path).resolve()
    software = harness.parent.name if harness.name == "agent-harness" else harness.name
    command = f"cli-anything-{software_slug(software)}"

    venv_path = Path(venv_dir) if venv_dir else harness / ".venv"
    if not venv_path.exists():
        venv.create(venv_path, with_pip=True)
    python = venv_path / "bin" / "python"
    if not python.exists():
        python = venv_path / "Scripts" / "python.exe"

    def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, **kwargs)

    install = _run([str(python), "-m", "pip", "install", "-q", "-e", str(harness)])
    if install.returncode != 0:
        return {"ok": False, "step": "pip install", "output": install.stderr}

    # Phase 7 (spec sections 2 and 5): the harness is installed with an
    # editable pip install into the environment running the toolkit, so
    # its command lands on PATH as cli-anything-<software>. The
    # harness-local venv above is only the isolated verification
    # environment; without this step the command is not usable from
    # anywhere and the Phase 7 "command on PATH" check cannot pass.
    current_install = _run(
        [sys.executable, "-m", "pip", "install", "-q", "-e", str(harness)]
    )
    if current_install.returncode != 0:
        return {
            "ok": False,
            "step": "pip install (current environment)",
            "output": current_install.stderr,
        }
    on_path = shutil.which(command)

    bin_dir = venv_path / "bin"
    cmd_path = bin_dir / command
    if not cmd_path.exists():
        cmd_path = venv_path / "Scripts" / f"{command}.exe"
    help_run = _run([str(cmd_path), "--help"]) if cmd_path.exists() else None
    help_ok = bool(help_run and help_run.returncode == 0)

    # Namespace coexistence: the sub-package imports without an
    # __init__.py in cli_anything/ (PEP 420).
    ns_check = _run(
        [
            str(python),
            "-c",
            f"import cli_anything.{software.replace('-', '_')}; "
            "print(cli_anything.__path__)",
        ]
    )
    ns_ok = ns_check.returncode == 0

    test_result = run_generated_tests(harness)
    ok = bool(
        help_ok and ns_ok and test_result.get("passed") and on_path
    )
    return {
        "ok": ok,
        "command": command,
        "command_path": str(cmd_path) if cmd_path.exists() else None,
        "on_path": on_path,
        "help_ok": help_ok,
        "namespace_ok": ns_ok,
        "tests_passed": bool(test_result.get("passed")),
        "venv": str(venv_path),
    }


def generate_skill_for_harness(
    harness_path: str | Path, muse_install: bool = False
) -> Path:
    """Phase 6.5 entry point: generate the harness SKILL.md."""
    return generate_skill(harness_path, install_muse_skill=muse_install)

# ---------------------------------------------------------------------------
# Harness file templates (Phase 3). Placeholders: __SOFTWARE__ (hyphen
# slug), __PKG__ (underscores), __DISPLAY__ (title case), __VERSION__.
# ---------------------------------------------------------------------------

SETUP_PY_TEMPLATE = '''\
"""Setup for cli-anything-__SOFTWARE__ (generated; PEP 420 namespace)."""
from setuptools import find_namespace_packages, setup

setup(
    name="cli-anything-__SOFTWARE__",
    version="__VERSION__",
    description="Command-line interface for __DISPLAY__ (CLI-Anything harness)",
    python_requires=">=3.10",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    package_data={"cli_anything.__PKG__": ["skills/*.md"]},
    install_requires=["click>=8.0"],
    entry_points={
        "console_scripts": [
            "cli-anything-__SOFTWARE__=cli_anything.__PKG__.__PKG___cli:main",
        ],
    },
)
# Hard dependency (documented, not a pip dependency): the real software
# (__DISPLAY__) must be installed separately. If it is missing, the CLI
# errors with clear install instructions.
'''

README_TEMPLATE = '''\
# CLI for __DISPLAY__

A command-line interface for __DISPLAY__, generated by CLI-Anything
(Muse-native, clean-room). It drives the **real** __DISPLAY__ software
through its real backend — it does not reimplement it.

## Prerequisites

- Python 3.10+
- __DISPLAY__ itself, installed. If it is missing, commands that need
  it fail with clear install instructions.

## Install (venv only — never system pip)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
cli-anything-__SOFTWARE__ --help
cli-anything-__SOFTWARE__ --json project new my-project
cli-anything-__SOFTWARE__            # REPL (default)
```

## Tests

```bash
python3 -m pytest tests/ -v
```

## Preview

This harness supports previews: `cli-anything-__SOFTWARE__ preview
recipes|capture|latest|diff|live ...` (producer). Read-only consumption
is via `cli-hub previews inspect|html|watch|open`. Previews are honest
views of real state from the real backend.
'''

INIT_TEMPLATE = '''\
"""__DISPLAY__ harness (CLI-Anything, generated)."""
__version__ = "__VERSION__"
'''

MAIN_TEMPLATE = '''\
"""Entry point: ``python3 -m cli_anything.__PKG__``."""
from cli_anything.__PKG__.__PKG___cli import main

if __name__ == "__main__":
    main()
'''

CORE_INIT_TEMPLATE = '''\
"""Core modules for the __DISPLAY__ harness."""
'''

UTILS_INIT_TEMPLATE = '''\
"""Utilities for the __DISPLAY__ harness."""
'''

PROJECT_TEMPLATE = '''\
"""Project data layer for __DISPLAY__ (generated).

The project file is plain JSON: a name, a creation timestamp, and an
items list. The CLI manipulates this file directly; rendering and
export go through the real backend (see utils/__PKG___backend.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProjectError(Exception):
    """Raised for project file misuse (missing file, bad content)."""


def _utc_now() -> str:
    """Current UTC time as an ISO-8601 string (e.g. 2026-11-22T15:00:00Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Project:
    """A __DISPLAY__ project: JSON state the CLI edits in place."""

    def __init__(self, name: str = "untitled", items: list[dict[str, Any]] | None = None) -> None:
        """Create a project in memory (unsaved until save() is called)."""
        self.name = name
        self.created_at = _utc_now()
        self.items: list[dict[str, Any]] = list(items or [])
        self.path: Path | None = None

    # -- construction -----------------------------------------------------
    @classmethod
    def new(cls, name: str) -> "Project":
        """Create a fresh, unsaved project."""
        return cls(name=name)

    @classmethod
    def open(cls, path: str | Path) -> "Project":
        """Load a project from a JSON file (raises ProjectError if bad)."""
        path = Path(path)
        if not path.is_file():
            raise ProjectError(f"project file not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"cannot read project file {path}: {exc}") from exc
        if not isinstance(data, dict) or "name" not in data:
            raise ProjectError(f"not a __DISPLAY__ project file: {path}")
        project = cls(name=str(data["name"]), items=list(data.get("items", [])))
        project.created_at = str(data.get("created_at", project.created_at))
        project.path = path
        return project

    # -- persistence --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Project state as a JSON-serialisable dict."""
        return {"name": self.name, "created_at": self.created_at, "items": self.items}

    def save(self, path: str | Path | None = None) -> Path:
        """Save the project as JSON (plain write; session locking lives
        in core/session.py and is used by the CLI for one-shot saves)."""
        target = Path(path) if path else self.path
        if target is None:
            raise ProjectError("no path: pass --project or save to a path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\\n", encoding="utf-8")
        self.path = target
        return target

    # -- content ------------------------------------------------------------
    def add_item(self, kind: str, value: str) -> dict[str, Any]:
        """Append one item; returns the stored item."""
        item = {"kind": kind, "value": value, "added_at": _utc_now()}
        self.items.append(item)
        return item

    def remove_item(self, index: int) -> dict[str, Any]:
        """Remove the item at index (raises IndexError when out of range)."""
        return self.items.pop(index)

    def info(self) -> dict[str, Any]:
        """Summary for the `info` / `status` commands."""
        return {
            "name": self.name,
            "created_at": self.created_at,
            "path": str(self.path) if self.path else None,
            "item_count": len(self.items),
        }
'''

SESSION_TEMPLATE = '''\
"""Session state, undo/redo, and lock-protected JSON persistence.

Implements spec section 3.3: a session holds the open project, its
path, a modified flag, and an undo/redo history (up to 50 snapshots).
Saves use exclusive file locking: open for read/write without
truncating, acquire the lock, truncate inside the lock, write the JSON,
flush, release. On platforms without the locking primitive, proceed
unlocked.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

MAX_HISTORY = 50


class SessionError(Exception):
    """Raised for session misuse (e.g. undo with empty history)."""


class Session:
    """Stateful session for a project-shaped harness."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        """Create a session around the given state (empty by default)."""
        self.state: dict[str, Any] = state if state is not None else {}
        self.project_path: str | None = None
        self.modified: bool = False
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []

    # -- mutation / history -------------------------------------------
    def mutate(self, apply_fn) -> None:
        """Apply ``apply_fn(state)`` after taking a snapshot."""
        snapshot = copy.deepcopy(self.state)
        apply_fn(self.state)
        self._undo.append(snapshot)
        if len(self._undo) > MAX_HISTORY:
            self._undo.pop(0)
        self._redo.clear()
        self.modified = True

    def undo(self) -> dict[str, Any]:
        """Restore the previous snapshot. Raises SessionError if empty."""
        if not self._undo:
            raise SessionError("nothing to undo")
        self._redo.append(copy.deepcopy(self.state))
        self.state = self._undo.pop()
        self.modified = True
        return self.state

    def redo(self) -> dict[str, Any]:
        """Re-apply the next snapshot. Raises SessionError if empty."""
        if not self._redo:
            raise SessionError("nothing to redo")
        self._undo.append(copy.deepcopy(self.state))
        self.state = self._redo.pop()
        self.modified = True
        return self.state

    def snapshot(self) -> dict[str, Any]:
        """Return a deep copy of the current state."""
        return copy.deepcopy(self.state)

    @property
    def can_undo(self) -> bool:
        """True when at least one undo snapshot is available."""
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        """True when at least one redo snapshot is available."""
        return bool(self._redo)

    # -- persistence --------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Session state as a JSON-serialisable dict (history included)."""
        # The undo/redo history is part of the session: without it,
        # `session undo` could never work across processes.
        return {
            "state": self.state,
            "project_path": self.project_path,
            "modified": self.modified,
            "undo": self._undo,
            "redo": self._redo,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        """Rebuild a session from a to_dict() payload (history included)."""
        session = cls(state=data.get("state") or {})
        session.project_path = data.get("project_path")
        session.modified = bool(data.get("modified", False))
        session._undo = list(data.get("undo") or [])
        session._redo = list(data.get("redo") or [])
        return session

    def save(self, path: str | Path) -> Path:
        """Save the session as JSON with exclusive file locking."""
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        with open(path, "a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                handle.truncate(0)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self.modified = False
        return Path(path)

    @classmethod
    def load(cls, path: str | Path) -> "Session":
        """Load a session from a JSON file."""
        with open(path, encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


class ServiceSession:
    """Connection-parameters session for a service-shaped harness.

    Loaded from environment variables named after the software
    (e.g. ``__SOFTWARE___HOST``), with per-invocation CLI options
    overriding them. No persistence beyond that.
    """

    def __init__(
        self,
        software: str | None = None,
        host: str | None = None,
        port: int | None = None,
        scheme: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        """Build the connection parameters: CLI options win, then env
        vars named <SOFTWARE>_HOST/_PORT/_SCHEME/_USER/_PASSWORD, then
        defaults (localhost:8080 over http)."""
        prefix = (software or "__SOFTWARE__").upper().replace("-", "_")
        self.software = software or "__SOFTWARE__"
        self.host = host or os.environ.get(f"{prefix}_HOST", "localhost")
        self.port = port or int(os.environ.get(f"{prefix}_PORT", "8080"))
        self.scheme = scheme or os.environ.get(f"{prefix}_SCHEME", "http")
        self.user = user or os.environ.get(f"{prefix}_USER")
        self.password = password or os.environ.get(f"{prefix}_PASSWORD")

    def base_url(self) -> str:
        """Base URL for the service (scheme://host:port)."""
        return f"{self.scheme}://{self.host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        """Connection parameters as a dict (password never included)."""
        return {
            "software": self.software,
            "host": self.host,
            "port": self.port,
            "scheme": self.scheme,
            "user": self.user,
        }
'''

EXPORT_TEMPLATE = '''\
"""Export / render pipeline for __DISPLAY__ (generated).

The export builds a valid intermediate file, then hands it to the real
software through the backend wrapper. It never reimplements rendering:
if the real software is missing, export fails with clear install
instructions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .project import Project
from ..utils import __PKG___backend as backend

# Render presets: name -> (description, output suffix).
EXPORT_PRESETS: dict[str, tuple[str, str]] = {
    "default": ("Default export through the real backend.", ".out"),
    "json": ("Project state as JSON (intermediate file only).", ".json"),
}


class ExportError(Exception):
    """Raised when an export cannot be produced."""


def render(
    project: Project,
    preset: str = "default",
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Render a project through the real backend.

    Returns a result describing the output (path, format, method used).
    """
    if preset not in EXPORT_PRESETS:
        raise ExportError(
            f"unknown preset {preset!r}; available: {sorted(EXPORT_PRESETS)}"
        )
    description, suffix = EXPORT_PRESETS[preset]
    out_path = Path(output) if output else Path(f"{project.name}{suffix}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if preset == "json":
        # Intermediate file only: the project state itself.
        out_path.write_text(json.dumps(project.to_dict(), indent=2) + "\\n", encoding="utf-8")
        return {
            "output": str(out_path),
            "format": "json",
            "method": "intermediate-file",
            "preset": preset,
            "description": description,
        }

    # Default preset: build the intermediate file, then invoke the real
    # software to convert it. Missing software -> clear error, no fake.
    intermediate = out_path.with_suffix(".intermediate.json")
    intermediate.write_text(json.dumps(project.to_dict(), indent=2) + "\\n", encoding="utf-8")
    result = backend.convert(intermediate, out_path)
    result["preset"] = preset
    result["description"] = description
    return result
'''

BACKEND_TEMPLATE = '''\
"""Backend wrapper for __DISPLAY__ (generated).

Contract (spec section 2, Phase 3, step 4): find the real software
executable on PATH, invoke it as a subprocess, and on absence raise an
error with clear install instructions. Never silently fall back to a
reimplementation.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

# The executable name of the real software, and how to install it.
EXECUTABLE = "__SOFTWARE__"
INSTALL_INSTRUCTIONS = (
    "Install __DISPLAY__ first, then retry.\\n"
    "  Debian/Ubuntu: sudo apt install __SOFTWARE__\\n"
    "  macOS (Homebrew): brew install __SOFTWARE__\\n"
    "  Or download it from the vendor and put it on PATH."
)


class BackendError(Exception):
    """Raised when the real software cannot be used."""


def find_executable() -> str:
    """Locate the real software on PATH (raises BackendError if absent)."""
    found = shutil.which(EXECUTABLE)
    if not found:
        raise BackendError(
            f"__DISPLAY__ executable {EXECUTABLE!r} not found on PATH.\\n"
            f"{INSTALL_INSTRUCTIONS}"
        )
    return found


def run(args: list[str], timeout: int = 120) -> dict[str, Any]:
    """Run the real software with arguments; return its result."""
    exe = find_executable()
    proc = subprocess.run(
        [exe, *args], capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise BackendError(
            f"{EXECUTABLE} exited with code {proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return {"executable": exe, "stdout": proc.stdout, "stderr": proc.stderr}


def convert(input_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Convert an intermediate file through the real software."""
    result = run(["--input", str(input_path), "--output", str(output_path)])
    return {
        "output": str(output_path),
        "format": Path(output_path).suffix.lstrip(".") or "unknown",
        "method": "real-backend",
        "executable": result["executable"],
    }


def version() -> str:
    """Ask the real software for its version string."""
    result = run(["--version"])
    return result["stdout"].strip() or result["stderr"].strip()
'''

CLI_TEMPLATE = '''\
"""CLI for __DISPLAY__ (generated by CLI-Anything, Muse-native).

Drives the real __DISPLAY__ software through its real backend. Every
command supports --json. Running with no subcommand starts the REPL.
"""

from __future__ import annotations

import functools
import json
import shlex
import sys
from pathlib import Path
from typing import Any

import click

from cli_anything.__PKG__.core.export import EXPORT_PRESETS, ExportError, render
from cli_anything.__PKG__.core.project import Project, ProjectError
from cli_anything.__PKG__.core.session import Session, SessionError
from cli_anything.__PKG__.utils import __PKG___backend as backend
from cli_anything.__PKG__.utils import preview_bundle as pb
from cli_anything.__PKG__.utils.repl_skin import ReplSkin

__version__ = "__VERSION__"


# ---------------------------------------------------------------------------
# One consistent error decorator (spec section 3.4): domain errors become
# a clear message on stderr and a non-zero exit. No bare handlers.
# ---------------------------------------------------------------------------


def handle_errors(fn):
    """Convert domain errors into Click errors (clear message, exit 1)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        """Run the command; convert domain errors into Click errors."""
        try:
            return fn(*args, **kwargs)
        except (ProjectError, SessionError, ExportError, backend.BackendError) as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


def _emit(ctx: click.Context, payload: Any, text: str) -> None:
    """Dual output: JSON on stdout with --json, human text otherwise."""
    if ctx.obj.get("json"):
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo(text)


def _open_project(path: str | Path) -> tuple[Project, Session]:
    """Open a project file, restoring undo/redo history from its sidecar.

    The project file is authoritative for the current state; the sidecar
    (<path>.session.json, written by auto-save) is authoritative for the
    undo/redo history, so `session undo` works across processes. A
    missing or corrupt sidecar means a fresh session (history lost),
    never a failed open.
    """
    project = Project.open(path)
    session = Session(state={"project": project.to_dict()})
    sidecar = Path(str(path) + ".session.json")
    if sidecar.is_file():
        try:
            loaded = Session.load(sidecar)
        except (OSError, ValueError):
            loaded = None
        if loaded is not None:
            if loaded.state.get("project") != project.to_dict():
                loaded.state = {"project": project.to_dict()}
            session = loaded
    session.project_path = str(path)
    return project, session


def _require_session(ctx: click.Context) -> Session:
    """Return the open session, or fail loudly when no project is open."""
    session = ctx.obj.get("session")
    if session is None:
        raise click.ClickException(
            "no project is open: pass --project <path> or run "
            "`project new` first"
        )
    return session


def _project_dict(ctx: click.Context) -> dict[str, Any]:
    """The project state dict held by the session ({} when none open)."""
    session = ctx.obj.get("session")
    if session is None:
        return {}
    return session.state.get("project", {})


def _maybe_autosave(ctx: click.Context) -> None:
    """Auto-save after a one-shot mutation (spec section 3.3).

    Skipped in REPL mode, with --dry-run, when nothing changed, and on
    the error path (this helper is only called after success). The save
    goes through the locked JSON session writer and the project file.
    """
    if ctx.obj.get("repl") or ctx.obj.get("dry_run"):
        return
    if not ctx.obj.get("modified"):
        return
    session = ctx.obj.get("session")
    project_path = ctx.obj.get("project_path")
    if session is None or project_path is None:
        return
    project = Project(
        name=_project_dict(ctx).get("name", "untitled"),
        items=list(_project_dict(ctx).get("items", [])),
    )
    project.created_at = _project_dict(ctx).get("created_at", project.created_at)
    project.save(project_path)
    session_path = Path(str(project_path) + ".session.json")
    session.save(session_path)
    ctx.obj["modified"] = False


# ---------------------------------------------------------------------------
# Main group: global options + REPL default.
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
@click.option("--project", "project_path", type=click.Path(), default=None, help="Project file to operate on.")
@click.option("--dry-run", is_flag=True, help="Run without saving changes to disk.")
@click.version_option(__version__, prog_name="cli-anything-__SOFTWARE__")
@click.pass_context
def cli(ctx: click.Context, as_json: bool, project_path: str | None, dry_run: bool) -> None:
    """Command-line interface for __DISPLAY__."""
    ctx.ensure_object(dict)
    ctx.obj.update(
        {"json": as_json, "dry_run": dry_run, "repl": False, "modified": False}
    )
    if project_path:
        _project, session = _open_project(project_path)
        ctx.obj["session"] = session
        ctx.obj["project_path"] = str(project_path)
    if ctx.invoked_subcommand is None:
        ctx.obj["repl"] = True
        _repl(ctx)


def _repl(ctx: click.Context) -> None:
    """Interactive REPL with the unified skin (default when no command)."""
    skin = ReplSkin("__SOFTWARE__", __version__)
    skin.print_banner()
    session = ctx.obj.get("session")
    prompt_session = skin.create_prompt_session()
    while True:
        try:
            if prompt_session is not None:
                line = prompt_session.prompt(skin.prompt())
            else:
                line = input(skin.prompt())
        except (EOFError, KeyboardInterrupt):
            skin.goodbye()
            return
        line = line.strip()
        if not line:
            continue
        if line in {"quit", "exit"}:
            skin.goodbye()
            return
        if line == "help":
            with click.Context(cli) as help_ctx:
                click.echo(cli.get_help(help_ctx))
            continue
        try:
            args = shlex.split(line)
        except ValueError as exc:
            skin.error(f"cannot parse command: {exc}")
            continue
        try:
            cli.main(args=args, standalone_mode=False, obj=dict(ctx.obj))
        except click.ClickException as exc:
            skin.error(str(exc))
        except click.Abort:
            skin.goodbye()
            return
        except SystemExit as exc:
            if exc.code not in (None, 0):
                skin.error(f"command exited with code {exc.code}")


# ---------------------------------------------------------------------------
# Mandatory probe commands: info, list, status.
# ---------------------------------------------------------------------------


@cli.command("info")
@handle_errors
@click.pass_context
def cmd_info(ctx: click.Context) -> None:
    """Show the open project (or the software) summary."""
    data = _project_dict(ctx)
    if data:
        project = Project(name=data.get("name", "untitled"), items=list(data.get("items", [])))
        project.created_at = data.get("created_at", project.created_at)
        project.path = Path(ctx.obj["project_path"]) if ctx.obj.get("project_path") else None
        payload = project.info()
    else:
        try:
            exe = backend.find_executable()
            backend_info = {"executable": exe, "installed": True}
        except backend.BackendError as exc:
            backend_info = {"executable": None, "installed": False, "error": str(exc)}
        payload = {
            "software": "__SOFTWARE__",
            "version": __version__,
            "project": None,
            "backend": backend_info,
        }
    _emit(ctx, payload, json.dumps(payload, indent=2) if ctx.obj.get("json") else _info_text(payload))


def _info_text(payload: dict[str, Any]) -> str:
    """Render the info payload as human-readable text."""
    if payload.get("project") is None and "name" not in payload:
        lines = [f"Software: {payload['software']} (v{payload['version']})"]
        lines.append(f"Backend installed: {payload['backend'].get('installed')}")
        return "\\n".join(lines)
    return (
        f"Project: {payload['name']} ({payload['item_count']} items)\\n"
        f"Created: {payload['created_at']}\\n"
        f"Path: {payload.get('path')}"
    )


@cli.command("list")
@handle_errors
@click.pass_context
def cmd_list(ctx: click.Context) -> None:
    """List the items of the open project."""
    items = _project_dict(ctx).get("items", [])
    _emit(ctx, {"items": items}, "\\n".join(
        f"{i}: [{item.get('kind')}] {item.get('value')}" for i, item in enumerate(items)
    ) or "(no items)")


@cli.command("status")
@handle_errors
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Show session status: project, modified flag, undo/redo depth."""
    session = ctx.obj.get("session")
    payload = {
        "project_path": ctx.obj.get("project_path"),
        "modified": bool(ctx.obj.get("modified")),
        "can_undo": bool(session and session.can_undo),
        "can_redo": bool(session and session.can_redo),
        "item_count": len(_project_dict(ctx).get("items", [])),
    }
    _emit(ctx, payload, "\\n".join(f"{k}: {v}" for k, v in payload.items()))


# ---------------------------------------------------------------------------
# Project group: new / open / save / close / add / remove / info.
# ---------------------------------------------------------------------------


@cli.group("project")
def project_group() -> None:
    """Manage __DISPLAY__ projects."""


@project_group.command("new")
@click.argument("name")
@click.option("--output", "output", type=click.Path(), default=None, help="Where to save the new project.")
@handle_errors
@click.pass_context
def cmd_project_new(ctx: click.Context, name: str, output: str | None) -> None:
    """Create a new project."""
    project = Project.new(name)
    session = Session(state={"project": project.to_dict()})
    ctx.obj["session"] = session
    if output:
        project.save(output)
        # A new project must not inherit another project's undo history.
        stale_sidecar = Path(str(output) + ".session.json")
        if stale_sidecar.is_file():
            stale_sidecar.unlink()
        session.project_path = str(output)
        ctx.obj["project_path"] = str(output)
        ctx.obj["modified"] = False
    else:
        ctx.obj["modified"] = True
    payload = project.info()
    _emit(ctx, payload, f"Created project {name}")


@project_group.command("open")
@click.argument("path", type=click.Path(exists=True))
@handle_errors
@click.pass_context
def cmd_project_open(ctx: click.Context, path: str) -> None:
    """Open an existing project file."""
    project, session = _open_project(path)
    ctx.obj["session"] = session
    ctx.obj["project_path"] = str(path)
    ctx.obj["modified"] = False
    _emit(ctx, project.info(), f"Opened {path}")


@project_group.command("save")
@click.argument("path", type=click.Path(), required=False)
@handle_errors
@click.pass_context
def cmd_project_save(ctx: click.Context, path: str | None) -> None:
    """Save the open project (to PATH, or its current path)."""
    session = _require_session(ctx)
    target = path or ctx.obj.get("project_path")
    if target is None:
        raise click.ClickException("no path: pass a path or open a project first")
    data = _project_dict(ctx)
    project = Project(name=data.get("name", "untitled"), items=list(data.get("items", [])))
    project.created_at = data.get("created_at", project.created_at)
    saved = project.save(target)
    session.save(Path(str(saved) + ".session.json"))
    session.project_path = str(saved)
    ctx.obj["project_path"] = str(saved)
    ctx.obj["modified"] = False
    _emit(ctx, {"saved": str(saved)}, f"Saved to {saved}")


@project_group.command("close")
@click.pass_context
def cmd_project_close(ctx: click.Context) -> None:
    """Close the open project (auto-saves first unless --dry-run)."""
    if ctx.obj.get("session") is not None:
        _maybe_autosave(ctx)
        ctx.obj["session"] = None
        ctx.obj["project_path"] = None
        ctx.obj["modified"] = False
    _emit(ctx, {"closed": True}, "Project closed.")


@project_group.command("add")
@click.argument("kind")
@click.argument("value")
@handle_errors
@click.pass_context
def cmd_project_add(ctx: click.Context, kind: str, value: str) -> None:
    """Add one item to the open project."""
    session = _require_session(ctx)

    def _apply(state: dict[str, Any]) -> None:
        """Append the new item to the project state."""
        items = state.setdefault("project", {}).setdefault("items", [])
        items.append({"kind": kind, "value": value})

    session.mutate(_apply)
    ctx.obj["modified"] = True
    _maybe_autosave(ctx)
    _emit(ctx, {"added": {"kind": kind, "value": value}}, f"Added [{kind}] {value}")


@project_group.command("remove")
@click.argument("index", type=int)
@handle_errors
@click.pass_context
def cmd_project_remove(ctx: click.Context, index: int) -> None:
    """Remove the item at INDEX from the open project."""
    session = _require_session(ctx)
    removed: list[dict[str, Any]] = []

    def _apply(state: dict[str, Any]) -> None:
        """Remove the item at INDEX from the project state."""
        items = state.get("project", {}).get("items", [])
        removed.append(items.pop(index))

    session.mutate(_apply)
    ctx.obj["modified"] = True
    _maybe_autosave(ctx)
    _emit(ctx, {"removed": removed[0] if removed else None}, f"Removed {removed[0] if removed else None}")


@project_group.command("info")
@handle_errors
@click.pass_context
def cmd_project_info(ctx: click.Context) -> None:
    """Show the open project summary."""
    data = _project_dict(ctx)
    if not data:
        raise click.ClickException("no project is open")
    project = Project(name=data.get("name", "untitled"), items=list(data.get("items", [])))
    project.created_at = data.get("created_at", project.created_at)
    project.path = Path(ctx.obj["project_path"]) if ctx.obj.get("project_path") else None
    _emit(ctx, project.info(), _info_text(project.info()))


# ---------------------------------------------------------------------------
# Session group: undo / redo / history / status.
# ---------------------------------------------------------------------------


@cli.group("session")
def session_group() -> None:
    """Undo, redo, and inspect session history."""


@session_group.command("undo")
@handle_errors
@click.pass_context
def cmd_undo(ctx: click.Context) -> None:
    """Undo the last mutation."""
    session = _require_session(ctx)
    session.undo()
    ctx.obj["modified"] = True
    _maybe_autosave(ctx)
    _emit(ctx, {"undone": True}, "Undone.")


@session_group.command("redo")
@handle_errors
@click.pass_context
def cmd_redo(ctx: click.Context) -> None:
    """Redo the last undone mutation."""
    session = _require_session(ctx)
    session.redo()
    ctx.obj["modified"] = True
    _maybe_autosave(ctx)
    _emit(ctx, {"redone": True}, "Redone.")


@session_group.command("history")
@handle_errors
@click.pass_context
def cmd_history(ctx: click.Context) -> None:
    """Show undo/redo depth."""
    session = _require_session(ctx)
    payload = {"undo_depth": len(session._undo), "redo_depth": len(session._redo)}
    _emit(ctx, payload, f"Undo depth: {payload['undo_depth']}, redo depth: {payload['redo_depth']}")


@session_group.command("status")
@handle_errors
@click.pass_context
def cmd_session_status(ctx: click.Context) -> None:
    """Show session status (same as top-level status)."""
    ctx.invoke(cmd_status)


# ---------------------------------------------------------------------------
# Export group: render through the real backend.
# ---------------------------------------------------------------------------


@cli.group("export")
def export_group() -> None:
    """Export through the real __DISPLAY__ backend."""


@export_group.command("render")
@click.option("--preset", default="default", show_default=True, help="Export preset.")
@click.option("--output", "output", type=click.Path(), default=None, help="Output path.")
@handle_errors
@click.pass_context
def cmd_export_render(ctx: click.Context, preset: str, output: str | None) -> None:
    """Render the open project via the real backend."""
    data = _project_dict(ctx)
    if not data:
        raise click.ClickException("no project is open")
    project = Project(name=data.get("name", "untitled"), items=list(data.get("items", [])))
    project.created_at = data.get("created_at", project.created_at)
    result = render(project, preset=preset, output=output)
    _emit(ctx, result, f"Exported to {result['output']} via {result['method']}")


@export_group.command("presets")
@click.pass_context
def cmd_export_presets(ctx: click.Context) -> None:
    """List available export presets."""
    payload = {name: desc for name, (desc, _) in EXPORT_PRESETS.items()}
    _emit(ctx, payload, "\\n".join(f"{name}: {desc}" for name, desc in payload.items()))


# ---------------------------------------------------------------------------
# Preview producer group (preview-bundle/v1): honest views of real state.
# ---------------------------------------------------------------------------


@cli.group("preview")
def preview_group() -> None:
    """Produce truthful preview bundles and live trajectories."""


@preview_group.command("recipes")
@click.pass_context
def cmd_preview_recipes(ctx: click.Context) -> None:
    """List available preview recipes."""
    recipes = [{"name": "default", "description": "Project state snapshot."}]
    _emit(ctx, {"software": "__SOFTWARE__", "recipes": recipes},
          "\\n".join(f"{r['name']}: {r['description']}" for r in recipes))


def _capture_bundle(recipe: str, ctx: click.Context, project_dir: Path | None, use_cache: bool) -> dict[str, Any]:
    """Capture a bundle from the open project state (real backend state)."""
    state = _project_dict(ctx)
    return pb.create_bundle(
        software="__SOFTWARE__",
        recipe=recipe,
        bundle_kind="capture",
        status="ok",
        cli_command="cli-anything-__SOFTWARE__ preview capture",
        harness_version=__version__,
        source_identity=ctx.obj.get("project_path") or "unsaved-project",
        source_data=json.dumps(state, sort_keys=True, default=str),
        headline=f"__DISPLAY__ {recipe} preview",
        facts=[f"Items: {len(state.get('items', []))}"],
        project_dir=project_dir,
        use_cache=use_cache,
    )


@preview_group.command("capture")
@click.option("--recipe", default="default", show_default=True, help="Recipe name.")
@click.option("--no-cache", is_flag=True, help="Bypass cached bundle lookup.")
@handle_errors
@click.pass_context
def cmd_preview_capture(ctx: click.Context, recipe: str, no_cache: bool) -> None:
    """Capture a preview bundle through the real backend."""
    proj = ctx.obj.get("project_path")
    res = _capture_bundle(recipe, ctx, Path(proj).parent if proj else None, not no_cache)
    _emit(ctx, res, f"Bundle {res['bundle_id']} -> {res['bundle_dir']}")


@preview_group.command("latest")
@click.option("--recipe", default=None, help="Filter by recipe.")
@handle_errors
@click.pass_context
def cmd_preview_latest(ctx: click.Context, recipe: str | None) -> None:
    """Return the latest existing preview bundle without rendering."""
    proj = ctx.obj.get("project_path")
    res = pb.latest_bundle("__SOFTWARE__", recipe=recipe, project_dir=Path(proj).parent if proj else None)
    if res is None:
        raise click.ClickException("No preview bundles found.")
    _emit(ctx, res, f"Latest: {res['bundle_id']} ({res['bundle_dir']})")


@preview_group.command("diff")
@click.argument("bundle_a")
@click.argument("bundle_b")
@handle_errors
@click.pass_context
def cmd_preview_diff(ctx: click.Context, bundle_a: str, bundle_b: str) -> None:
    """Publish an immutable diff bundle comparing two bundles."""
    a = pb.inspect_bundle(bundle_a)
    b = pb.inspect_bundle(bundle_b)
    proj = ctx.obj.get("project_path")
    res = pb.create_bundle(
        software="__SOFTWARE__",
        recipe="diff",
        bundle_kind="diff",
        status="ok",
        cli_command="cli-anything-__SOFTWARE__ preview diff",
        harness_version=__version__,
        source_identity=f"{a['bundle_id']}..{b['bundle_id']}",
        source_data={"a": a, "b": b},
        headline=f"Diff {a['bundle_id']} -> {b['bundle_id']}",
        facts=[f"A status={a['status']}", f"B status={b['status']}"],
        source_bundles=[a["bundle_dir"], b["bundle_dir"]],
        project_dir=Path(proj).parent if proj else None,
        use_cache=False,
    )
    _emit(ctx, res, f"Diff bundle {res['bundle_id']} -> {res['bundle_dir']}")


@preview_group.group("live")
def preview_live_group() -> None:
    """Manage live preview sessions and trajectories."""


@preview_live_group.command("start")
@click.option("--recipe", default="default", show_default=True)
@handle_errors
@click.pass_context
def cmd_live_start(ctx: click.Context, recipe: str) -> None:
    """Start a live session and publish an initial bundle."""
    proj = ctx.obj.get("project_path")
    pdir = Path(proj).parent if proj else None
    bundle = _capture_bundle(recipe, ctx, pdir, False)
    res = pb.live_start("__SOFTWARE__", recipe, bundle, project_dir=pdir)
    _emit(ctx, res, f"Live started: {res['current_bundle_id']}")


@preview_live_group.command("push")
@click.option("--recipe", default="default", show_default=True)
@handle_errors
@click.pass_context
def cmd_live_push(ctx: click.Context, recipe: str) -> None:
    """Push a new bundle to the live session."""
    proj = ctx.obj.get("project_path")
    pdir = Path(proj).parent if proj else None
    bundle = _capture_bundle(recipe, ctx, pdir, False)
    res = pb.live_push("__SOFTWARE__", recipe, bundle, project_dir=pdir)
    _emit(ctx, res, f"Live pushed: {res['current_bundle_id']}")


@preview_live_group.command("status")
@click.pass_context
def cmd_live_status(ctx: click.Context) -> None:
    """Report live-session state (cheap introspection)."""
    proj = ctx.obj.get("project_path")
    res = pb.live_status("__SOFTWARE__", project_dir=Path(proj).parent if proj else None)
    _emit(ctx, res, f"Active: {res.get('active')}")


@preview_live_group.command("stop")
@click.pass_context
def cmd_live_stop(ctx: click.Context) -> None:
    """Stop publishing without deleting history."""
    proj = ctx.obj.get("project_path")
    res = pb.live_stop("__SOFTWARE__", project_dir=Path(proj).parent if proj else None)
    _emit(ctx, res, f"Active: {res.get('active')}")


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (spec section 8.6: the `main` form)."""
    cli(argv, standalone_mode=True)


if __name__ == "__main__":
    main()
'''

CLI_SERVICE_TEMPLATE = '''\
"""CLI for __DISPLAY__ (generated by CLI-Anything, Muse-native).

Stateless service client: the "session" is connection parameters
(host, port, scheme, credentials) from environment variables, with CLI
options overriding them. No project file, no undo/redo. Every command
supports --json. Running with no subcommand starts the REPL.
"""

from __future__ import annotations

import functools
import json
import shlex
from pathlib import Path
from typing import Any

import click

from cli_anything.__PKG__.core.client import ServiceClient, ServiceError
from cli_anything.__PKG__.core.session import ServiceSession
from cli_anything.__PKG__.utils.repl_skin import ReplSkin

__version__ = "__VERSION__"


def handle_errors(fn):
    """Convert domain errors into Click errors (clear message, exit 1)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        """Run the command; convert service errors into Click errors."""
        try:
            return fn(*args, **kwargs)
        except ServiceError as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


def _emit(ctx: click.Context, payload: Any, text: str) -> None:
    """Dual output: JSON on stdout with --json, human text otherwise."""
    if ctx.obj.get("json"):
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo(text)


def _client(ctx: click.Context) -> ServiceClient:
    """Build the service client from the connection session."""
    session: ServiceSession = ctx.obj["connection"]
    return ServiceClient(session)


@click.group(invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
@click.option("--host", default=None, help="Service host (or __PKG___HOST).")
@click.option("--port", type=int, default=None, help="Service port (or __PKG___PORT).")
@click.option("--scheme", default=None, help="http or https (or __PKG___SCHEME).")
@click.option("--user", default=None, help="Username (or __PKG___USER).")
@click.option("--password", default=None, help="Password (or __PKG___PASSWORD).")
@click.version_option(__version__, prog_name="cli-anything-__SOFTWARE__")
@click.pass_context
def cli(ctx: click.Context, as_json: bool, host: str | None, port: int | None,
        scheme: str | None, user: str | None, password: str | None) -> None:
    """Command-line interface for __DISPLAY__."""
    ctx.ensure_object(dict)
    ctx.obj.update({"json": as_json, "repl": False})
    ctx.obj["connection"] = ServiceSession(
        software="__SOFTWARE__", host=host, port=port, scheme=scheme,
        user=user, password=password,
    )
    if ctx.invoked_subcommand is None:
        ctx.obj["repl"] = True
        _repl(ctx)


def _repl(ctx: click.Context) -> None:
    """Interactive REPL with the unified skin (default when no command)."""
    skin = ReplSkin("__SOFTWARE__", __version__)
    skin.print_banner()
    prompt_session = skin.create_prompt_session()
    while True:
        try:
            if prompt_session is not None:
                line = prompt_session.prompt(skin.prompt())
            else:
                line = input(skin.prompt())
        except (EOFError, KeyboardInterrupt):
            skin.goodbye()
            return
        line = line.strip()
        if not line:
            continue
        if line in {"quit", "exit"}:
            skin.goodbye()
            return
        if line == "help":
            with click.Context(cli) as help_ctx:
                click.echo(cli.get_help(help_ctx))
            continue
        try:
            args = shlex.split(line)
        except ValueError as exc:
            skin.error(f"cannot parse command: {exc}")
            continue
        try:
            cli.main(args=args, standalone_mode=False, obj=dict(ctx.obj))
        except click.ClickException as exc:
            skin.error(str(exc))
        except click.Abort:
            skin.goodbye()
            return
        except SystemExit as exc:
            if exc.code not in (None, 0):
                skin.error(f"command exited with code {exc.code}")


@cli.command("info")
@handle_errors
@click.pass_context
def cmd_info(ctx: click.Context) -> None:
    """Show the software and connection summary."""
    session: ServiceSession = ctx.obj["connection"]
    payload = {
        "software": "__SOFTWARE__",
        "version": __version__,
        "connection": session.to_dict(),
        "base_url": session.base_url(),
    }
    _emit(ctx, payload, f"Software: __SOFTWARE__ (v{__version__})\\nBase URL: {session.base_url()}")


@cli.command("list")
@handle_errors
@click.pass_context
def cmd_list(ctx: click.Context) -> None:
    """List the service's top-level resources (GET /)."""
    client = _client(ctx)
    payload = client.get("/")
    _emit(ctx, payload, json.dumps(payload, indent=2))


@cli.command("status")
@handle_errors
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Check the service health (GET /health, falls back to /).

    An unreachable service is an error, not a report: live commands
    fail gracefully with a clear message and a non-zero exit (spec
    section 6.3).
    """
    client = _client(ctx)
    payload = client.health()
    if not payload.get("reachable"):
        raise click.ClickException(
            f"service unreachable at {payload.get('url')}: connection "
            "refused or timed out. Is the service running? Check "
            "host/port or start the service."
        )
    _emit(ctx, payload, f"Reachable: {payload.get('reachable')} ({payload.get('url')})")


@cli.group("server")
def server_group() -> None:
    """Server health and connection commands."""


@server_group.command("status")
@handle_errors
@click.pass_context
def cmd_server_status(ctx: click.Context) -> None:
    """Check the service health (same as top-level status)."""
    ctx.invoke(cmd_status)


@server_group.command("info")
@handle_errors
@click.pass_context
def cmd_server_info(ctx: click.Context) -> None:
    """Show the connection summary (same as top-level info)."""
    ctx.invoke(cmd_info)


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (spec section 8.6: the `main` form)."""
    cli(argv, standalone_mode=True)


if __name__ == "__main__":
    main()
'''

CLIENT_TEMPLATE = '''\
"""HTTP client for __DISPLAY__ (generated service shape).

Standard-library only (urllib): the service is the real software, and
the CLI talks to it over HTTP(S) from the host. No Docker, no extra
HTTP dependency. Unreachable services fail loudly with a clear message.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .session import ServiceSession


class ServiceError(Exception):
    """Raised when the service cannot be reached or answers badly."""


class ServiceClient:
    """Thin urllib client for the __DISPLAY__ service."""

    def __init__(self, session: ServiceSession, timeout: int = 15) -> None:
        self.session = session
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = self.session.base_url().rstrip("/") + "/" + path.lstrip("/")
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.session.user:
            import base64

            token = base64.b64encode(
                f"{self.session.user}:{self.session.password or ''}".encode("utf-8")
            ).decode("ascii")
            request.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise ServiceError(
                f"service answered HTTP {exc.code} for {method} {url}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ServiceError(
                f"service unreachable at {url}: {exc}. Is __DISPLAY__ "
                "running? Check host/port or start the service."
            ) from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}

    def get(self, path: str) -> Any:
        """GET a path; returns parsed JSON (or {'raw': body})."""
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        """POST a JSON payload; returns parsed JSON (or {'raw': body})."""
        return self._request("POST", path, payload)

    def health(self) -> dict[str, Any]:
        """GET /health, falling back to GET /; reports reachability."""
        base = self.session.base_url()
        for path in ("/health", "/"):
            try:
                self._request("GET", path)
                return {"reachable": True, "url": base + path}
            except ServiceError:
                continue
        return {"reachable": False, "url": base}
'''

BACKEND_SERVICE_TEMPLATE = '''\
"""Backend wrapper for __DISPLAY__ (generated service shape).

The "real software" is the __DISPLAY__ service itself. This module is
the single place that knows how to reach it; every command goes through
here. An unreachable service fails loudly with a clear message — never
a silent fake.
"""

from __future__ import annotations

from typing import Any

from ..core.client import ServiceClient, ServiceError
from ..core.session import ServiceSession


class BackendError(ServiceError):
    """Raised when the real service cannot be used."""


def health(session: ServiceSession | None = None) -> dict[str, Any]:
    """Check the service health; returns the reachability report."""
    session = session or ServiceSession(software="__SOFTWARE__")
    return ServiceClient(session).health()


def request(
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    session: ServiceSession | None = None,
) -> Any:
    """One HTTP call to the real service (GET by default)."""
    session = session or ServiceSession(software="__SOFTWARE__")
    client = ServiceClient(session)
    if method.upper() == "POST":
        return client.post(path, payload or {})
    return client.get(path)
'''

TEST_CORE_TEMPLATE = '''\
"""Unit tests for the __DISPLAY__ harness (synthetic data only)."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from cli_anything.__PKG__.core.export import EXPORT_PRESETS, ExportError, render
from cli_anything.__PKG__.core.project import Project, ProjectError
from cli_anything.__PKG__.core.session import ServiceSession, Session, SessionError
from cli_anything.__PKG__.utils import __PKG___backend as backend


# --- core/project.py -------------------------------------------------------


def test_project_new_open_save_info_round_trip(tmp_path):
    project = Project.new("demo")
    project.add_item("note", "hello")
    saved = project.save(tmp_path / "demo.json")
    assert saved.is_file()
    reopened = Project.open(saved)
    assert reopened.name == "demo"
    assert reopened.items[0]["value"] == "hello"
    info = reopened.info()
    assert info["item_count"] == 1
    assert info["path"] == str(saved)


def test_project_open_missing_raises(tmp_path):
    with pytest.raises(ProjectError):
        Project.open(tmp_path / "nope.json")


def test_project_save_without_path_raises():
    with pytest.raises(ProjectError):
        Project.new("x").save()


def test_project_remove_out_of_range_raises():
    project = Project.new("x")
    with pytest.raises(IndexError):
        project.remove_item(0)


def test_project_open_bad_content_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectError):
        Project.open(bad)


# --- core/session.py -------------------------------------------------------


def test_session_mutate_undo_redo():
    session = Session(state={"project": {"items": []}})
    session.mutate(lambda s: s["project"]["items"].append({"kind": "a"}))
    assert len(session.state["project"]["items"]) == 1
    assert session.can_undo
    session.undo()
    assert session.state["project"]["items"] == []
    assert session.can_redo
    session.redo()
    assert len(session.state["project"]["items"]) == 1


def test_session_empty_undo_redo_raise():
    session = Session()
    with pytest.raises(SessionError):
        session.undo()
    with pytest.raises(SessionError):
        session.redo()


def test_session_history_capped_at_50():
    session = Session(state={"n": 0})
    for i in range(55):
        session.mutate(lambda s, i=i: s.update({"n": i}))
    assert len(session._undo) == 50


def test_session_save_load_round_trip(tmp_path):
    session = Session(state={"project": {"name": "demo"}})
    session.project_path = str(tmp_path / "demo.json")
    saved = session.save(tmp_path / "session.json")
    loaded = Session.load(saved)
    assert loaded.state == session.state
    assert loaded.project_path == session.project_path
    assert loaded.modified is False


def test_service_session_env_loading(monkeypatch):
    monkeypatch.setenv("__PKG___HOST".upper(), "example.test")
    monkeypatch.setenv("__PKG___PORT".upper(), "1234")
    monkeypatch.setenv("__PKG___SCHEME".upper(), "https")
    session = ServiceSession(software="__SOFTWARE__")
    assert session.host == "example.test"
    assert session.port == 1234
    assert session.scheme == "https"
    assert session.base_url() == "https://example.test:1234"


# --- core/export.py --------------------------------------------------------


def test_export_unknown_preset_raises(tmp_path):
    with pytest.raises(ExportError):
        render(Project.new("x"), preset="nope", output=tmp_path / "o.out")


def test_export_json_preset_writes_file(tmp_path):
    project = Project.new("demo")
    project.add_item("note", "hello")
    result = render(project, preset="json", output=tmp_path / "demo.json")
    assert Path(result["output"]).is_file()
    assert json.loads(Path(result["output"]).read_text())["name"] == "demo"
    assert result["method"] == "intermediate-file"


def test_export_default_preset_missing_backend_raises(tmp_path, monkeypatch):
    # The real software is not installed here: export must fail loudly,
    # never silently fake a render.
    monkeypatch.setenv("PATH", str(tmp_path))
    project = Project.new("demo")
    with pytest.raises(backend.BackendError):
        render(project, preset="default", output=tmp_path / "demo.out")


# --- utils/__PKG___backend.py -----------------------------------------------


def test_backend_missing_executable_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(backend.BackendError) as excinfo:
        backend.find_executable()
    assert "not found on PATH" in str(excinfo.value)
    assert "Install" in str(excinfo.value)


def test_backend_run_with_fake_executable(tmp_path, monkeypatch):
    fake = tmp_path / "__SOFTWARE__"
    fake.write_text("#!/bin/sh\\necho FAKE-BACKEND \\\"$@\\\"\\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = backend.run(["--version"])
    assert "FAKE-BACKEND" in result["stdout"]
'''

TEST_E2E_TEMPLATE = '''\
"""E2E + CLI subprocess tests for the __DISPLAY__ harness.

Rules (spec section 6.3): always use _resolve_cli; never set the working
directory; force installed mode in CI/release testing; tests FAIL (never
skip) when the real software is missing.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

COMMAND = "cli-anything-__SOFTWARE__"
MODULE = "cli_anything.__PKG__.__PKG___cli"
HARNESS_ROOT = Path(__file__).resolve().parents[3]


def _resolve_cli():
    """Prefer the installed command; fall back to the module with a note.

    CLI_ANYTHING_FORCE_INSTALLED=1 raises instead of falling back.
    """
    installed = shutil.which(COMMAND)
    if installed:
        print(f"[_resolve_cli] Using installed command: {installed}")
        return [installed]
    if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED") == "1":
        raise RuntimeError(
            f"installed command {COMMAND!r} not found on PATH and "
            "CLI_ANYTHING_FORCE_INSTALLED=1"
        )
    print(f"[_resolve_cli] Falling back to module: {MODULE}")
    return [sys.executable, "-m", MODULE]


def _run_cli(*args, check=True, env=None):
    """Run the resolved CLI with captured output (no cwd override).

    PYTHONPATH always includes the harness root, so the module fallback
    (and the installed console script) import this harness's source.
    """
    merged = dict(os.environ)
    merged["PYTHONPATH"] = (
        str(HARNESS_ROOT) + os.pathsep + merged.get("PYTHONPATH", "")
    )
    if env:
        merged.update(env)
    proc = subprocess.run(
        [*_resolve_cli(), *args], capture_output=True, text=True, env=merged
    )
    if check:
        assert proc.returncode == 0, f"STDOUT:\\n{proc.stdout}\\nSTDERR:\\n{proc.stderr}"
    return proc


# --- CLI surface ------------------------------------------------------------


def test_cli_help_lists_groups():
    proc = _run_cli("--help")
    for group in ("project", "session", "export", "preview", "info", "list", "status"):
        assert group in proc.stdout


def test_cli_json_info_without_project():
    proc = _run_cli("--json", "info")
    payload = json.loads(proc.stdout)
    assert payload["software"] == "__SOFTWARE__"
    assert payload["version"] == "__VERSION__"


def test_cli_status_json_shape():
    proc = _run_cli("--json", "status")
    payload = json.loads(proc.stdout)
    assert payload["project_path"] is None
    assert payload["can_undo"] is False


# --- Workflow: project new -> add -> save -> reopen -> undo/redo -----------


def test_workflow_project_round_trip(tmp_path):
    project_path = tmp_path / "demo.json"
    _run_cli("project", "new", "demo", "--output", str(project_path))
    assert project_path.is_file()
    _run_cli("--project", str(project_path), "project", "add", "note", "hello")
    data = json.loads(project_path.read_text())
    assert data["items"][0]["value"] == "hello"
    proc = _run_cli("--json", "--project", str(project_path), "list")
    assert json.loads(proc.stdout)["items"][0]["value"] == "hello"
    _run_cli("--project", str(project_path), "session", "undo")
    data = json.loads(project_path.read_text())
    assert data["items"] == []
    _run_cli("--project", str(project_path), "session", "redo")
    data = json.loads(project_path.read_text())
    assert data["items"][0]["value"] == "hello"


def test_workflow_export_json_preset(tmp_path):
    project_path = tmp_path / "demo.json"
    _run_cli("project", "new", "demo", "--output", str(project_path))
    out = tmp_path / "demo-export.json"
    _run_cli("--project", str(project_path), "export", "render",
             "--preset", "json", "--output", str(out))
    assert out.is_file()
    assert json.loads(out.read_text())["name"] == "demo"


def test_workflow_preview_capture_and_latest(tmp_path):
    project_path = tmp_path / "demo.json"
    _run_cli("project", "new", "demo", "--output", str(project_path))
    _run_cli("--project", str(project_path), "project", "add", "note", "hello")
    proc = _run_cli("--json", "--project", str(project_path), "preview", "capture")
    bundle = json.loads(proc.stdout)
    assert bundle["bundle_id"]
    assert Path(bundle["bundle_dir"]).is_dir()
    proc = _run_cli("--json", "--project", str(project_path), "preview", "latest")
    latest = json.loads(proc.stdout)
    assert latest["bundle_id"] == bundle["bundle_id"]


# --- True backend (zero-compromise): FAILS when software is missing --------


def test_true_backend_export_via_real_software(tmp_path):
    """Invoke the real software. No graceful degradation: if it is not
    installed, this test fails — it must not skip."""
    exe = shutil.which("__SOFTWARE__")
    assert exe is not None, (
        "real software __SOFTWARE__ not installed on PATH; install it per "
        "the harness README, then re-run"
    )
    project_path = tmp_path / "demo.json"
    _run_cli("project", "new", "demo", "--output", str(project_path))
    out = tmp_path / "demo.out"
    _run_cli("--project", str(project_path), "export", "render",
             "--output", str(out))
    assert out.is_file()
    assert out.stat().st_size > 0
    print(f"artifact: {out}")
'''

TEST_CORE_SERVICE_TEMPLATE = '''\
"""Unit tests for the __DISPLAY__ service harness (synthetic data only)."""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from cli_anything.__PKG__.core.client import ServiceClient, ServiceError
from cli_anything.__PKG__.core.session import ServiceSession


def test_service_session_defaults():
    session = ServiceSession(software="__SOFTWARE__")
    assert session.host == "localhost"
    assert session.port == 8080
    assert session.scheme == "http"
    assert session.base_url() == "http://localhost:8080"


def test_service_session_env_loading(monkeypatch):
    monkeypatch.setenv("__PKG___HOST".upper(), "example.test")
    monkeypatch.setenv("__PKG___PORT".upper(), "1234")
    monkeypatch.setenv("__PKG___SCHEME".upper(), "https")
    session = ServiceSession(software="__SOFTWARE__")
    assert session.base_url() == "https://example.test:1234"


def test_service_session_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("__PKG___HOST".upper(), "example.test")
    session = ServiceSession(software="__SOFTWARE__", host="other.test", port=9999)
    assert session.host == "other.test"
    assert session.port == 9999


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def live_service():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_client_health_against_real_local_service(live_service):
    session = ServiceSession(software="__SOFTWARE__", host="127.0.0.1", port=live_service)
    client = ServiceClient(session)
    report = client.health()
    assert report["reachable"] is True
    assert client.get("/")["ok"] is True


def test_client_unreachable_service_raises_clear_error():
    session = ServiceSession(software="__SOFTWARE__", host="127.0.0.1", port=1)
    client = ServiceClient(session, timeout=5)
    with pytest.raises(ServiceError) as excinfo:
        client.get("/health")
    assert "unreachable" in str(excinfo.value)
'''

TEST_E2E_SERVICE_TEMPLATE = '''\
"""E2E + CLI subprocess tests for the __DISPLAY__ service harness.

Rules (spec section 6.3): always use _resolve_cli; never set the working
directory; force installed mode in CI/release testing. Live commands
fail gracefully (clear error, non-zero exit) when the service is
unreachable; the true-backend test runs a real local HTTP service.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

COMMAND = "cli-anything-__SOFTWARE__"
MODULE = "cli_anything.__PKG__.__PKG___cli"
HARNESS_ROOT = Path(__file__).resolve().parents[3]


def _resolve_cli():
    """Prefer the installed command; fall back to the module with a note.

    CLI_ANYTHING_FORCE_INSTALLED=1 raises instead of falling back.
    """
    installed = shutil.which(COMMAND)
    if installed:
        print(f"[_resolve_cli] Using installed command: {installed}")
        return [installed]
    if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED") == "1":
        raise RuntimeError(
            f"installed command {COMMAND!r} not found on PATH and "
            "CLI_ANYTHING_FORCE_INSTALLED=1"
        )
    print(f"[_resolve_cli] Falling back to module: {MODULE}")
    return [sys.executable, "-m", MODULE]


def _run_cli(*args, check=True, env=None):
    """Run the resolved CLI with captured output (no cwd override).

    PYTHONPATH always includes the harness root, so the module fallback
    (and the installed console script) import this harness's source.
    """
    merged = dict(os.environ)
    merged["PYTHONPATH"] = (
        str(HARNESS_ROOT) + os.pathsep + merged.get("PYTHONPATH", "")
    )
    if env:
        merged.update(env)
    proc = subprocess.run(
        [*_resolve_cli(), *args], capture_output=True, text=True, env=merged
    )
    if check:
        assert proc.returncode == 0, f"STDOUT:\\n{proc.stdout}\\nSTDERR:\\n{proc.stderr}"
    return proc


def test_cli_help_lists_groups():
    proc = _run_cli("--help")
    for group in ("server", "info", "list", "status"):
        assert group in proc.stdout


def test_cli_json_info():
    proc = _run_cli("--json", "--host", "127.0.0.1", "--port", "8080", "info")
    payload = json.loads(proc.stdout)
    assert payload["software"] == "__SOFTWARE__"
    assert payload["base_url"] == "http://127.0.0.1:8080"


def test_live_command_fails_gracefully_when_unreachable():
    """Live commands fail gracefully: clear error, non-zero exit."""
    proc = _run_cli("--host", "127.0.0.1", "--port", "1", "server", "status", check=False)
    assert proc.returncode != 0
    assert "unreachable" in proc.stderr.lower() or "unreachable" in proc.stdout.lower()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_true_backend_against_real_local_service():
    """True-backend test: a real HTTP service (stdlib, in-process)."""
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        proc = _run_cli("--json", "--host", "127.0.0.1", "--port", str(port), "server", "status")
        payload = json.loads(proc.stdout)
        assert payload["reachable"] is True
    finally:
        server.shutdown()
        server.server_close()
'''

EXAMPLE_TEMPLATE = '''\
"""Basic workflow example for __DISPLAY__ (generated).

Runs the installed CLI end to end: create a project, add an item,
save, inspect, export the JSON preset.
"""

import json
import subprocess
import sys
from pathlib import Path

COMMAND = "cli-anything-__SOFTWARE__"


def run(*args, check=True):
    proc = subprocess.run([COMMAND, *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"failed: {args}\\n{proc.stdout}\\n{proc.stderr}")
    return proc


def main() -> int:
    workdir = Path("/tmp/__SOFTWARE__-example")
    workdir.mkdir(parents=True, exist_ok=True)
    project = workdir / "demo.json"
    run("project", "new", "demo", "--output", str(project))
    run("--project", str(project), "project", "add", "note", "hello")
    print(run("--json", "--project", str(project), "info").stdout)
    out = workdir / "demo-export.json"
    run("--project", str(project), "export", "render",
        "--preset", "json", "--output", str(out))
    print(f"Exported: {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
