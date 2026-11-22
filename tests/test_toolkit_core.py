"""Four-layer test suite for the cli-anything-muse toolkit itself.

Layers (spec section 6):
1. Unit tests — synthetic data only, one module at a time.
2. E2E tests — build a trial harness on a small synthetic target,
   install it in a venv, drive --help / --json / workflows.
3. CLI subprocess tests — both console scripts via a _resolve_cli helper
   (installed command preferred, `python -m` fallback with a printed
   note, CLI_ANYTHING_FORCE_INSTALLED=1 raises instead).
4. True-backend tests — a fake executable on PATH satisfies the
   backend contract; with the executable absent the command FAILS
   (never skips, never fakes a success).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cli_anything_muse import __version__  # noqa: E402
from cli_anything_muse import analyzer, generator, lifecycle, validator  # noqa: E402
from cli_anything_muse import preview_bundle, repl_skin, skill_generator  # noqa: E402
from cli_anything_muse import cli_hub  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------


def _resolve_cli(command: str, module: str) -> list[str]:
    """Prefer the installed command; fall back to `python -m` with a note.

    CLI_ANYTHING_FORCE_INSTALLED=1 raises instead of falling back.
    """
    installed = shutil.which(command)
    if installed:
        print(f"[_resolve_cli] Using installed command: {installed}")
        return [installed]
    if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED") == "1":
        raise AssertionError(
            f"installed command {command!r} not found on PATH and "
            "CLI_ANYTHING_FORCE_INSTALLED=1"
        )
    print(f"[_resolve_cli] Falling back to module: {module}")
    return [sys.executable, "-m", module]


def _run_toolkit_cli(command: str, module: str, *args, check: bool = True, env=None):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    proc = subprocess.run(
        [*_resolve_cli(command, module), *args],
        capture_output=True, text=True, env=merged,
    )
    if check:
        assert proc.returncode == 0, (
            f"{command} {args} failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


@pytest.fixture(scope="session", autouse=True)
def _venv_bin_on_path():
    """Ensure the venv bin dir (holding the installed console scripts)
    is on PATH for _resolve_cli."""
    bin_dir = str(Path(sys.executable).resolve().parent)
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    yield


def _make_target(tmp_path: Path, name: str, kind: str = "project") -> Path:
    """Create a small synthetic target software tree.

    project: a file-based tool with a convert() + version() API and a
    JSON state file (so shape detection returns "project").
    service: an HTTP service (fastapi keyword + openapi.yaml) so shape
    detection returns "service".
    """
    root = tmp_path / name
    (root / name.replace("-", "_")).mkdir(parents=True)
    pkg = root / name.replace("-", "_")
    if kind == "project":
        (pkg / "__init__.py").write_text('"""Demo tool package."""\n', encoding="utf-8")
        (pkg / "convert.py").write_text(
            '"""Convert files through the demo tool."""\n\n\n'
            "def convert(input_path, output_path):\n"
            '    """Convert one file to another format."""\n'
            "    return {\"input\": input_path, \"output\": output_path}\n\n\n"
            "def version():\n"
            '    """Return the tool version."""\n'
            '    return "1.2.3"\n\n\n'
            "def undo_last():\n"
            '    """Undo the last operation."""\n'
            "    return True\n\n\n"
            "def redo_last():\n"
            '    """Redo the last undone operation."""\n'
            "    return True\n\n\n"
            'if __name__ == "__main__":\n'
            '    print(version())\n',
            encoding="utf-8",
        )
        (root / "sample.json").write_text('{"name": "demo"}\n', encoding="utf-8")
        (root / "README.md").write_text(
            "# Demo Tool\n\nA small file converter for tests.\n", encoding="utf-8"
        )
    else:
        (pkg / "__init__.py").write_text('"""Demo service package."""\n', encoding="utf-8")
        (pkg / "server.py").write_text(
            '"""Demo service: a fastapi app serving the tool over HTTP."""\n\n\n'
            "def create_app():\n"
            '    """Build the fastapi application."""\n'
            "    return {\"app\": \"fastapi\"}\n\n\n"
            "def list_items():\n"
            '    """List items from the service."""\n'
            "    return []\n",
            encoding="utf-8",
        )
        (root / "openapi.yaml").write_text(
            "openapi: 3.0.0\ninfo:\n  title: Demo Service\n  version: 1.0.0\n"
            "paths: {}\n",
            encoding="utf-8",
        )
        (root / "README.md").write_text(
            "# Demo Service\n\nA small HTTP service for tests.\n", encoding="utf-8"
        )
    return root


def _make_fake_executable(bin_dir: Path, name: str) -> Path:
    """A fake real-software executable: answers --version and writes the
    --output file when called as `exe --input X --output Y`."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / name
    exe.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "demo-tool 1.2.3"; exit 0; fi\n'
        'out=""\n'
        'while [ $# -gt 0 ]; do case "$1" in --output) out="$2"; shift 2;; *) shift;; esac; done\n'
        'if [ -n "$out" ]; then printf "converted by demo-tool\\n" > "$out"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    exe.chmod(0o755)
    return exe


# ---------------------------------------------------------------------------
# Layer 1 — analyzer unit tests (synthetic data only).
# ---------------------------------------------------------------------------


