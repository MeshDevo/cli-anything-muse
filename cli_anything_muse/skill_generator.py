"""AST-based SKILL.md generator for CLI-Anything harnesses."""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CommandMeta:
    name: str
    full_command: str
    description: str


@dataclass
class GroupMeta:
    func_name: str
    declared_name: str
    title: str
    description: str
    parent_func: str | None = None
    commands: list[CommandMeta] = field(default_factory=list)


def _locate_package(harness_path: Path) -> tuple[str, Path, Path]:
    """Return (software_slug, cli_anything_dir, subpkg_dir)."""
    if not harness_path.exists():
        raise FileNotFoundError(f"Harness path does not exist: {harness_path}")
    ns_dir = harness_path / "cli_anything"
    if not ns_dir.is_dir():
        raise FileNotFoundError(f"Missing cli_anything/ directory under {harness_path}")

    subpkgs = [
        p for p in sorted(ns_dir.iterdir())
        if p.is_dir() and (p / "__init__.py").is_file()
    ]
    if not subpkgs:
        raise ValueError(f"No CLI sub-package with __init__.py found under {ns_dir}")

    subpkg = subpkgs[0]
    # The sub-package directory is the ground truth for the software name:
    # it is generated from the software slug. The parent directory name is
    # NOT reliable (it is whatever output directory the user chose, e.g.
    # "out" or "go-bot-trial").
    software_slug = subpkg.name.strip().lower().replace("_", "-")
    return software_slug, ns_dir, subpkg


def _find_readme(harness_path: Path, subpkg: Path) -> Path | None:
    for candidate in (subpkg / "README.md", harness_path / "README.md"):
        if candidate.is_file():
            return candidate
    return None


def _parse_readme(readme_path: Path | None) -> tuple[str, str | None]:
    if readme_path is None or not readme_path.is_file():
        return "", None
    text = readme_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    intro_lines: list[str] = []
    seen_h1 = False
    collecting = False
    for line in lines:
        stripped = line.strip()
        if not seen_h1:
            if stripped.startswith("# "):
                seen_h1 = True
            continue
        if stripped.startswith("#"):
            break
        if not stripped:
            if collecting and intro_lines:
                break
            continue
        collecting = True
        intro_lines.append(stripped)
    intro = " ".join(intro_lines).strip()

    sys_cmd: str | None = None
    m = re.search(r"`((?:sudo\s+)?(?:apt-get|apt|brew)\s+install[^`]+)`", text)
    if m:
        sys_cmd = m.group(1).strip()
    return intro, sys_cmd


def _parse_version(harness_path: Path) -> str:
    setup_py = harness_path / "setup.py"
    if not setup_py.is_file():
        return "1.0.0"
    text = setup_py.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"version\s*=\s*['\"]([^'\"]+)['\"]", text)
    return m.group(1).strip() if m else "1.0.0"


def _extract_decorator_call(dec: ast.expr) -> tuple[str | None, str | None, str | None]:
    """Return (owner_name, kind: 'group'|'command'|None, declared_name)."""
    if isinstance(dec, ast.Call):
        func = dec.func
        args = dec.args
        keywords = dec.keywords
    else:
        func = dec
        args = []
        keywords = []

    if isinstance(func, ast.Attribute) and func.attr in {"group", "command"}:
        owner = func.value.id if isinstance(func.value, ast.Name) else None
        kind = func.attr
        declared: str | None = None
        if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            declared = args[0].value
        for kw in keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                declared = kw.value.value
        return owner, kind, declared
    return None, None, None


