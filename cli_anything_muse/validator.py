"""Validate command: audit a harness against the methodology.

Implements spec section 2 (validate): eight check categories, each
with per-check results and pass counts, plus an overall PASS/FAIL.
Both harness shapes are accepted (spec section 8.7): the project shape
(project/session/export core) and the service shape (client + connection
session, no undo/redo).

Checks are static (AST parsing, text search, py_compile) except the
"all tests pass" check, which executes the harness test suite.
"""

from __future__ import annotations

import ast
import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants: the eight categories, in spec order.
# ---------------------------------------------------------------------------

CATEGORIES = [
    "directory_structure",
    "required_files",
    "cli_standards",
    "core_standards",
    "test_standards",
    "documentation_standards",
    "packaging_standards",
    "code_quality",
]


class ValidationError(Exception):
    """Raised when a harness cannot be located or read."""


def _check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    """One check result."""
    return {"name": name, "passed": bool(passed), "detail": detail}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_ast(path: Path) -> ast.Module | None:
    try:
        return ast.parse(_read(path), filename=str(path))
    except SyntaxError:
        return None


def _function_names(tree: ast.Module | None) -> set[str]:
    if tree is None:
        return set()
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _has_decorator_kind(tree: ast.Module | None, kind: str) -> bool:
    """True when any function is decorated as a Click group/command."""
    if tree is None:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(func, ast.Attribute) and func.attr == kind:
                return True
    return False


# ---------------------------------------------------------------------------
# Shape detection.
# ---------------------------------------------------------------------------


def detect_shape(harness_path: str | Path) -> str:
    """Detect 'project' or 'service' from the SOP, else from the CLI file."""
    harness = Path(harness_path)
    for sop in harness.parent.glob("*.md"):
        text = _read(sop)
        match = re.search(r"\*\*Shape:\*\*\s*`?(project|service)`?", text)
        if match:
            return match.group(1)
    subpkg = _find_subpkg(harness)
    if subpkg is not None:
        cli_files = list(subpkg.glob("*_cli.py"))
        if cli_files:
            text = _read(cli_files[0])
            if "--host" in text and "ServiceSession" in text:
                return "service"
            if "--project" in text:
                return "project"
    return "project"


def _find_subpkg(harness: Path) -> Path | None:
    """The CLI sub-package under cli_anything/ (None when absent)."""
    ns_dir = harness / "cli_anything"
    if not ns_dir.is_dir():
        return None
    for sub in sorted(ns_dir.iterdir()):
        if sub.is_dir() and (sub / "__init__.py").is_file():
            return sub
    return None


# ---------------------------------------------------------------------------
# The eight categories.
# ---------------------------------------------------------------------------


