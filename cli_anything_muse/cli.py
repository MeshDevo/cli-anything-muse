"""Entry point: `cli-anything-muse` (build / list / refine / test / validate).

Implements the generator skill procedures (spec section 7.1) as a Click
group: no subcommand starts the REPL (spec section 3.1). One consistent
error decorator; venv-only pip enforcement in build (spec section 7.2:
never system pip, PEP 668).
"""

from __future__ import annotations

import functools
import json
import shlex
import sys
from pathlib import Path
from typing import Any

import click

from . import __version__
from .analyzer import AnalysisError
from .generator import GeneratorError, build_harness
from .lifecycle import format_list, list_clis, refine, run_tests
from .repl_skin import ReplSkin
from .validator import ValidationError, format_report, validate


# ---------------------------------------------------------------------------
# One consistent error decorator (spec section 3.4).
# ---------------------------------------------------------------------------


def handle_errors(fn):
    """Convert domain errors into Click errors (clear message, exit 1)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (AnalysisError, GeneratorError, ValidationError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


def _emit_json(ctx: click.Context, payload: Any) -> bool:
    """Echo payload as JSON when --json is set; returns True if emitted."""
    if ctx.obj.get("json"):
        click.echo(json.dumps(payload, indent=2, default=str))
        return True
    return False


def _in_venv() -> bool:
    """True when running inside a virtual environment (PEP 668 rule)."""
    return sys.prefix != sys.base_prefix or bool(__import__("os").environ.get("VIRTUAL_ENV"))


# ---------------------------------------------------------------------------
# Main group: global options + REPL default.
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
@click.version_option(__version__, prog_name="cli-anything-muse")
@click.pass_context
def cli(ctx: click.Context, as_json: bool) -> None:
    """Muse-native CLI-Anything generator: build, list, refine, test, validate."""
    ctx.ensure_object(dict)
    ctx.obj.update({"json": as_json, "repl": False})
    if ctx.invoked_subcommand is None:
        ctx.obj["repl"] = True
        _repl(ctx)


def _repl(ctx: click.Context) -> None:
    """Interactive REPL with the unified skin (default when no command)."""
    skin = ReplSkin("cli-anything-muse", __version__)
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


# ---------------------------------------------------------------------------
# build — Phases 0-7 + 6.5.
# ---------------------------------------------------------------------------


@cli.command("build")
@click.argument("target")
@click.option("--out", "out_dir", type=click.Path(), default=None, help="Output directory (default: ./<software>).")
@click.option("--no-tests", is_flag=True, help="Skip running the generated tests (build is then UNVERIFIED).")
@click.option("--no-install", is_flag=True, help="Skip the editable install + verification.")
@click.option("--muse-install", is_flag=True, help="Also install the generated SKILL.md as a Muse custom skill.")
@handle_errors
@click.pass_context
def cmd_build(ctx: click.Context, target: str, out_dir: str | None,
              no_tests: bool, no_install: bool, muse_install: bool) -> None:
    """Build a harness from a local path or GitHub URL (Phases 0-7)."""
    if not _in_venv():
        raise click.ClickException(
            "refusing to build outside a virtual environment: create one "
            "first (python3 -m venv .venv && source .venv/bin/activate). "
            "System pip is forbidden (PEP 668)."
        )
    report = build_harness(
        target,
        out_dir=out_dir,
        run_tests=not no_tests,
        install=not no_install,
        muse_install=muse_install,
    )
    if _emit_json(ctx, report):
        return
    click.echo(f"Software: {report['software']} (shape: {report['shape']})")
    click.echo(f"Root: {report['root']}")
    click.echo(f"Files written: {len(report['files_written'])}")
    click.echo(f"Tests passed: {report['tests_passed']}")
    click.echo(f"Installed: {report['installed']}")
    click.echo(f"Verified: {report['verified']}")
    for note in report["notes"]:
        click.echo(f"Note: {note}")
    if not report["verified"]:
        raise click.ClickException("build finished but the harness is NOT verified")


# ---------------------------------------------------------------------------
# list — installed + generated CLIs.
# ---------------------------------------------------------------------------


@cli.command("list")
@click.argument("search_path", type=click.Path(), required=False, default=None)
@click.option("--max-depth", type=int, default=3, show_default=True, help="Recursion depth for generated-CLI search.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
@handle_errors
@click.pass_context
def cmd_list(ctx: click.Context, search_path: str | None, max_depth: int, as_json: bool) -> None:
    """List installed and generated CLI-Anything tools."""
    result = list_clis(search_path, max_depth)
    if as_json or ctx.obj.get("json"):
        click.echo(json.dumps(result, indent=2, default=str))
        return
    click.echo(format_list(result))


# ---------------------------------------------------------------------------
# refine — extend an existing harness.
# ---------------------------------------------------------------------------


@cli.command("refine")
@click.argument("harness_path", type=click.Path(exists=True))
@click.option("--source", "source_path", type=click.Path(), default=None, help="Target source path to re-scan.")
@click.option("--focus", default=None, help="Natural-language focus area.")
@click.option("--yes", is_flag=True, help="Implement structural gaps without asking.")
@handle_errors
@click.pass_context
def cmd_refine(ctx: click.Context, harness_path: str, source_path: str | None,
               focus: str | None, yes: bool) -> None:
    """Refine a harness: gap analysis, then implement (with --yes)."""
    proposal = refine(harness_path, source_path=source_path, focus=focus, yes=yes)
    if _emit_json(ctx, proposal):
        return
    click.echo(f"Harness: {proposal['harness']}")
    click.echo(f"Covered functions: {proposal['covered_count']}")
    if proposal["structural_gaps"]:
        click.echo("Structural gaps:")
        for gap in proposal["structural_gaps"]:
            click.echo(f"  - {gap}")
    if proposal["uncovered_apis"]:
        click.echo("Top uncovered APIs (by impact+ease+composability):")
        for gap in proposal["uncovered_apis"][:10]:
            click.echo(f"  - {gap['api']} (score {gap['score']})")
    for item in proposal.get("implemented", []):
        click.echo(f"Implemented: {item}")
    if proposal.get("note"):
        click.echo(f"Note: {proposal['note']}")
    if "tests_passed" in proposal:
        click.echo(f"Tests passed after refine: {proposal['tests_passed']}")


# ---------------------------------------------------------------------------
# test — run a harness's tests, update TEST.md on pass.
# ---------------------------------------------------------------------------


@cli.command("test")
@click.argument("path_or_url")
@handle_errors
@click.pass_context
def cmd_test(ctx: click.Context, path_or_url: str) -> None:
    """Run a harness's tests (pytest -v, installed mode forced)."""
    report = run_tests(path_or_url)
    if _emit_json(ctx, report):
        return
    click.echo(report["output"])
    click.echo(f"Passed: {report['passed']}")
    click.echo(f"Resolver reports installed command: {report['resolver_reports_installed']}")
    if report["test_md_updated"]:
        click.echo(f"TEST.md updated: {report['test_md_updated']}")
    else:
        for line in report.get("failed_lines", []):
            click.echo(line)
        for suggestion in report.get("suggestions", []):
            click.echo(f"Suggestion: {suggestion}")
        raise click.ClickException("tests failed; TEST.md was NOT updated")


# ---------------------------------------------------------------------------
# validate — 8-category audit.
# ---------------------------------------------------------------------------


@cli.command("validate")
@click.argument("harness_path", type=click.Path(exists=True))
@click.option("--shape", type=click.Choice(["auto", "project", "service"]), default="auto", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
@handle_errors
@click.pass_context
def cmd_validate(ctx: click.Context, harness_path: str, shape: str, as_json: bool) -> None:
    """Validate a harness against the methodology (8 categories)."""
    result = validate(harness_path, shape=shape)
    if as_json or ctx.obj.get("json"):
        click.echo(json.dumps(result, indent=2, default=str))
    else:
        click.echo(format_report(result))
    if result["overall"] != "PASS":
        raise click.ClickException("validation FAILED")


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (spec section 8.6: the `main` form)."""
    cli(argv, standalone_mode=True)