def _collect_functions_in_order(node: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Walk AST body statements in source order without skipping nested defs."""
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(child)
        found.extend(_collect_functions_in_order(child))
    return found


def extract_cli_structure(cli_file: Path, skill_name: str) -> tuple[list[GroupMeta], bool]:
    """Parse Click groups and commands via AST without executing the CLI module."""
    source = cli_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(cli_file))
    funcs = _collect_functions_in_order(tree)

    groups_by_func: dict[str, GroupMeta] = {}
    root_funcs: set[str] = set()
    loose_commands: list[tuple[str | None, CommandMeta]] = []
    has_preview = False

    for fn in funcs:
        doc = (ast.get_docstring(fn) or "").strip().splitlines()[0].strip() if ast.get_docstring(fn) else ""
        default_name = fn.name.replace("_", "-")
        for dec in fn.decorator_list:
            owner, kind, declared = _extract_decorator_call(dec)
            if kind == "group":
                dname = declared or default_name
                if owner == "click":
                    root_funcs.add(fn.name)
                    groups_by_func[fn.name] = GroupMeta(
                        func_name=fn.name,
                        declared_name=dname,
                        title=dname,
                        description=doc or f"Commands for {dname} operations.",
                        parent_func=None,
                    )
                else:
                    parent = owner if owner in groups_by_func else None
                    groups_by_func[fn.name] = GroupMeta(
                        func_name=fn.name,
                        declared_name=dname,
                        title=dname,
                        description=doc or f"Commands for {dname} operations.",
                        parent_func=parent,
                    )
                if dname == "preview":
                    has_preview = True
            elif kind == "command":
                cname = declared or default_name
                desc = doc or f"Execute {cname} operation."
                loose_commands.append((owner, CommandMeta(name=cname, full_command=cname, description=desc)))

    def group_path(fn_name: str) -> str:
        parts: list[str] = []
        cur: str | None = fn_name
        seen: set[str] = set()
        while cur and cur in groups_by_func and cur not in seen:
            seen.add(cur)
            g = groups_by_func[cur]
            if cur not in root_funcs:
                parts.append(g.declared_name)
            elif len(groups_by_func) == 1:
                parts.append(g.declared_name)
            cur = g.parent_func
        parts.reverse()
        return " ".join(parts) if parts else "General"

    ordered_groups: list[GroupMeta] = []
    for fn_name, g in groups_by_func.items():
        g.title = group_path(fn_name)
        if fn_name not in root_funcs or len(groups_by_func) == 1:
            ordered_groups.append(g)

    general_cmds: list[CommandMeta] = []
    for owner, cmd in loose_commands:
        if owner and owner in groups_by_func and owner not in root_funcs:
            parent_group = groups_by_func[owner]
            cmd.full_command = f"{skill_name} {parent_group.title} {cmd.name}".strip()
            parent_group.commands.append(cmd)
        else:
            cmd.full_command = f"{skill_name} {cmd.name}".strip()
            general_cmds.append(cmd)

    if general_cmds:
        general = GroupMeta(
            func_name="__general__",
            declared_name="general",
            title="General",
            description="Top-level commands.",
            commands=general_cmds,
        )
        ordered_groups.insert(0, general)

    if not ordered_groups:
        ordered_groups.append(
            GroupMeta(
                func_name="__general__",
                declared_name="general",
                title="General",
                description="Top-level commands.",
            )
        )

    final_groups = [g for g in ordered_groups if g.commands] or ordered_groups
    return final_groups, has_preview


def _find_cli_file(subpkg: Path) -> Path:
    candidates = sorted(subpkg.glob("*_cli.py"))
    if candidates:
        return candidates[0]
    for p in sorted(subpkg.glob("*.py")):
        if p.name not in {"__init__.py", "__main__.py"}:
            return p
    raise FileNotFoundError(f"No CLI entry module found in {subpkg}")


def _build_examples(skill_name: str, groups: list[GroupMeta]) -> list[dict[str, str]]:
    group_names = {g.declared_name.lower() for g in groups}
    examples = [
        {
            "title": "One-shot JSON invocation",
            "code": f"{skill_name} --json status",
        },
        {
            "title": "Interactive REPL session",
            "code": f"{skill_name}\n# Inside REPL: help, status, undo, redo, quit",
        },
    ]
    if "project" in group_names:
        examples.insert(
            0,
            {
                "title": "Create a new project (human & JSON)",
                "code": (
                    f"{skill_name} project new --output /tmp/demo.proj\n"
                    f"{skill_name} --json --project /tmp/demo.proj project info"
                ),
            },
        )
    if "export" in group_names:
        examples.append(
            {
                "title": "Export via the real backend",
                "code": f"{skill_name} --json --project /tmp/demo.proj export render --output /tmp/out.bin",
            }
        )
    return examples


def _simple_render(ctx: dict[str, Any]) -> str:
    lines = [
        "---",
        f'name: "{ctx["skill_name"]}"',
        f'description: "{ctx["description"]}"',
        "---",
        "",
        f"# {ctx['skill_name']}",
        "",
        ctx["intro"] or f"Command-line interface for {ctx['display_name']}.",
        "",
        "## Prerequisites",
        "",
        "- **Python:** >= 3.10 (install inside a virtual environment; never use system `pip` under PEP 668)",
        f"- **Real software dependency:** `{ctx['display_name']}` must be installed on the host.",
    ]
    if ctx["system_install_cmd"]:
        lines.append(f"- **System install command:** `{ctx['system_install_cmd']}`")
    lines.extend(
        [
            "",
            "## Installation (Venv)",
            "",
            "```bash",
            "python3 -m venv .venv",
            "source .venv/bin/activate",
            "pip install -e .",
            "```",
            "",
            "## Command Groups",
            "",
        ]
    )
    for grp in ctx["groups"]:
        lines.extend(
            [
                f"### {grp.title}",
                "",
                "| Command | Description |",
                "|---------|-------------|",
            ]
        )
        for cmd in grp.commands:
            lines.append(f"| `{cmd.full_command}` | {cmd.description} |")
        lines.append("")

    lines.extend(
        [
            "## State Management & Output Contract",
            "",
            f"- **Session state:** {ctx['session_notes']}",
            "- **Dual output:** Human-readable by default; pass `--json` before the subcommand for machine-readable JSON on `stdout`.",
            "- **Errors & exit codes:** Exit `0` on success, non-zero on failure with diagnostics on `stderr`.",
            "",
        ]
    )
    if ctx["has_preview"]:
        lines.extend(
            [
                "## Preview Support (`preview-bundle/v1`)",
                "",
                f"- **Producer (`{ctx['skill_name']} preview ...`):** `recipes`, `capture`, `latest`, `diff`, `live start|push|status|stop`.",
                "- **Consumer (`cli-hub previews ...`):** Read-only `inspect`, `html`, `watch`, `open`.",
                "",
                "```bash",
                f"{ctx['skill_name']} --json preview capture --recipe default",
                "cli-hub previews inspect <bundle_dir>",
                "```",
                "",
            ]
        )
    lines.extend(["## Usage Examples", ""])
    for ex in ctx["examples"]:
        lines.extend([f"### {ex['title']}", "", "```bash", ex["code"], "```", ""])

    lines.extend(
        [
            "## For Muse / AI Agents",
            "",
            "1. Always pass `--json` before the subcommand so output is valid JSON on `stdout`.",
            "2. Check exit codes (`0` success, non-zero failure) and parse `stderr` on error.",
            "3. Use absolute paths for file operations and verify exported files exist.",
            "",
            "## Version",
            "",
            f"`{ctx['version']}`",
            "",
        ]
    )
    return "\n".join(lines)


def generate_skill(
    harness_path: str | Path,
    output_path: str | Path | None = None,
    template_path: str | Path | None = None,
    install_muse_skill: bool = False,
) -> Path:
    """Extract metadata via AST and write canonical + packaged SKILL.md copies."""
    hpath = Path(harness_path).resolve()
    software_slug, _, subpkg = _locate_package(hpath)
    skill_name = f"cli-anything-{software_slug}"
    display_name = software_slug.replace("-", " ").title()

    readme = _find_readme(hpath, subpkg)
    intro, system_install_cmd = _parse_readme(readme)
    version = _parse_version(hpath)
    cli_file = _find_cli_file(subpkg)
    groups, has_preview = extract_cli_structure(cli_file, skill_name)
    examples = _build_examples(skill_name, groups)

    if intro:
        trimmed = intro[:100].rstrip() + ("..." if len(intro) > 100 else "")
        description = f"Command-line interface for {display_name} - {trimmed}"
    else:
        description = f"Command-line interface for {display_name}"
    description = description.replace('"', "'")

    has_project_group = any(g.declared_name.lower() == "project" for g in groups)
    session_notes = (
        "JSON project/session persistence with exclusive file locking, up to 50 levels of undo/redo history, and auto-save unless --dry-run."
        if has_project_group
        else "Environment-backed connection session (host, port, scheme, token) overridable per invocation via CLI flags."
    )

    context = {
        "skill_name": skill_name,
        "display_name": display_name,
        "description": description,
        "intro": intro,
        "system_install_cmd": system_install_cmd,
        "version": version,
        "groups": groups,
        "examples": examples,
        "has_preview": has_preview,
        "session_notes": session_notes,
    }

    default_tpl = Path(__file__).resolve().parent / "templates" / "SKILL.md.template"
    tpl_file = Path(template_path) if template_path else default_tpl

    rendered: str | None = None
    if tpl_file.is_file():
        try:
            from jinja2 import Environment, FileSystemLoader

            env = Environment(loader=FileSystemLoader(str(tpl_file.parent)), autoescape=False)
            template = env.get_template(tpl_file.name)
            rendered = template.render(**context)
        except ImportError:
            rendered = None

    if rendered is None:
        rendered = _simple_render(context)

    repo_root = hpath.parent if hpath.name == "agent-harness" else hpath
    canonical = (
        Path(output_path).resolve()
        if output_path
        else repo_root / "skills" / skill_name / "SKILL.md"
    )
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(rendered.rstrip() + "\n", encoding="utf-8")

    packaged = subpkg / "skills" / "SKILL.md"
    packaged.parent.mkdir(parents=True, exist_ok=True)
    packaged.write_text(rendered.rstrip() + "\n", encoding="utf-8")

    if install_muse_skill:
        muse_dest = Path.home() / "workspace" / "custom-skills" / skill_name / "SKILL.md"
        muse_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(canonical, muse_dest)

    return canonical


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate SKILL.md from a CLI-Anything harness.")
    parser.add_argument("harness_path", help="Path to agent-harness/ directory.")
    parser.add_argument("-o", "--output", default=None, help="Canonical SKILL.md output path.")
    parser.add_argument("-t", "--template", default=None, help="Jinja2 template override path.")
    parser.add_argument("--muse-install", action="store_true", help="Also copy to ~/workspace/custom-skills/.")
    args = parser.parse_args(argv)
    out = generate_skill(
        args.harness_path,
        output_path=args.output,
        template_path=args.template,
        install_muse_skill=args.muse_install,
    )
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())