def _check_structure(harness: Path, subpkg: Path | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    ns_dir = harness / "cli_anything"
    checks.append(_check(
        "cli_anything/ is a PEP 420 namespace (no __init__.py)",
        ns_dir.is_dir() and not (ns_dir / "__init__.py").exists(),
        "cli_anything/__init__.py must NOT exist",
    ))
    checks.append(_check(
        "sub-package has __init__.py",
        subpkg is not None,
        "no sub-package with __init__.py under cli_anything/",
    ))
    if subpkg is not None:
        for sub in ("core", "utils", "tests"):
            checks.append(_check(
                f"subdirectory {sub}/ present",
                (subpkg / sub).is_dir(),
                f"missing {sub}/",
            ))
    setup = _read(harness / "setup.py")
    checks.append(_check(
        "namespace-package discovery in setup.py",
        "find_namespace_packages" in setup and "cli_anything.*" in setup,
        "setup.py must use find_namespace_packages(include=['cli_anything.*'])",
    ))
    return checks


def _check_required_files(
    harness: Path, subpkg: Path | None, shape: str
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    readme_ok = (harness / "README.md").is_file() or (
        subpkg is not None and (subpkg / "README.md").is_file()
    )
    checks.append(_check("README.md exists", readme_ok,
                         "no README.md in the harness"))
    checks.append(_check("TEST.md exists",
                         bool(subpkg and (subpkg / "tests" / "TEST.md").is_file()),
                         "missing tests/TEST.md"))
    checks.append(_check("test_core.py exists",
                         bool(subpkg and (subpkg / "tests" / "test_core.py").is_file()),
                         "missing tests/test_core.py"))
    checks.append(_check("test_full_e2e.py exists",
                         bool(subpkg and (subpkg / "tests" / "test_full_e2e.py").is_file()),
                         "missing tests/test_full_e2e.py"))
    sops = list(harness.parent.glob("*.md")) if harness.name == "agent-harness" else []
    checks.append(_check("SOP document exists (<SOFTWARE>.md)",
                         len(sops) > 0,
                         "no SOP document next to agent-harness/"))
    if subpkg is not None:
        cli_files = list(subpkg.glob("*_cli.py"))
        checks.append(_check("CLI entry point exists (<software>_cli.py)",
                             len(cli_files) > 0, "no *_cli.py in the sub-package"))
        if shape == "service":
            checks.append(_check("core/client.py exists",
                                 (subpkg / "core" / "client.py").is_file(),
                                 "service shape needs core/client.py"))
            checks.append(_check("core/session.py exists",
                                 (subpkg / "core" / "session.py").is_file(),
                                 "service shape needs core/session.py"))
        else:
            for module in ("project.py", "session.py", "export.py"):
                checks.append(_check(f"core/{module} exists",
                                     (subpkg / "core" / module).is_file(),
                                     f"project shape needs core/{module}"))
    return checks


def _check_cli_standards(subpkg: Path | None, shape: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if subpkg is None:
        return [_check("CLI file parseable", False, "no sub-package found")]
    cli_files = list(subpkg.glob("*_cli.py"))
    if not cli_files:
        return [_check("CLI file exists", False, "no *_cli.py")]
    tree = _parse_ast(cli_files[0])
    text = _read(cli_files[0])
    checks.append(_check("uses Click", "import click" in text, "no Click import"))
    checks.append(_check("command groups (not flat commands)",
                         _has_decorator_kind(tree, "group"),
                         "no @*.group decorator found"))
    checks.append(_check("every command supports --json",
                         '"--json"' in text or "'--json'" in text,
                         "no --json option on the main group"))
    if shape == "project":
        checks.append(_check("--project option present",
                             '"--project"' in text or "'--project'" in text,
                             "project shape needs a --project option"))
        checks.append(_check("--dry-run option present",
                             '"--dry-run"' in text or "'--dry-run'" in text,
                             "project shape needs a --dry-run option"))
    else:
        checks.append(_check("connection options present",
                             '"--host"' in text and '"--port"' in text,
                             "service shape needs --host/--port options"))
    checks.append(_check("consistent error-handling decorator",
                         "handle_errors" in text or "ClickException" in text,
                         "no error decorator / ClickException usage"))
    checks.append(_check("REPL is the default (invoke_without_command)",
                         "invoke_without_command=True" in text,
                         "main group must use invoke_without_command=True"))
    checks.append(_check("global session state (ctx.obj)",
                         "ctx.obj" in text,
                         "no ctx.obj session state"))
    return checks


def _check_core_standards(subpkg: Path | None, shape: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if subpkg is None:
        return [_check("core modules present", False, "no sub-package found")]
    if shape == "service":
        client = _parse_ast(subpkg / "core" / "client.py")
        names = _function_names(client)
        checks.append(_check("client has get/post/health",
                             {"get", "post", "health"} <= names,
                             f"client functions found: {sorted(names)}"))
        session = _parse_ast(subpkg / "core" / "session.py")
        checks.append(_check("ServiceSession class exists",
                             "ServiceSession" in _read(subpkg / "core" / "session.py"),
                             "no ServiceSession in core/session.py"))
        checks.append(_check("client uses stdlib urllib (no extra HTTP dep)",
                             "urllib" in _read(subpkg / "core" / "client.py"),
                             "service client should prefer stdlib urllib"))
        return checks
    project = _parse_ast(subpkg / "core" / "project.py")
    names = _function_names(project)
    for method in ("new", "open", "save", "info"):
        checks.append(_check(f"project.{method} exists",
                             method in names, f"missing Project.{method}"))
    session = _parse_ast(subpkg / "core" / "session.py")
    names = _function_names(session)
    for method in ("mutate", "undo", "redo", "snapshot", "save", "load"):
        checks.append(_check(f"session.{method} exists",
                             method in names, f"missing Session.{method}"))
    export = _parse_ast(subpkg / "core" / "export.py")
    checks.append(_check("export.render exists",
                         "render" in _function_names(export),
                         "no render() in core/export.py"))
    checks.append(_check("export presets collection exists",
                         "EXPORT_PRESETS" in _read(subpkg / "core" / "export.py"),
                         "no EXPORT_PRESETS in core/export.py"))
    # Docstrings and type hints everywhere (sampled over core modules).
    for module in ("project.py", "session.py", "export.py"):
        path = subpkg / "core" / module
        tree = _parse_ast(path)
        missing_doc = [
            node.name
            for node in ast.walk(tree or ast.Module(body=[], type_ignores=[]))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not ast.get_docstring(node)
        ]
        checks.append(_check(f"{module}: all functions documented",
                             not missing_doc,
                             f"undocumented: {missing_doc}" if missing_doc else ""))
    return checks


def _check_test_standards(harness: Path, subpkg: Path | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if subpkg is None:
        return [_check("tests present", False, "no sub-package found")]
    test_md = _read(subpkg / "tests" / "TEST.md")
    checks.append(_check("TEST.md has plan + results",
                         "## 1. Test inventory plan" in test_md
                         and ("## Run" in test_md or "## Results" in test_md),
                         "TEST.md needs a plan section and a results section"))
    e2e = _read(subpkg / "tests" / "test_full_e2e.py")
    checks.append(_check("E2E tests use real files (tmp_path, real outputs)",
                         "tmp_path" in e2e,
                         "E2E tests should use tmp_path fixtures"))
    checks.append(_check("workflow tests exist",
                         "workflow" in e2e.lower(),
                         "no workflow test in test_full_e2e.py"))
    checks.append(_check("subprocess tests use the resolver helper",
                         "_resolve_cli" in e2e,
                         "no _resolve_cli helper in test_full_e2e.py"))
    checks.append(_check("forced-installed mode supported",
                         "CLI_ANYTHING_FORCE_INSTALLED" in e2e,
                         "no CLI_ANYTHING_FORCE_INSTALLED support"))
    checks.append(_check("no working-directory override in subprocess tests",
                         "cwd=" not in e2e,
                         "subprocess tests must not set cwd"))
    # Execute the suite: the honest "all tests pass" check. Prefer the
    # harness's own venv python (where the harness is installed) so the
    # subprocess tests resolve the real installed command.
    try:
        tests_dir = harness / "cli_anything"
        found = sorted(tests_dir.glob("*/tests")) if tests_dir.is_dir() else []
        tests_arg = str(found[0]) if found else "tests"
        venv_python = harness / ".venv" / "bin" / "python"
        python = str(venv_python) if venv_python.is_file() else sys.executable
        proc = subprocess.run(
            [python, "-m", "pytest", tests_arg, "-q"],
            cwd=str(harness),
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "CLI_ANYTHING_FORCE_INSTALLED": "0"},
        )
        passed = proc.returncode == 0
        tail = (proc.stdout or "")[-500:]
        checks.append(_check("all tests pass", passed,
                             tail if not passed else "suite green"))
    except subprocess.TimeoutExpired:
        checks.append(_check("all tests pass", False, "pytest timed out (300s)"))
    except OSError as exc:
        checks.append(_check("all tests pass", False, f"pytest could not run: {exc}"))
    return checks


def _check_docs(harness: Path, subpkg: Path | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    readme = _read(harness / "README.md")
    if not readme and subpkg is not None:
        readme = _read(subpkg / "README.md")
    checks.append(_check("README documents install + usage",
                         "Install" in readme and "Usage" in readme,
                         "README needs Install and Usage sections"))
    checks.append(_check("README documents tests",
                         "pytest" in readme or "Tests" in readme,
                         "README should document how to run tests"))
    sops = list(harness.parent.glob("*.md")) if harness.name == "agent-harness" else []
    sop_text = "\n".join(_read(p) for p in sops)
    checks.append(_check("SOP documents the analysis",
                         "Analysis Summary" in sop_text or "Command Map" in sop_text,
                         "SOP needs the analysis summary and command map"))
    checks.append(_check("no duplicated methodology document",
                         not (harness / "METHODOLOGY.md").exists(),
                         "METHODOLOGY.md must not be copied into the harness"))
    if subpkg is not None:
        skill = subpkg / "skills" / "SKILL.md"
        checks.append(_check("packaged SKILL.md exists",
                             skill.is_file(),
                             "missing cli_anything/<pkg>/skills/SKILL.md"))
        if skill.is_file():
            text = _read(skill)
            checks.append(_check("SKILL.md has frontmatter + examples",
                                 text.startswith("---") and "## Usage Examples" in text,
                                 "SKILL.md needs frontmatter and examples"))
    return checks


def _check_packaging(harness: Path, subpkg: Path | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    setup = _read(harness / "setup.py")
    checks.append(_check("namespace discovery scoped to cli_anything.*",
                         "find_namespace_packages" in setup and "cli_anything.*" in setup,
                         "setup.py must scope discovery to cli_anything.*"))
    name_match = re.search(r'name\s*=\s*["\'](cli-anything-[^"\']+)["\']', setup)
    checks.append(_check("package named cli-anything-<software>",
                         bool(name_match),
                         "setup.py name must be cli-anything-<software>"))
    checks.append(_check("console-script entry point",
                         "console_scripts" in setup and "cli-anything-" in setup,
                         "no console_scripts entry point in setup.py"))
    checks.append(_check("imports use the cli_anything.<pkg> prefix",
                         bool(subpkg) and "cli_anything." in _read(next(subpkg.glob("*_cli.py"), subpkg / "x")),
                         "CLI module must import from cli_anything.<pkg>"))
    checks.append(_check("click dependency declared",
                         "click" in setup,
                         "setup.py must declare click>=8.0"))
    checks.append(_check("Python >= 3.10 required",
                         ">=3.10" in setup,
                         "setup.py must require python_requires>=3.10"))
    return checks


def _check_code_quality(harness: Path, subpkg: Path | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if subpkg is None:
        return [_check("package present", False, "no sub-package found")]
    py_files = sorted(subpkg.rglob("*.py"))
    syntax_ok = True
    bad_file = ""
    for path in py_files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError:
            syntax_ok = False
            bad_file = str(path)
            break
    checks.append(_check("no syntax errors (all modules compile)",
                         syntax_ok, f"compile failed: {bad_file}" if bad_file else ""))
    bare_handlers = []
    hardcoded_home = []
    for path in py_files:
        text = _read(path)
        if re.search(r"except\s*:", text):
            bare_handlers.append(str(path.relative_to(harness)))
        if "/home/hatch" in text or "/Users/" in text:
            hardcoded_home.append(str(path.relative_to(harness)))
    checks.append(_check("no bare exception handlers",
                         not bare_handlers,
                         f"bare except in: {bare_handlers}" if bare_handlers else ""))
    checks.append(_check("no hardcoded home paths",
                         not hardcoded_home,
                         f"hardcoded paths in: {hardcoded_home}" if hardcoded_home else ""))
    return checks


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def validate(
    harness_path: str | Path, shape: str = "auto"
) -> dict[str, Any]:
    """Validate a harness; returns per-category results + overall verdict."""
    harness = Path(harness_path).resolve()
    if not harness.is_dir():
        raise ValidationError(f"harness path does not exist: {harness}")
    if shape == "auto":
        shape = detect_shape(harness)
    if shape not in {"project", "service"}:
        raise ValidationError(f"unknown shape {shape!r} (want project|service)")

    subpkg = _find_subpkg(harness)
    runners = {
        "directory_structure": lambda: _check_structure(harness, subpkg),
        "required_files": lambda: _check_required_files(harness, subpkg, shape),
        "cli_standards": lambda: _check_cli_standards(subpkg, shape),
        "core_standards": lambda: _check_core_standards(subpkg, shape),
        "test_standards": lambda: _check_test_standards(harness, subpkg),
        "documentation_standards": lambda: _check_docs(harness, subpkg),
        "packaging_standards": lambda: _check_packaging(harness, subpkg),
        "code_quality": lambda: _check_code_quality(harness, subpkg),
    }
    categories: list[dict[str, Any]] = []
    for name in CATEGORIES:
        checks = runners[name]()
        passed = sum(1 for c in checks if c["passed"])
        categories.append({
            "name": name,
            "passed": passed,
            "total": len(checks),
            "ok": passed == len(checks),
            "checks": checks,
        })
    overall = all(c["ok"] for c in categories)
    return {
        "harness": str(harness),
        "shape": shape,
        "overall": "PASS" if overall else "FAIL",
        "categories": categories,
    }


def format_report(result: dict[str, Any]) -> str:
    """Render a validation result as human-readable text."""
    lines = [
        f"Validation: {result['harness']} (shape: {result['shape']})",
        f"Overall: {result['overall']}",
        "",
    ]
    for category in result["categories"]:
        mark = "PASS" if category["ok"] else "FAIL"
        lines.append(f"[{mark}] {category['name']}: "
                     f"{category['passed']}/{category['total']}")
        for check in category["checks"]:
            if not check["passed"]:
                lines.append(f"    ✗ {check['name']}: {check['detail']}")
    return "\n".join(lines)
