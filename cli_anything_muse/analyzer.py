"""Phase 0 (source acquisition) and Phase 1 (codebase analysis).

Implements spec sections 2 (Phase 0, Phase 1) and the gap choice in
spec section 8.1: the analysis records exactly what was read (files,
entry points, APIs) so coverage is auditable in the SOP document.

Clean-room: written from REA_SPEC.md only. No upstream source was read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants: file kinds the analyzer recognizes as source code.
# ---------------------------------------------------------------------------

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".cs", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt",
}

# Directories never walked during analysis (generated, vendored, VCS).
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules",
    "dist", "build", ".tox", ".pytest_cache", "target", ".idea", ".vscode",
}

# Keywords that hint at a backend engine or framework, per language.
BACKEND_HINTS: dict[str, list[str]] = {
    "web-framework": ["flask", "django", "fastapi", "express", "rails", "spring"],
    "desktop-gui": ["tkinter", "pyqt", "pyside", "gtk", "electron", "javafx", "wpf"],
    "media": ["ffmpeg", "opencv", "pillow", "gstreamer"],
    "office": ["openpyxl", "python-docx", "libreoffice", "odf"],
    "database": ["sqlalchemy", "psycopg", "sqlite3", "mongoose", "prisma"],
    "http-service": ["http.server", "BaseHTTPRequestHandler", "aiohttp", "uvicorn"],
}

# File-name patterns that mark an existing CLI tool.
CLI_TOOL_PATTERNS = ("cli", "main", "console", "command")

# Data-model markers: project file formats the software reads/writes.
DATA_MODEL_MARKERS: dict[str, str] = {
    ".xml": "XML project files",
    ".json": "JSON state files",
    ".sqlite": "SQLite database",
    ".db": "SQLite database",
    ".yaml": "YAML configuration",
    ".yml": "YAML configuration",
    ".toml": "TOML configuration",
    ".csv": "CSV data files",
    ".md": "Markdown documents",
}


class AnalysisError(Exception):
    """Raised when a target cannot be acquired or analyzed."""


# ---------------------------------------------------------------------------
# Phase 0 — source acquisition.
# ---------------------------------------------------------------------------


def is_github_url(value: str) -> bool:
    """True when the argument is a GitHub repository URL."""
    return value.startswith("https://github.com/") or value.startswith(
        "git@github.com:"
    )


def software_slug(name: str) -> str:
    """Normalize a directory name into a software slug (lowercase, hyphens)."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise AnalysisError("cannot derive a software name from an empty name")
    return slug


def verify_source(path: str | Path) -> Path:
    """Verify a local path exists and contains source code."""
    path = Path(path).expanduser()
    if not path.exists():
        raise AnalysisError(f"path does not exist: {path}")
    if not path.is_dir():
        raise AnalysisError(f"not a directory: {path}")
    found = any(
        f.is_file() and f.suffix.lower() in SOURCE_EXTENSIONS
        for f in path.rglob("*")
        if not any(part in SKIP_DIRS for part in f.parts)
    )
    if not found:
        raise AnalysisError(f"no source code found under {path}")
    return path


