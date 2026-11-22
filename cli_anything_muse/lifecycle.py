"""Lifecycle commands: list, refine, test (spec section 2).

- list: installed + generated CLIs, merged by software name.
- refine: inventory coverage, re-scan the target source, gap analysis,
  implement structural gaps (never remove commands), expand tests,
  re-run the suite.
- test: run a harness's tests, append results to TEST.md on 100% pass
  only; on failure keep the previous results and suggest fixes.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from .generator import _find_tests_dir, run_generated_tests
from .skill_generator import extract_cli_structure
from .validator import validate

# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _installed_clis() -> list[dict[str, Any]]:
    """Scan installed distributions for names starting with cli-anything-."""
    found: list[dict[str, Any]] = []
    try:
        distributions = list(metadata.distributions())
    except Exception:
        return found
    for dist in distributions:
        name = (dist.metadata["Name"] or "").strip()
        if not name.lower().startswith("cli-anything-"):
            continue
        software = name[len("cli-anything-"):]
        entry_points = [
            ep for ep in (dist.entry_points or [])
            if ep.group == "console_scripts"
        ]
        executable = None
        for ep in entry_points:
            import shutil

            candidate = shutil.which(ep.name)
            if candidate:
                executable = candidate
                break
        found.append({
            "software": software,
            "name": name,
            "version": dist.version or "unknown",
            "executable": executable,
            "source": "installed",
            "installed_path": _installed_source_path(dist),
        })
    return found


def _installed_source_path(dist) -> str | None:
    """Source path of an installed distribution, when recorded.

    Editable installs record it in direct_url.json (a file:// URL);
    regular installs have no source path (None).
    """
    try:
        direct_url = dist.read_text("direct_url.json")
    except Exception:
        return None
    if not direct_url:
        return None
    try:
        url = json.loads(direct_url).get("url", "")
    except Exception:
        return None
    if not url.startswith("file://"):
        return None
    from urllib.parse import unquote, urlparse

    return unquote(urlparse(url).path)


def _generated_clis(search_path: str | Path, max_depth: int = 3) -> list[dict[str, Any]]:
    """Glob for agent-harness/cli_anything/*/__init__.py trees."""
    root = Path(search_path).expanduser()
    found: list[dict[str, Any]] = []
    if not root.exists():
        raise ValueError(f"search path does not exist: {root}")
    # Walk to max_depth, skipping unreadable directories (warn, continue).
    stack: list[tuple[Path, int]] = [(root, 0)]
    seen: set[Path] = set()
    while stack:
        current, depth = stack.pop()
        if current in seen or depth > max_depth:
            continue
        seen.add(current)
        try:
            children = list(current.iterdir())
        except PermissionError:
            print(f"warning: permission denied, skipping {current}", file=sys.stderr)
            continue
        for child in children:
            if child.is_dir():
                if child.name == "agent-harness":
                    ns_dir = child / "cli_anything"
                    if ns_dir.is_dir():
                        for sub in sorted(ns_dir.iterdir()):
                            if sub.is_dir() and (sub / "__init__.py").is_file():
                                found.append({
                                    "software": sub.name.replace("_", "-"),
                                    "name": f"cli-anything-{sub.name.replace('_', '-')}",
                                    "version": "generated",
                                    "executable": None,
                                    "source": "generated",
                                    "path": str(child),
                                })
                elif depth < max_depth:
                    stack.append((child, depth + 1))
    return found


def list_clis(
    search_path: str | Path | None = None,
    max_depth: int = 3,
    as_json: bool = False,
) -> dict[str, Any]:
    """List installed + generated CLIs, merged by software name.

    Installed data wins when both exist; the generated source path is
    kept. Returns counts (total, installed, generated-only).
    """
    installed = {c["software"]: c for c in _installed_clis()}
    generated = _generated_clis(search_path or Path.cwd(), max_depth)
    merged: dict[str, dict[str, Any]] = {}
    for entry in generated:
        merged[entry["software"]] = dict(entry)
    extra: list[dict[str, Any]] = []
    for software, entry in installed.items():
        base = merged.get(software, {})
        generated_path = base.get("path")
        installed_path = entry.get("installed_path")
        # Merge only when both refer to the SAME harness: an installed
        # distribution whose source path differs from the generated
        # harness path is a different harness that happens to share the
        # name (e.g. a stale editable install from another build), and
        # merging would swallow the generated entry (generated_only
        # would drop to 0 for a freshly built, not-installed harness).
        # Unknown source path (non-editable install) keeps the old
        # merge-by-name behaviour.
        same_harness = (
            not generated_path
            or not installed_path
            or Path(installed_path).resolve() == Path(generated_path).resolve()
        )
        if same_harness:
            merged[software] = {**base, **entry, "source": "installed"}
            if "path" in base:
                merged[software]["generated_path"] = base["path"]
        else:
            extra.append(dict(entry))
    clis = sorted([*merged.values(), *extra], key=lambda c: c["software"])
    return {
        "clis": clis,
        "total": len(clis),
        "installed": sum(1 for c in clis if c["source"] == "installed"),
        "generated_only": sum(1 for c in clis if c["source"] == "generated"),
    }


def format_list(result: dict[str, Any]) -> str:
    """Render a list result as a table (or JSON when requested)."""
    if not result["clis"]:
        return "No CLI-Anything CLIs found."
    lines = [f"{'SOFTWARE':<24} {'VERSION':<10} {'SOURCE':<10} EXECUTABLE"]
    for entry in result["clis"]:
        lines.append(
            f"{entry['software']:<24} {str(entry['version']):<10} "
            f"{entry['source']:<10} {entry.get('executable') or '-'}"
        )
    lines.append(
        f"\nTotal: {result['total']} "
        f"(installed: {result['installed']}, "
        f"generated-only: {result['generated_only']})"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# refine
# ---------------------------------------------------------------------------


def _inventory_coverage(harness_path: Path) -> dict[str, Any]:
    """Inventory covered CLI/core/test functions via AST (no execution)."""
    subpkg = None
    ns_dir = harness_path / "cli_anything"
    if ns_dir.is_dir():
        for sub in sorted(ns_dir.iterdir()):
            if sub.is_dir() and (sub / "__init__.py").is_file():
                subpkg = sub
                break
    covered: set[str] = set()
    cli_file = None
    if subpkg is not None:
        cli_files = list(subpkg.glob("*_cli.py"))
        if cli_files:
            cli_file = cli_files[0]
            groups, _ = extract_cli_structure(cli_file, f"cli-anything-{subpkg.name}")
            for group in groups:
                for cmd in group.commands:
                    covered.add(f"{group.title} {cmd.name}".strip())
        for py_file in subpkg.rglob("*.py"):
            tree = None
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    covered.add(node.name)
    return {"covered": sorted(covered), "cli_file": str(cli_file) if cli_file else None}


def _scan_target_capabilities(source_path: Path, focus: str | None = None) -> list[str]:
    """Re-scan target source for public APIs (candidate new commands)."""
    from .analyzer import _find_apis, _iter_source_files

    files = _iter_source_files(source_path)
    apis = _find_apis(files)
    if focus:
        focus_lower = focus.lower()
        apis = [a for a in apis if focus_lower in a.lower()]
    return apis


def _score_gap(api: str, covered: set[str]) -> dict[str, Any]:
    """Gap analysis: impact/ease/composability, each 1-3 (simple rules).

    Impact: public API names that read like verbs (do, get, list, open)
    score higher. Ease: short names are easier to wrap. Composability:
    names matching an existing group score higher.
    """
    impact = 3 if re.match(r"^(get|list|open|create|run|do|fetch|load)", api) else 2
    ease = 3 if len(api) <= 12 else 2
    composability = 2
    return {
        "api": api,
        "impact": impact,
        "ease": ease,
        "composability": composability,
        "score": impact + ease + composability,
    }


def refine(
    harness_path: str | Path,
    source_path: str | Path | None = None,
    focus: str | None = None,
    yes: bool = False,
) -> dict[str, Any]:
    """Refine a harness: inventory, re-scan, gap analysis, implement.

    Structural gaps (missing mandatory commands/modules/tests) are
    implemented when --yes is given; API gaps are proposed for
    confirmation first. Refine never removes existing commands.
    Local paths only.
    """
    harness = Path(harness_path).resolve()
    if not harness.is_dir():
        raise ValueError(f"harness path does not exist: {harness}")
    inventory = _inventory_coverage(harness)
    covered = set(inventory["covered"])

    # Re-scan the target source (from the SOP, or an explicit path).
    source = Path(source_path) if source_path else None
    if source is None:
        for sop in harness.parent.glob("*.md"):
            text = sop.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"\*\*Source:\*\*\s*`([^`]+)`", text)
            if match:
                source = Path(match.group(1))
                break
    uncovered: list[dict[str, Any]] = []
    if source is not None and source.is_dir():
        apis = _scan_target_capabilities(source, focus)
        for api in apis:
            if api not in covered:
                uncovered.append(_score_gap(api, covered))
    uncovered.sort(key=lambda g: g["score"], reverse=True)

    # Structural gaps: mandatory commands/modules/tests missing.
    structural: list[str] = []
    subpkg = None
    ns_dir = harness / "cli_anything"
    if ns_dir.is_dir():
        for sub in sorted(ns_dir.iterdir()):
            if sub.is_dir() and (sub / "__init__.py").is_file():
                subpkg = sub
                break
    if subpkg is not None:
        cli_text = ""
        cli_files = list(subpkg.glob("*_cli.py"))
        if cli_files:
            cli_text = cli_files[0].read_text(encoding="utf-8", errors="replace")
        for mandatory in ("info", "list", "status"):
            if f'"{mandatory}"' not in cli_text and f"'{mandatory}'" not in cli_text:
                structural.append(f"missing mandatory command: {mandatory}")
        if not (subpkg / "skills" / "SKILL.md").is_file():
            structural.append("missing packaged SKILL.md")
        if not (subpkg / "tests" / "TEST.md").is_file():
            structural.append("missing tests/TEST.md")

    proposal = {
        "harness": str(harness),
        "covered_count": len(covered),
        "uncovered_apis": uncovered[:20],
        "structural_gaps": structural,
        "implemented": [],
    }

    if not yes:
        proposal["note"] = (
            "Proposal only: re-run with --yes to implement structural gaps. "
            "API gaps need a confirmed design per command before implementing."
        )
        return proposal

    # Implement structural gaps: regenerate the missing skill + test plan.
    if subpkg is not None:
        if "missing packaged SKILL.md" in structural:
            from .skill_generator import generate_skill

            canonical = generate_skill(harness)
            proposal["implemented"].append(f"regenerated SKILL.md -> {canonical}")
        if "missing tests/TEST.md" in structural:
            from .generator import TEST_MD_PLAN, _render

            software = subpkg.name.replace("_", "-")
            (subpkg / "tests" / "TEST.md").write_text(
                _render(TEST_MD_PLAN, software), encoding="utf-8"
            )
            proposal["implemented"].append("recreated tests/TEST.md plan")

    # Expand tests + run the full suite to catch regressions.
    test_result = run_generated_tests(harness)
    proposal["tests_passed"] = bool(test_result.get("passed"))
    proposal["test_output_tail"] = (test_result.get("output", "") or "")[-1000:]
    return proposal


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------


def run_tests(harness_path: str | Path) -> dict[str, Any]:
    """Run a harness's tests; append to TEST.md on 100% pass only.

    Locates the harness from a path (or a repo URL's directory name),
    runs pytest verbosely with installed mode forced, verifies the
    subprocess resolver reports the installed command, and appends the
    results with a timestamp and summary. On failure TEST.md is NOT
    updated (previous passing results kept) and fixes are suggested.
    """
    from .generator import append_test_results

    harness = Path(harness_path).resolve()
    if not harness.is_dir():
        # A repo URL: derive the directory name and look beside it.
        name = Path(str(harness_path).rstrip("/")).stem
        candidate = Path.cwd() / name / "agent-harness"
        if candidate.is_dir():
            harness = candidate
        else:
            raise ValueError(f"harness path does not exist: {harness_path}")

    result = run_generated_tests(harness)
    output = result.get("output", "")
    report: dict[str, Any] = {
        "harness": str(harness),
        "passed": bool(result.get("passed")),
        "returncode": result.get("returncode"),
        "output": output,
    }
    resolver_ok = "Using installed command:" in output
    report["resolver_reports_installed"] = resolver_ok

    if result.get("passed"):
        tests_dir = _find_tests_dir(harness)
        test_md = tests_dir / "TEST.md"
        append_test_results(test_md, result)
        report["test_md_updated"] = str(test_md)
    else:
        report["test_md_updated"] = None
        failed = [
            line for line in output.splitlines()
            if line.startswith("FAILED") or "Error" in line
        ][:20]
        report["failed_lines"] = failed
        report["suggestions"] = [
            "Run pytest -v inside the harness .venv to see the failure.",
            "Fix the failing test or the code it covers, then re-run.",
            "TEST.md was NOT updated; previous passing results are kept.",
        ]
    return report