class TestAnalyzer:
    def test_acquire_local_path(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        acquired = analyzer.acquire(str(target))
        assert acquired.is_dir()
        assert acquired.name == "demo-tool"

    def test_acquire_rejects_bare_name(self, tmp_path):
        with pytest.raises(analyzer.AnalysisError, match="bare software name"):
            analyzer.acquire("demo-tool")

    def test_acquire_rejects_nonexistent_path(self, tmp_path):
        with pytest.raises(analyzer.AnalysisError):
            analyzer.acquire(str(tmp_path / "does-not-exist"))

    def test_is_github_url_recognizes_github_urls(self):
        assert analyzer.is_github_url("https://github.com/x/y") is True
        assert analyzer.is_github_url("git@github.com:x/y") is True
        assert analyzer.is_github_url("demo-tool") is False
        assert analyzer.is_github_url("/tmp/demo-tool") is False
        assert analyzer.is_github_url("file:///tmp/demo-tool") is False

    def test_acquire_github_url_clone_failure_raises(self, tmp_path):
        # A GitHub URL for a repo that does not exist: git clone fails
        # (404 or no network) and acquire raises a clear AnalysisError.
        with pytest.raises(analyzer.AnalysisError, match="git clone failed"):
            analyzer.acquire(
                "https://github.com/definitely-not-a-real-user-xyz/definitely-not-a-real-repo-xyz",
                work_dir=tmp_path / "work",
            )

    def test_analyse_project_shape(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        analysis = analyzer.analyse(target)
        assert analysis["software"] == "demo-tool"
        assert analysis["shape"] == "project"
        assert analysis["files_read"], "files_read must be non-empty"
        assert analysis["entry_points"], "entry_points must be non-empty"
        assert analysis["apis"], "apis must be non-empty"
        assert "convert" in " ".join(analysis["apis"])
        assert analysis["undo_system"]["has_undo"] is True

    def test_analyse_service_shape(self, tmp_path):
        target = _make_target(tmp_path, "demo-service", kind="service")
        analysis = analyzer.analyse(target)
        assert analysis["shape"] == "service"
        assert analysis["backend"]["engine"] in {"http-service", "web-framework"}

    def test_analyse_empty_directory_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(analyzer.AnalysisError):
            analyzer.analyse(empty)

    def test_analyse_target_round_trip(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        analysis = analyzer.analyse_target(str(target))
        assert analysis["software"] == "demo-tool"
        assert analysis["shape"] == "project"

    def test_write_sop_contains_audit_lists(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        analysis = analyzer.analyse(target)
        sop = analyzer.write_sop(analysis, tmp_path / "DEMO-TOOL.md")
        text = sop.read_text(encoding="utf-8")
        assert "demo-tool" in text
        assert "Alfred" in text or "alfred" in text.lower()
        for item in analysis["files_read"][:3]:
            assert item in text

    def test_software_slug(self):
        assert analyzer.software_slug("Demo Tool") == "demo-tool"
        assert analyzer.software_slug("demo_tool") == "demo-tool"


# ---------------------------------------------------------------------------
# Layer 1 — generator unit tests (synthetic data only).
# ---------------------------------------------------------------------------


class TestGenerator:
    def test_render_replaces_tokens(self):
        out = generator._render(
            "cli-anything-__SOFTWARE__ (__DISPLAY__) v__VERSION__ pkg __PKG__",
            "demo-tool",
        )
        assert out == "cli-anything-demo-tool (Demo Tool) v1.0.0 pkg demo_tool"

    def test_design_project_shape(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        design_doc = generator.design(analyzer.analyse(target))
        assert design_doc["shape"] == "project"
        assert "project" in design_doc["groups"]

    def test_design_service_shape(self, tmp_path):
        target = _make_target(tmp_path, "demo-service", kind="service")
        design_doc = generator.design(analyzer.analyse(target))
        assert design_doc["shape"] == "service"
        assert "server" in design_doc["groups"]

    def test_build_harness_project_layout(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        assert report["software"] == "demo-tool"
        assert report["shape"] == "project"
        assert report["verified"] is False  # tests skipped => unverified
        harness = Path(report["harness"])
        assert (harness / "setup.py").is_file()
        subpkg = harness / "cli_anything" / "demo_tool"
        assert (subpkg / "README.md").is_file()
        assert (subpkg / "__init__.py").is_file()
        assert (subpkg / "demo_tool_cli.py").is_file()
        assert (subpkg / "core" / "project.py").is_file()
        assert (subpkg / "core" / "session.py").is_file()
        assert (subpkg / "core" / "export.py").is_file()
        assert (subpkg / "utils" / "demo_tool_backend.py").is_file()
        assert (subpkg / "tests" / "test_core.py").is_file()
        assert (subpkg / "tests" / "test_full_e2e.py").is_file()
        assert (subpkg / "tests" / "TEST.md").is_file()
        assert (tmp_path / "out" / "DEMO-TOOL.md").is_file()  # SOP doc
        # Generated CLI parses as valid Python.
        import ast
        ast.parse((subpkg / "demo_tool_cli.py").read_text(encoding="utf-8"))

    def test_build_harness_service_layout(self, tmp_path):
        target = _make_target(tmp_path, "demo-service", kind="service")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        assert report["shape"] == "service"
        harness = Path(report["harness"])
        subpkg = harness / "cli_anything" / "demo_service"
        assert (subpkg / "core" / "client.py").is_file()
        assert (subpkg / "demo_service_cli.py").is_file()
        assert not (subpkg / "core" / "project.py").is_file()
        assert not (subpkg / "core" / "export.py").is_file()

    def test_build_harness_skill_unverified_without_tests(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        assert any("not verified" in note.lower() or "unverified" in note.lower() for note in report["notes"])

    def test_generate_skill_for_harness(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        skill = generator.generate_skill_for_harness(Path(report["harness"]))
        assert skill.is_file()
        text = skill.read_text(encoding="utf-8")
        assert "demo-tool" in text.lower()

    def test_append_test_results(self, tmp_path):
        test_md = tmp_path / "TEST.md"
        test_md.write_text("## 1. Test inventory plan\n", encoding="utf-8")
        generator.append_test_results(test_md, {
            "passed": True, "total": 5, "failed": 0, "output": "5 passed",
        })
        text = test_md.read_text(encoding="utf-8")
        assert "5 passed" in text or "passed" in text.lower()

    def test_run_generated_tests_creates_venv_and_passes(self, tmp_path, monkeypatch):
        # Full Phase 6 on the synthetic target with the fake executable on
        # PATH (true-backend test needs it).
        bin_dir = tmp_path / "bin"
        _make_fake_executable(bin_dir, "demo-tool")
        monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        harness = Path(report["harness"])
        result = generator.run_generated_tests(harness)
        assert result["passed"] is True, result.get("output", "")[-2000:]
        assert result["total"] >= 10


# ---------------------------------------------------------------------------
# Layer 1 — validator unit tests.
# ---------------------------------------------------------------------------


class TestValidator:
    def test_detect_shape_project_and_service(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        generator.build_harness(str(target), out_dir=tmp_path / "out",
                                run_tests=False, install=False)
        assert validator.detect_shape(tmp_path / "out" / "agent-harness") == "project"
        target_svc = _make_target(tmp_path, "demo-service", kind="service")
        generator.build_harness(str(target_svc), out_dir=tmp_path / "out-svc",
                                run_tests=False, install=False)
        assert validator.detect_shape(tmp_path / "out-svc" / "agent-harness") == "service"

    def test_validate_passes_on_generated_harness(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        _make_fake_executable(bin_dir, "demo-tool")
        monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=True, install=True,
        )
        assert report["tests_passed"] is True, report.get("test_output", "")[-2000:]
        result = validator.validate(Path(report["harness"]))
        assert result["overall"] == "PASS", validator.format_report(result)
        assert len(result["categories"]) == 8
        for category in result["categories"]:
            assert category["passed"] == category["total"], category["name"]

    def test_validate_fails_when_required_file_deleted(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        harness = Path(report["harness"])
        (harness / "cli_anything" / "demo_tool" / "README.md").unlink()
        result = validator.validate(harness)
        assert result["overall"] == "FAIL"
        failed = [c for c in result["categories"] if not c["ok"]]
        assert failed, "at least one category must fail"

    def test_validate_rejects_unknown_shape(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        with pytest.raises(validator.ValidationError):
            validator.validate(Path(report["harness"]), shape="weird")

    def test_format_report_contains_verdict(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        result = validator.validate(Path(report["harness"]))
        text = validator.format_report(result)
        assert "FAIL" in text or "PASS" in text
        assert "directory_structure" in text


# ---------------------------------------------------------------------------
# Layer 1 — lifecycle unit tests.
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_list_clis_finds_installed_toolkit(self):
        result = lifecycle.list_clis()
        names = {c["software"] for c in result["clis"]}
        assert "muse" in names, "the installed cli-anything-muse must be listed"
        assert result["installed"] >= 1

    def test_list_clis_finds_generated(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        generator.build_harness(str(target), out_dir=tmp_path / "out",
                                run_tests=False, install=False)
        result = lifecycle.list_clis(tmp_path / "out", max_depth=2)
        names = {c["software"] for c in result["clis"]}
        assert "demo-tool" in names
        assert result["generated_only"] >= 1

    def test_format_list(self, tmp_path):
        result = lifecycle.list_clis()
        text = lifecycle.format_list(result)
        assert "muse" in text

    def test_refine_proposal_only_without_yes(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        # Add a new API function to the target source after the build.
        pkg = target / "demo_tool" / "convert.py"
        pkg.write_text(
            pkg.read_text(encoding="utf-8")
            + '\n\ndef export_all(items):\n    """Export every item."""\n    return items\n',
            encoding="utf-8",
        )
        proposal = lifecycle.refine(Path(report["harness"]))
        assert proposal["covered_count"] >= 1
        assert any("export_all" in g["api"] for g in proposal["uncovered_apis"])
        assert "note" in proposal  # proposal-only marker
        assert proposal["implemented"] == []

    def test_refine_with_yes_regenerates_missing_skill(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        _make_fake_executable(bin_dir, "demo-tool")
        monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=True, install=True,
        )
        assert report["tests_passed"] is True
        harness = Path(report["harness"])
        skill = harness / "cli_anything" / "demo_tool" / "skills" / "SKILL.md"
        assert skill.is_file()
        skill.unlink()
        proposal = lifecycle.refine(harness, yes=True)
        assert any("SKILL.md" in item for item in proposal["implemented"])
        assert skill.is_file()
        assert proposal["tests_passed"] is True

    def test_refine_never_removes_commands(self, tmp_path):
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        harness = Path(report["harness"])
        cli_file = harness / "cli_anything" / "demo_tool" / "demo_tool_cli.py"
        before = cli_file.read_text(encoding="utf-8")
        lifecycle.refine(harness, yes=True)
        after = cli_file.read_text(encoding="utf-8")
        assert before == after, "refine must never remove or rewrite commands"

    def test_run_tests_appends_test_md_on_pass(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        _make_fake_executable(bin_dir, "demo-tool")
        monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        harness = Path(report["harness"])
        test_md = harness / "cli_anything" / "demo_tool" / "tests" / "TEST.md"
        before = test_md.read_text(encoding="utf-8")
        assert "## Run" not in before
        result = lifecycle.run_tests(harness)
        assert result["passed"] is True, result.get("output", "")[-2000:]
        assert result["test_md_updated"], "TEST.md path must be reported on pass"
        assert Path(result["test_md_updated"]).is_file()
        after = test_md.read_text(encoding="utf-8")
        assert "## Run" in after
        assert result["resolver_reports_installed"] is True

    def test_run_tests_keeps_test_md_on_failure(self, tmp_path, monkeypatch):
        # No fake executable on PATH: the true-backend test fails, so
        # TEST.md must NOT be updated (previous results preserved).
        target = _make_target(tmp_path, "demo-tool")
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        harness = Path(report["harness"])
        test_md = harness / "cli_anything" / "demo_tool" / "tests" / "TEST.md"
        before = test_md.read_text(encoding="utf-8")
        result = lifecycle.run_tests(harness)
        assert result["passed"] is False
        assert result["test_md_updated"] is None
        assert result["suggestions"], "failure must come with suggestions"
        assert test_md.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Layer 1 — skill_generator unit tests.
# ---------------------------------------------------------------------------


class TestSkillGenerator:
    def _harness(self, tmp_path, name="demo-tool"):
        target = _make_target(tmp_path, name)
        report = generator.build_harness(
            str(target), out_dir=tmp_path / "out",
            run_tests=False, install=False,
        )
        return Path(report["harness"])

    def test_generate_skill_writes_both_copies(self, tmp_path):
        harness = self._harness(tmp_path)
        skill = skill_generator.generate_skill(harness)
        assert skill.is_file()
        assert "demo-tool" in skill.read_text(encoding="utf-8").lower()
        packaged = harness / "cli_anything" / "demo_tool" / "skills" / "SKILL.md"
        assert packaged.is_file()

    def test_generate_skill_uses_jinja_template_when_available(self, tmp_path):
        harness = self._harness(tmp_path)
        template = REPO_ROOT / "cli_anything_muse" / "templates" / "SKILL.md.template"
        assert template.is_file()
        skill = skill_generator.generate_skill(harness, template_path=template)
        text = skill.read_text(encoding="utf-8")
        assert "Basic Usage" in text
        assert "Command Groups" in text
        assert "State Management" in text

    def test_generate_skill_missing_harness_raises(self, tmp_path):
        with pytest.raises((ValueError, FileNotFoundError, OSError)):
            skill_generator.generate_skill(tmp_path / "nope")

    def test_extract_cli_structure_nested_groups(self, tmp_path):
        harness = self._harness(tmp_path)
        cli_file = harness / "cli_anything" / "demo_tool" / "demo_tool_cli.py"
        groups, has_preview = skill_generator.extract_cli_structure(
            cli_file, "cli-anything-demo-tool"
        )
        titles = [g.title for g in groups]
        assert any("project" in t.lower() for t in titles)
        assert has_preview is True
        commands = [c for g in groups for c in g.commands]
        assert commands, "commands must be extracted"
        assert all(c.full_command.startswith("cli-anything-demo-tool") for c in commands)

    def test_extract_cli_structure_large_file_no_backtracking(self, tmp_path):
        # 500 nested groups under one root group (the real generated-CLI
        # shape): extraction must stay fast (no catastrophic backtracking).
        big = tmp_path / "big_cli.py"
        lines = ['"""Big CLI."""\n', "import click\n",
                 '@click.group()\ndef cli():\n    """Root."""\n    pass\n']
        for i in range(500):
            lines.append(f'@cli.group("g{i}")\ndef g{i}():\n    """Group {i}."""\n    pass\n')
            lines.append(f'@g{i}.command("run")\ndef g{i}_run():\n    """Run {i}."""\n    pass\n')
        big.write_text("".join(lines), encoding="utf-8")
        start = time.monotonic()
        groups, _ = skill_generator.extract_cli_structure(big, "cli-anything-big")
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"extraction took {elapsed:.1f}s (backtracking?)"
        assert len(groups) >= 100

    def test_version_quoting(self, tmp_path):
        # YAML-safe quoting in the generated SKILL.md: a bare version in
        # setup.py must appear quoted in the skill front matter.
        harness = self._harness(tmp_path)
        setup = harness / "setup.py"
        setup.write_text(
            setup.read_text(encoding="utf-8").replace('version="1.0.0"', 'version="1.0"'),
            encoding="utf-8",
        )
        skill = skill_generator.generate_skill(harness)
        text = skill.read_text(encoding="utf-8")
        assert "`1.0`" in text, "version 1.0 must be quoted as `1.0` in the skill"

    def test_description_contains_software_name(self, tmp_path):
        harness = self._harness(tmp_path)
        skill = skill_generator.generate_skill(harness)
        text = skill.read_text(encoding="utf-8")
        assert "demo-tool" in text.lower()

    def test_intro_extraction_falls_back(self, tmp_path):
        # A harness with no README and no docstring still gets an intro.
        harness = tmp_path / "bare" / "agent-harness"
        subpkg = harness / "cli_anything" / "bare"
        subpkg.mkdir(parents=True)
        (subpkg / "__init__.py").write_text("", encoding="utf-8")
        (subpkg / "bare_cli.py").write_text("import click\n", encoding="utf-8")
        (harness / "setup.py").write_text('version="1.0.0"\n', encoding="utf-8")
        skill = skill_generator.generate_skill(harness)
        assert skill.is_file()
        assert skill.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# Layer 1 — preview_bundle unit tests.
# ---------------------------------------------------------------------------


class TestPreviewBundle:
    def _bundle_kwargs(self, **overrides):
        kwargs = dict(
            software="demo-tool",
            recipe="default",
            cli_command="cli-anything-demo-tool --json preview capture",
            harness_version="1.0.0",
            headline="Demo bundle",
            facts=["fact one"],
            source_identity="demo identity",
            source_data={"key": "value"},
        )
        kwargs.update(overrides)
        return kwargs

    def test_create_bundle_and_cache_hit(self, tmp_path):
        first = preview_bundle.create_bundle(
            **self._bundle_kwargs(project_dir=tmp_path)
        )
        second = preview_bundle.create_bundle(
            **self._bundle_kwargs(project_dir=tmp_path)
        )
        assert first["bundle_id"] == second["bundle_id"]
        assert second["cache_hit"] is True

    def test_create_bundle_no_cache_forces_new(self, tmp_path):
        first = preview_bundle.create_bundle(
            **self._bundle_kwargs(project_dir=tmp_path)
        )
        second = preview_bundle.create_bundle(
            **self._bundle_kwargs(project_dir=tmp_path, use_cache=False)
        )
        assert second["cache_hit"] is False

    def test_latest_bundle(self, tmp_path):
        created = preview_bundle.create_bundle(
            **self._bundle_kwargs(project_dir=tmp_path)
        )
        latest = preview_bundle.latest_bundle("demo-tool", project_dir=tmp_path)
        assert latest is not None
        assert latest["bundle_id"] == created["bundle_id"]

    def test_latest_bundle_none_when_empty(self, tmp_path):
        assert preview_bundle.latest_bundle("demo-tool", project_dir=tmp_path) is None

    def test_inspect_bundle(self, tmp_path):
        created = preview_bundle.create_bundle(
            **self._bundle_kwargs(project_dir=tmp_path)
        )
        info = preview_bundle.inspect_bundle(created["bundle_dir"])
        assert info["bundle_id"] == created["bundle_id"]
        assert info["software"] == "demo-tool"

    def test_render_html(self, tmp_path):
        created = preview_bundle.create_bundle(
            **self._bundle_kwargs(project_dir=tmp_path)
        )
        out = preview_bundle.render_html(created["bundle_dir"])
        assert out.is_file()
        assert "demo-tool" in out.read_text(encoding="utf-8")

    def test_live_session_round_trip(self, tmp_path):
        created = preview_bundle.create_bundle(
            **self._bundle_kwargs(project_dir=tmp_path)
        )
        session = preview_bundle.live_start(
            "demo-tool", "default", created, project_dir=tmp_path
        )
        assert session["active"] is True
        pushed = preview_bundle.live_push(
            "demo-tool", "default", created, project_dir=tmp_path
        )
        assert pushed["current_bundle_id"] == created["bundle_id"]
        status = preview_bundle.live_status("demo-tool", project_dir=tmp_path)
        assert status["active"] is True
        stopped = preview_bundle.live_stop("demo-tool", project_dir=tmp_path)
        assert stopped["active"] is False

    def test_watch_session_snapshots(self, tmp_path):
        created = preview_bundle.create_bundle(
            **self._bundle_kwargs(project_dir=tmp_path)
        )
        preview_bundle.live_start("demo-tool", "default", created, project_dir=tmp_path)
        session_dir = preview_bundle.live_session_dir("demo-tool", project_dir=tmp_path)
        snapshots = preview_bundle.watch_session(
            session_dir, interval=0.01, max_cycles=2
        )
        assert len(snapshots) >= 1
        assert snapshots[0]["active"] is True

    def test_compute_fingerprint_stable(self):
        a = preview_bundle.compute_fingerprint({"x": 1, "y": [2, 3]})
        b = preview_bundle.compute_fingerprint({"y": [2, 3], "x": 1})
        assert a == b
        assert preview_bundle.compute_fingerprint({"x": 2}) != a

    def test_protocol_version(self):
        assert preview_bundle.PROTOCOL_VERSION == "preview-bundle/v1"


# ---------------------------------------------------------------------------
# Layer 1 — repl_skin unit tests.
# ---------------------------------------------------------------------------


class TestReplSkin:
    def test_banner_contains_software_and_version(self, capsys):
        skin = repl_skin.ReplSkin("demo-tool", "1.2.3", use_color=False)
        skin.print_banner()
        out = capsys.readouterr().out
        assert "demo-tool" in out.lower() or "Demo Tool" in out
        assert "1.2.3" in out

    def test_prompt_degrades_without_color(self):
        skin = repl_skin.ReplSkin("demo-tool", use_color=False)
        prompt = skin.prompt()
        assert prompt == "> " or "demo-tool" in prompt

    def test_no_color_env_disables_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        skin = repl_skin.ReplSkin("demo-tool")
        assert skin.color_enabled is False

    def test_success_error_warning_info(self, capsys):
        skin = repl_skin.ReplSkin("demo-tool", use_color=False)
        skin.success("ok")
        skin.error("bad")
        skin.warning("careful")
        skin.info("note")
        captured = capsys.readouterr()
        assert "ok" in captured.out
        assert "bad" in captured.err, "error() writes to stderr"
        assert "careful" in captured.out and "note" in captured.out

    def test_table_prints(self, capsys):
        skin = repl_skin.ReplSkin("demo-tool", use_color=False)
        skin.table(["Name", "Value"], [["a", "1"], ["b", "2"]])
        out = capsys.readouterr().out
        assert "Name" in out and "a" in out and "2" in out

    def test_strip_ansi_and_visible_len(self):
        colored = "\x1b[31mhello\x1b[0m"
        assert repl_skin.strip_ansi(colored) == "hello"
        assert repl_skin.visible_len(colored) == 5

    def test_history_file_default_location(self):
        skin = repl_skin.ReplSkin("demo-tool")
        assert ".cli-anything-demo-tool" in str(skin.history_file)

    def test_prompt_tokens(self):
        skin = repl_skin.ReplSkin("demo-tool", use_color=False)
        tokens = skin.prompt_tokens()
        assert tokens, "prompt tokens must be non-empty"


# ---------------------------------------------------------------------------
# Layer 1 — cli_hub unit tests.
# ---------------------------------------------------------------------------


class TestCliHub:
    @pytest.fixture(autouse=True)
    def _hub_state_isolation(self, tmp_path, monkeypatch):
        """Keep hub cache/state inside tmp (never the real ~/.cli-anything-hub)."""
        hub_dir = tmp_path / "hub"
        hub_dir.mkdir()
        monkeypatch.setattr(cli_hub, "STATE_DIR", hub_dir)
        monkeypatch.setattr(cli_hub, "STATE_FILE", hub_dir / "installed.json")
        monkeypatch.setattr(cli_hub, "CACHE_DIR", hub_dir / "cache")

    def test_load_registry_from_repo_file(self, monkeypatch):
        # Default walk-up resolution finds the repo registry.json.
        monkeypatch.delenv("CLI_HUB_REGISTRY_URL", raising=False)
        document = cli_hub.load_registry("harness")
        names = {e["name"] for e in document.get("clis", [])}
        assert "go-bot" in names

    def test_find_entry_go_bot(self):
        entry = cli_hub.find_entry("go-bot")
        assert entry is not None
        assert entry["display_name"] == "go-bot"
        assert "service" in entry["description"].lower() or "go-bot" in entry["description"].lower()

    def test_search_catalog(self):
        results = cli_hub.search_catalog("docker")
        assert any(e["name"] == "docker" for e in results)

    def test_list_categories(self):
        categories = cli_hub.list_categories()
        assert "ai" in categories

    def test_registry_cache_and_stale_fallback(self, tmp_path, monkeypatch):
        # Fresh fetch caches; a later fetch failure with a STALE cache
        # falls back to the cached copy instead of erroring.
        registry = tmp_path / "registry.json"
        registry.write_text(
            json.dumps({"meta": {}, "clis": [{"name": "demo", "version": "1.0"}]}),
            encoding="utf-8",
        )
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(cli_hub, "CACHE_DIR", cache_dir)
        monkeypatch.setenv("CLI_HUB_REGISTRY_URL", registry.as_uri())
        first = cli_hub.load_registry("harness")
        assert {e["name"] for e in first["clis"]} == {"demo"}
        # Age the cache past the TTL, then break the source.
        cache_file = cache_dir / "harness.json"
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        cached["fetched_at"] = 0.0
        cache_file.write_text(json.dumps(cached), encoding="utf-8")
        registry.unlink()
        second = cli_hub.load_registry("harness")
        assert {e["name"] for e in second["clis"]} == {"demo"}

    def test_registry_cache_ttl(self, tmp_path, monkeypatch):
        # A fresh cache is used without re-fetching.
        registry = tmp_path / "registry.json"
        registry.write_text(
            json.dumps({"meta": {}, "clis": [{"name": "demo", "version": "1.0"}]}),
            encoding="utf-8",
        )
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(cli_hub, "CACHE_DIR", cache_dir)
        monkeypatch.setenv("CLI_HUB_REGISTRY_URL", registry.as_uri())
        cli_hub.load_registry("harness")
        cache_path = cache_dir / "harness.json"
        assert cache_path.is_file()
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cached["data"]["clis"][0]["name"] == "demo"

    def test_public_registry_empty_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("CLI_HUB_PUBLIC_REGISTRY_URL", raising=False)
        document = cli_hub.load_registry("public")
        assert document.get("clis") == []

    def test_matrix_commands_are_deferred(self):
        from click.testing import CliRunner
        runner = CliRunner()
        for args in (["matrix", "list"], ["matrix", "search", "x"],
                     ["matrix", "info", "x"], ["matrix", "doctor"]):
            result = runner.invoke(cli_hub.hub, args)
            assert result.exit_code == 1, args  # ClickException -> exit 1
            assert "deferred" in result.output.lower(), args

    def test_can_command_does_not_exist(self):
        assert "can" not in cli_hub.hub.commands
        assert "can" not in cli_hub.matrix_group.commands

    def test_install_strategy_pip_records_state(self, tmp_path, monkeypatch):
        # A pip entry installs via the local venv pip; use a tiny
        # already-installed package name to keep it fast and offline-safe.
        state_dir = tmp_path / "hub-state"
        state_dir.mkdir()
        monkeypatch.setattr(cli_hub, "STATE_DIR", state_dir)
        monkeypatch.setattr(cli_hub, "STATE_FILE", state_dir / "installed.json")
        entry = {"name": "demo", "install_cmd": "pip install click", "entry_point": "demo"}
        strategy = cli_hub.install_entry(entry)
        assert strategy == "pip"
        state = cli_hub._load_state()
        assert "demo" in state
        assert state["demo"]["strategy"] == "pip"


# ---------------------------------------------------------------------------
# Layer 2 — E2E: build a trial harness on a synthetic target, install in a
# venv, drive --help / --json / workflows. Module-scoped fixtures build
# each harness once (venv creation is slow).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def project_harness(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("project")
    bin_dir = tmp_path / "bin"
    _make_fake_executable(bin_dir, "demo-tool")
    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    target = _make_target(tmp_path, "demo-tool")
    report = generator.build_harness(
        str(target), out_dir=tmp_path / "out",
        run_tests=True, install=True,
    )
    assert report["tests_passed"] is True, report.get("test_output", "")[-3000:]
    harness = Path(report["harness"])
    return {"harness": harness, "bin_dir": bin_dir, "tmp": tmp_path}


@pytest.fixture(scope="module")
def service_harness(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("service")
    target = _make_target(tmp_path, "demo-service", kind="service")
    report = generator.build_harness(
        str(target), out_dir=tmp_path / "out",
        run_tests=True, install=True,
    )
    assert report["tests_passed"] is True, report.get("test_output", "")[-3000:]
    return {"harness": Path(report["harness"]), "tmp": tmp_path}


def _harness_cli(harness: Path, software: str) -> list[str]:
    """Resolve the generated harness's installed command."""
    command = f"cli-anything-{software}"
    installed = shutil.which(command)
    assert installed, f"installed command {command!r} not on PATH"
    return [installed]


class TestE2EProject:
    def test_help_lists_all_groups(self, project_harness):
        proc = subprocess.run(
            [*_harness_cli(project_harness["harness"], "demo-tool"), "--help"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        for group in ("project", "session", "export", "preview", "info", "list", "status"):
            assert group in proc.stdout

    def test_json_info(self, project_harness):
        proc = subprocess.run(
            [*_harness_cli(project_harness["harness"], "demo-tool"), "--json", "info"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["software"] == "demo-tool"
        assert payload["version"] == "1.0.0"

    def test_workflow_round_trip(self, project_harness, tmp_path):
        cli_cmd = _harness_cli(project_harness["harness"], "demo-tool")
        project_path = tmp_path / "demo.json"

        def run(*args, check=True):
            proc = subprocess.run(
                [*cli_cmd, *args], capture_output=True, text=True,
            )
            if check:
                assert proc.returncode == 0, proc.stderr
            return proc

        run("project", "new", "demo", "--output", str(project_path))
        assert project_path.is_file()
        run("--project", str(project_path), "project", "add", "note", "hello")
        data = json.loads(project_path.read_text(encoding="utf-8"))
        assert data["items"][0]["value"] == "hello"
        proc = run("--json", "--project", str(project_path), "list")
        assert json.loads(proc.stdout)["items"][0]["value"] == "hello"
        run("--project", str(project_path), "session", "undo")
        data = json.loads(project_path.read_text(encoding="utf-8"))
        assert data["items"] == []
        run("--project", str(project_path), "session", "redo")
        data = json.loads(project_path.read_text(encoding="utf-8"))
        assert data["items"][0]["value"] == "hello"
        out = tmp_path / "demo-export.json"
        run("--project", str(project_path), "export", "render",
            "--preset", "json", "--output", str(out))
        assert out.is_file()
        assert json.loads(out.read_text(encoding="utf-8"))["name"] == "demo"

    def test_dry_run_prints_without_running(self, project_harness):
        proc = subprocess.run(
            [*_harness_cli(project_harness["harness"], "demo-tool"),
             "--dry-run", "project", "new", "demo"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert "dry" in proc.stdout.lower() or "project" in proc.stdout.lower()

    def test_preview_capture_and_latest(self, project_harness, tmp_path):
        cli_cmd = _harness_cli(project_harness["harness"], "demo-tool")
        project_path = tmp_path / "demo.json"
        subprocess.run([*cli_cmd, "project", "new", "demo", "--output", str(project_path)],
                       capture_output=True, text=True, check=True)
        proc = subprocess.run(
            [*cli_cmd, "--json", "--project", str(project_path),
             "preview", "capture", "--recipe", "default"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["bundle_id"]
        proc = subprocess.run(
            [*cli_cmd, "--project", str(project_path), "preview", "latest"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert payload["bundle_id"] in proc.stdout

    def test_true_backend_export_default_preset(self, project_harness, tmp_path):
        # The fake executable is on PATH: the default preset must produce
        # a non-empty output file through the real backend.
        cli_cmd = _harness_cli(project_harness["harness"], "demo-tool")
        project_path = tmp_path / "demo.json"
        subprocess.run([*cli_cmd, "project", "new", "demo", "--output", str(project_path)],
                       capture_output=True, text=True, check=True)
        out = tmp_path / "demo.out"
        proc = subprocess.run(
            [*cli_cmd, "--project", str(project_path), "export", "render",
             "--output", str(out)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert out.is_file()
        assert out.stat().st_size > 0
        assert "converted by demo-tool" in out.read_text(encoding="utf-8")

    def test_missing_backend_fails_never_skips(self, project_harness, tmp_path):
        # PATH without the fake executable: the export must FAIL with a
        # clear error, not skip and not fake a success.
        cli_cmd = _harness_cli(project_harness["harness"], "demo-tool")
        project_path = tmp_path / "demo.json"
        subprocess.run([*cli_cmd, "project", "new", "demo", "--output", str(project_path)],
                       capture_output=True, text=True, check=True)
        venv_bin = str(Path(sys.executable).resolve().parent)
        harness_bin = str(project_harness["harness"] / ".venv" / "bin")
        clean_path = os.pathsep.join([harness_bin, venv_bin, "/usr/bin", "/bin"])
        out = tmp_path / "demo.out"
        proc = subprocess.run(
            [*cli_cmd, "--project", str(project_path), "export", "render",
             "--output", str(out)],
            capture_output=True, text=True,
            env={**os.environ, "PATH": clean_path},
        )
        assert proc.returncode != 0
        assert "not found on PATH" in proc.stderr
        assert not out.is_file()


class TestE2EService:
    def test_help_lists_groups(self, service_harness):
        proc = subprocess.run(
            [*_harness_cli(service_harness["harness"], "demo-service"), "--help"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        for group in ("server", "info", "list", "status"):
            assert group in proc.stdout

    def test_json_info(self, service_harness):
        proc = subprocess.run(
            [*_harness_cli(service_harness["harness"], "demo-service"),
             "--json", "--host", "127.0.0.1", "--port", "8080", "info"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["software"] == "demo-service"
        assert payload["base_url"] == "http://127.0.0.1:8080"

    def test_live_command_fails_gracefully_when_unreachable(self, service_harness):
        proc = subprocess.run(
            [*_harness_cli(service_harness["harness"], "demo-service"),
             "--host", "127.0.0.1", "--port", "1", "server", "status"],
            capture_output=True, text=True,
        )
        assert proc.returncode != 0
        assert "unreachable" in proc.stderr.lower() or "refused" in proc.stderr.lower()
        assert "Traceback" not in proc.stderr


# ---------------------------------------------------------------------------
# Layer 3 — CLI subprocess tests (both console scripts).
# ---------------------------------------------------------------------------


class TestCliSubprocess:
    def test_resolve_cli_prefers_installed(self):
        cmd = _resolve_cli("cli-anything-muse", "cli_anything_muse.cli")
        assert cmd[0].endswith("cli-anything-muse")

    def test_resolve_cli_falls_back_with_note(self, capsys):
        cmd = _resolve_cli("cli-anything-no-such-command", "cli_anything_muse.cli")
        assert cmd == [sys.executable, "-m", "cli_anything_muse.cli"]
        assert "falling back" in capsys.readouterr().out.lower()

    def test_resolve_cli_forced_installed_raises(self, monkeypatch):
        monkeypatch.setenv("CLI_ANYTHING_FORCE_INSTALLED", "1")
        with pytest.raises(AssertionError):
            _resolve_cli("cli-anything-no-such-command", "cli_anything_muse.cli")

    def test_cli_anything_muse_help(self):
        proc = _run_toolkit_cli("cli-anything-muse", "cli_anything_muse.cli", "--help")
        assert "build" in proc.stdout
        assert "list" in proc.stdout
        assert "refine" in proc.stdout
        assert "test" in proc.stdout
        assert "validate" in proc.stdout

    def test_cli_anything_muse_version(self):
        proc = _run_toolkit_cli("cli-anything-muse", "cli_anything_muse.cli", "--version")
        assert __version__ in proc.stdout

    def test_cli_hub_help(self):
        proc = _run_toolkit_cli("cli-hub", "cli_anything_muse.cli_hub", "--help")
        for word in ("install", "uninstall", "update", "list", "search", "info", "launch", "previews", "matrix"):
            assert word in proc.stdout

    def test_cli_anything_muse_json_list(self):
        proc = _run_toolkit_cli("cli-anything-muse", "cli_anything_muse.cli", "--json", "list")
        payload = json.loads(proc.stdout)
        assert "clis" in payload
        assert any(c["software"] == "muse" for c in payload["clis"])

    def test_cli_hub_list_json_contains_go_bot(self):
        proc = _run_toolkit_cli("cli-hub", "cli_anything_muse.cli_hub", "list", "--json")
        payload = json.loads(proc.stdout)
        assert any(e["name"] == "go-bot" for e in payload)

    def test_cli_hub_search_json(self):
        proc = _run_toolkit_cli("cli-hub", "cli_anything_muse.cli_hub", "search", "go-bot", "--json")
        payload = json.loads(proc.stdout)
        assert any(e["name"] == "go-bot" for e in payload)

    def test_cli_hub_matrix_list_exits_2(self):
        proc = _run_toolkit_cli(
            "cli-hub", "cli_anything_muse.cli_hub", "matrix", "list", check=False
        )
        assert proc.returncode == 1
        assert "deferred" in proc.stdout.lower() or "deferred" in proc.stderr.lower()

    def test_repl_exits_on_quit(self):
        proc = subprocess.run(
            [*_resolve_cli("cli-anything-muse", "cli_anything_muse.cli")],
            input="help\nquit\n", capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0
        assert "build" in proc.stdout