def acquire(target: str, work_dir: str | Path | None = None) -> Path:
    """Return a verified local source tree for a path or a GitHub URL.

    A GitHub URL is cloned first; a local path is verified in place.
    A bare software name (no path, no URL) is rejected: the generator
    cannot work from a name alone because it needs source to analyze.
    """
    if is_github_url(target):
        base = Path(work_dir) if work_dir else Path.cwd() / "acquired"
        base.mkdir(parents=True, exist_ok=True)
        name = Path(target.rstrip("/")).stem
        if name.endswith(".git"):
            name = name[:-4]
        destination = base / name
        if not destination.exists():
            result = subprocess.run(
                ["git", "clone", "--depth", "1", target, str(destination)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise AnalysisError(
                    f"git clone failed for {target}: {result.stderr.strip()}"
                )
        return verify_source(destination)

    path = Path(target).expanduser()
    if not path.exists():
        # A bare name (no slash, no URL) is the documented rejection case.
        raise AnalysisError(
            f"cannot acquire {target!r}: not an existing path and not a "
            "GitHub URL; a bare software name is not accepted"
        )
    return verify_source(path)


# ---------------------------------------------------------------------------
# Phase 1 — codebase analysis.
# ---------------------------------------------------------------------------


def _iter_source_files(root: Path, limit: int = 2000) -> list[Path]:
    """Walk the tree in sorted order, skipping generated/VCS directories."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            candidate = Path(dirpath) / filename
            if candidate.suffix.lower() in SOURCE_EXTENSIONS:
                found.append(candidate)
                if len(found) >= limit:
                    return found
    return found


def _read_text_safe(path: Path, max_bytes: int = 1_000_000) -> str:
    """Read a source file as text, tolerating undecodable bytes."""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[:max_bytes].decode("utf-8", errors="replace")


def _find_entry_points(root: Path, files: list[Path]) -> list[str]:
    """Entry points: __main__ guards, main() definitions, console scripts."""
    entry_points: list[str] = []
    main_guard = re.compile(r"""__name__\s*==\s*["']__main__["']""")
    main_def = re.compile(r"^\s*def\s+main\s*\(", re.MULTILINE)
    for path in files:
        text = _read_text_safe(path)
        rel = str(path.relative_to(root))
        if main_guard.search(text) or main_def.search(text):
            entry_points.append(rel)
    for manifest in ("setup.py", "pyproject.toml", "package.json"):
        candidate = root / manifest
        if candidate.is_file():
            text = _read_text_safe(candidate)
            if "console_scripts" in text or '"bin"' in text or "'bin'" in text:
                entry_points.append(manifest)
    return sorted(set(entry_points))


def _find_apis(files: list[Path]) -> list[str]:
    """Public API surface: top-level function and class definitions."""
    func_def = re.compile(r"^(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
    js_func = re.compile(
        r"(?:^|\n)\s*(?:export\s+)?(?:function\s+([A-Za-z_][A-Za-z0-9_]*)"
        r"|(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\()"
    )
    apis: list[str] = []
    for path in files:
        text = _read_text_safe(path)
        if path.suffix.lower() == ".py":
            for match in func_def.finditer(text):
                name = match.group(1)
                if not name.startswith("_"):
                    apis.append(name)
        elif path.suffix.lower() in {".js", ".ts"}:
            for match in js_func.finditer(text):
                name = match.group(1) or match.group(2)
                if name and not name.startswith("_"):
                    apis.append(name)
    return sorted(set(apis))


def _detect_backend(files: list[Path]) -> dict[str, Any]:
    """Identify the backend engine from framework keywords in the code."""
    hits: dict[str, list[str]] = {}
    for path in files:
        text = _read_text_safe(path).lower()
        for kind, keywords in BACKEND_HINTS.items():
            for keyword in keywords:
                if keyword in text:
                    hits.setdefault(kind, [])
                    if keyword not in hits[kind]:
                        hits[kind].append(keyword)
    # The backend is the kind with the most distinct keyword hits.
    engine = "unknown"
    evidence: list[str] = []
    if hits:
        engine = max(hits, key=lambda kind: len(hits[kind]))
        evidence = sorted(hits[engine])
    return {"engine": engine, "evidence": evidence, "all_hits": hits}


def _detect_data_model(root: Path, files: list[Path]) -> dict[str, Any]:
    """Identify the data model from project file formats and markers."""
    formats: dict[str, int] = {}
    for path in files:
        marker = DATA_MODEL_MARKERS.get(path.suffix.lower())
        if marker:
            formats[marker] = formats.get(marker, 0) + 1
    # A project file is a native format the software itself reads/writes.
    project_markers = ("XML project files", "JSON state files", "SQLite database")
    has_project_file = any(fmt in formats for fmt in project_markers)
    return {"formats": formats, "has_project_file": has_project_file}


def _detect_cli_tools(root: Path, files: list[Path]) -> list[str]:
    """Find existing CLI tools shipped with the target (building blocks)."""
    tools: list[str] = []
    for path in files:
        stem = path.stem.lower()
        if any(token in stem for token in CLI_TOOL_PATTERNS):
            tools.append(str(path.relative_to(root)))
        elif path.name == "__main__.py":
            tools.append(str(path.relative_to(root)))
    return sorted(set(tools))


def _detect_undo_system(files: list[Path]) -> dict[str, Any]:
    """Catalog the command/undo system, if the target has one."""
    undo_hits = 0
    redo_hits = 0
    command_files: list[str] = []
    for path in files:
        text = _read_text_safe(path).lower()
        undo = len(re.findall(r"\bundo\b", text))
        redo = len(re.findall(r"\bredo\b", text))
        if undo or redo:
            undo_hits += undo
            redo_hits += redo
            command_files.append(str(path))
    return {
        "has_undo": undo_hits > 0,
        "undo_mentions": undo_hits,
        "redo_mentions": redo_hits,
        "files": sorted(set(command_files))[:20],
    }


def _detect_shape(
    backend: dict[str, Any],
    data_model: dict[str, Any],
    files: list[Path],
) -> str:
    """Detect the harness shape: 'service' or 'project' (spec section 3.1).

    A service has an HTTP surface (server framework, OpenAPI spec, or
    client/server keywords) and no native project file; everything else
    is a stateful project CLI.
    """
    engine = backend["engine"]
    if engine in {"http-service", "web-framework"}:
        return "service"
    names = " ".join(p.name.lower() for p in files)
    if "openapi" in names or "swagger" in names:
        return "service"
    if data_model["has_project_file"]:
        return "project"
    return "project"


def _detect_groups(analysis: dict[str, Any]) -> list[str]:
    """Command groups for the generated CLI, from the analysis."""
    groups = ["project", "info"]
    if analysis["shape"] == "service":
        groups = ["server", "info"]
    if analysis["undo_system"]["has_undo"]:
        groups.append("session")
    groups.append("export")
    return groups


def analyse(target_path: str | Path) -> dict[str, Any]:
    """Run Phase 1 on a verified local source tree.

    Returns a dict with the backend engine, data model, CLI tools, undo
    system, shape, command groups, and the audit lists (files_read,
    entry_points, apis) required by spec section 8.1.
    """
    root = Path(target_path).expanduser()
    if not root.is_dir():
        raise AnalysisError(f"not a directory: {root}")
    files = _iter_source_files(root)
    if not files:
        raise AnalysisError(f"no source code found under {root}")

    backend = _detect_backend(files)
    data_model = _detect_data_model(root, files)
    cli_tools = _detect_cli_tools(root, files)
    undo_system = _detect_undo_system(files)
    shape = _detect_shape(backend, data_model, files)

    analysis: dict[str, Any] = {
        "software": software_slug(root.name),
        "source_path": str(root.resolve()),
        "shape": shape,
        "backend": backend,
        "data_model": data_model,
        "cli_tools": cli_tools,
        "undo_system": undo_system,
        "files_read": [str(p.relative_to(root)) for p in files],
        "entry_points": _find_entry_points(root, files),
        "apis": _find_apis(files),
        "file_count": len(files),
    }
    analysis["groups"] = _detect_groups(analysis)
    return analysis


def write_sop(analysis: dict[str, Any], out_path: str | Path) -> Path:
    """Write the software-specific SOP document (<SOFTWARE>.md).

    Records the architecture analysis, the command map, the rendering-gap
    assessment, the inspected-file audit list, and the Alfred notes
    register (open questions, never promoted to facts before verification).
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    backend = analysis["backend"]
    data_model = analysis["data_model"]
    lines = [
        f"# {analysis['software'].replace('-', ' ').title()} — Architecture & SOP",
        "",
        "Generated by CLI-Anything (Muse-native, clean-room) from the",
        "codebase analysis in Phase 1. Coverage is auditable: the",
        "inspected-file list below is exactly what the analyzer read.",
        "",
        "## Analysis Summary",
        "",
        f"- **Software:** `{analysis['software']}`",
        f"- **Source:** `{analysis['source_path']}`",
        f"- **Shape:** `{analysis['shape']}` (project = stateful CLI, "
        "service = stateless API client)",
        f"- **Backend engine:** `{backend['engine']}` "
        f"(evidence: {', '.join(backend['evidence']) or 'none found'})",
        f"- **Data model:** {', '.join(data_model['formats']) or 'none detected'}",
        f"- **Existing CLI tools:** {', '.join(analysis['cli_tools']) or 'none'}",
        f"- **Undo system:** "
        f"{'present' if analysis['undo_system']['has_undo'] else 'not found'} "
        f"({analysis['undo_system']['undo_mentions']} undo mentions, "
        f"{analysis['undo_system']['redo_mentions']} redo mentions)",
        f"- **Files read:** {analysis['file_count']}",
        f"- **Entry points:** {', '.join(analysis['entry_points']) or 'none'}",
        "",
        "## Command Map",
        "",
        "Command groups for the generated CLI:",
        "",
    ]
    for group in analysis["groups"]:
        lines.append(f"- `{group}`")
    lines += [
        "",
        "Mandatory probe commands exist on every harness: `info`, `list`,",
        "`status` — so an agent can inspect state before modifying it.",
        "",
        "## Rendering-Gap Assessment",
        "",
        "The CLI drives the real software through its real backend and",
        "never reimplements rendering. Effects applied at render time by",
        "the application's engine are handled in this priority order:",
        "native renderer first, filter-translation layer second, generated",
        "render script last. Any effect without a render mapping is",
        "documented as \"project-only (not rendered)\".",
        "",
        "## Inspected Files (audit list)",
        "",
    ]
    for rel in analysis["files_read"]:
        lines.append(f"- `{rel}`")
    lines += [
        "",
        "## Public APIs Found",
        "",
    ]
    for api in analysis["apis"][:200]:
        lines.append(f"- `{api}`")
    if len(analysis["apis"]) > 200:
        lines.append(f"- ... and {len(analysis['apis']) - 200} more")
    lines += [
        "",
        "## Alfred Notes Register",
        "",
        "Open questions, ambiguities, and assumptions to test. A note is",
        "never promoted to a confirmed fact before verification.",
        "",
        "| Note | Status |",
        "|------|--------|",
        "| Backend engine detection is keyword-based; confirm against the real entry point. | needs verification |",
        "| Shape detection is heuristic; confirm the harness shape fits the target. | needs verification |",
        "",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def analyse_target(target: str, work_dir: str | Path | None = None) -> dict[str, Any]:
    """Phase 0 + Phase 1 in one call: acquire, then analyze."""
    return analyse(acquire(target, work_dir=work_dir))
