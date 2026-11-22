"""Entry point: `cli-hub` (install / uninstall / update / list / search /
info / launch / previews / matrix).

Implements spec section 5: registry fetch (HTTP GET, 15 s timeout,
1-hour TTL cache under ~/.cli-anything-hub/cache/, stale-cache fallback),
the merged harness+public view, and the install strategies (pip, npm,
uv, bundled, generic).

Gap choices (spec section 8, recorded — not silently resolved):
- 8.2 `matrix`: command surface exists, full semantics deferred to a
  later phase. The commands print that plainly and exit 2.
- 8.3 `can`: NOT implemented — its semantics were not traced; do not
  guess. There is no `can` command.
- 8.4 Registry regeneration: the registry is a manually maintained JSON
  file; no regeneration script pretends otherwise.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import click

from . import __version__
from .preview_bundle import inspect_bundle, render_html, watch_session

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS = 3600  # 1-hour TTL (spec section 5.3).
FETCH_TIMEOUT_SECONDS = 15  # HTTP GET timeout (spec section 5.3).
STATE_DIR = Path.home() / ".cli-anything-hub"
CACHE_DIR = STATE_DIR / "cache"
STATE_FILE = STATE_DIR / "installed.json"

# Gap record (spec section 8.3): `can` is intentionally absent.
CAN_NOT_IMPLEMENTED = (
    "The `can` command is not implemented: its semantics were not "
    "traced in the reverse-engineering pass (spec section 8.3), and "
    "guessing them would be wrong."
)


class HubError(Exception):
    """Raised for registry / install failures with a clear message."""


# ---------------------------------------------------------------------------
# Registry loading (fetch + cache + stale fallback).
# ---------------------------------------------------------------------------


def _default_harness_registry_url() -> str:
    """Default harness registry: CLI_HUB_REGISTRY_URL, else the repo file.

    The repo file is found by walking up from this module (works for the
    editable install and the repo checkout); when it cannot be found the
    harness registry is empty and the user is told to set the URL.
    """
    env_url = os.environ.get("CLI_HUB_REGISTRY_URL")
    if env_url:
        return env_url
    module = Path(__file__).resolve()
    for parent in [module.parent, *module.parents]:
        candidate = parent / "registry.json"
        if candidate.is_file():
            return candidate.as_uri()
    return ""


def _default_public_registry_url() -> str:
    """Default public registry: env override, else an empty catalog."""
    env_url = os.environ.get("CLI_HUB_PUBLIC_REGISTRY_URL")
    if env_url:
        return env_url
    module = Path(__file__).resolve()
    for parent in [module.parent, *module.parents]:
        candidate = parent / "public_registry.json"
        if candidate.is_file():
            return candidate.as_uri()
    return ""


def _cache_path(kind: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{kind}.json"


def _read_cached(kind: str) -> dict[str, Any] | None:
    """Read a cached registry (any age — staleness is checked by caller)."""
    path = _cache_path(kind)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cached(kind: str, data: dict[str, Any]) -> None:
    path = _cache_path(kind)
    path.write_text(
        json.dumps({"fetched_at": time.time(), "data": data}, indent=2),
        encoding="utf-8",
    )


def _fetch_document(url: str) -> dict[str, Any]:
    """Fetch one registry document (HTTP GET or file:// read)."""
    if url.startswith("file://"):
        path = Path(urllib.request.url2pathname(url[7:]))
        return json.loads(path.read_text(encoding="utf-8"))
    request = urllib.request.Request(url, headers={"User-Agent": "cli-hub/1.0"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def load_registry(kind: str) -> dict[str, Any]:
    """Load one registry: fresh cache, else fetch, else stale cache.

    On fetch failure a stale cached copy is returned when one exists;
    with no cached copy the error propagates — except for the public
    registry, which returns an empty catalog instead (spec 5.3).
    """
    if kind == "harness":
        url = _default_harness_registry_url()
    else:
        url = _default_public_registry_url()
    if not url:
        return {"meta": {}, "clis": []}
    cached = _read_cached(kind)
    if cached and time.time() - cached.get("fetched_at", 0) < CACHE_TTL_SECONDS:
        return cached["data"]
    try:
        data = _fetch_document(url)
        _write_cached(kind, data)
        return data
    except (OSError, ValueError, urllib.error.URLError) as exc:
        if cached:
            return cached["data"]
        if kind == "public":
            return {"meta": {}, "clis": []}
        raise HubError(f"cannot fetch {kind} registry from {url}: {exc}") from exc


def load_merged_catalog() -> list[dict[str, Any]]:
    """Merged view: harness + public entries, each tagged with `source`."""
    merged: list[dict[str, Any]] = []
    for kind in ("harness", "public"):
        document = load_registry(kind)
        for entry in document.get("clis", []):
            tagged = dict(entry)
            tagged["source"] = kind
            merged.append(tagged)
    return merged


def find_entry(name: str) -> dict[str, Any] | None:
    """Lookup by name, case-insensitive, across both registries."""
    wanted = name.lower()
    for entry in load_merged_catalog():
        if str(entry.get("name", "")).lower() == wanted:
            return entry
    return None


def search_catalog(query: str) -> list[dict[str, Any]]:
    """Case-insensitive substring search over name/description/category/
    display name (spec section 5.3)."""
    needle = query.lower()
    results = []
    for entry in load_merged_catalog():
        haystack = " ".join(str(entry.get(field, "")) for field in
                            ("name", "display_name", "description", "category"))
        if needle in haystack.lower():
            results.append(entry)
    return results


def list_categories() -> list[str]:
    """Sorted unique category set across both registries."""
    return sorted({str(e.get("category", "")) for e in load_merged_catalog() if e.get("category")})


# ---------------------------------------------------------------------------
# Install state + strategies.
# ---------------------------------------------------------------------------


def _load_state() -> dict[str, Any]:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _strategy_for(entry: dict[str, Any]) -> str:
    """Pick the install strategy from the registry entry."""
    if entry.get("package_manager") == "npm" or entry.get("npm_package"):
        return "npm"
    if entry.get("package_manager") == "uv":
        return "uv"
    install_cmd = str(entry.get("install_cmd") or "")
    if install_cmd.startswith("pip install"):
        return "pip"
    if entry.get("bundle_url"):
        return "bundled"
    return "generic"


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def _install_pip(entry: dict[str, Any]) -> None:
    install_cmd = str(entry.get("install_cmd") or "")
    if not install_cmd.startswith("pip install "):
        raise HubError(f"pip entry {entry.get('name')} has no pip install_cmd")
    args = shlex.split(install_cmd[len("pip install "):])
    proc = _run([sys.executable, "-m", "pip", "install", *args])
    if proc.returncode != 0:
        raise HubError(f"pip install failed: {proc.stderr.strip()}")


def _install_npm(entry: dict[str, Any]) -> None:
    package = entry.get("npm_package") or entry.get("name")
    npm = shutil.which("npm")
    if not npm:
        raise HubError("npm is not installed on this host")
    proc = _run([npm, "install", "-g", str(package)])
    if proc.returncode != 0:
        raise HubError(f"npm install failed: {proc.stderr.strip()}")


def _install_uv(entry: dict[str, Any]) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise HubError("uv is not installed on this host")
    proc = _run([uv, "tool", "install", str(entry.get("name"))])
    if proc.returncode != 0:
        raise HubError(f"uv install failed: {proc.stderr.strip()}")


def _install_bundled(entry: dict[str, Any]) -> None:
    url = entry.get("bundle_url")
    if not url:
        raise HubError(
            f"bundled entry {entry.get('name')} has no bundle_url; "
            "download the artifact manually"
        )
    dest_dir = STATE_DIR / "bundles" / str(entry.get("name"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(str(url)).name
    urllib.request.urlretrieve(str(url), dest)
    click.echo(f"Downloaded bundle to {dest} (install it manually if needed).")


def _install_generic(entry: dict[str, Any]) -> None:
    install_cmd = str(entry.get("install_cmd") or "")
    if not install_cmd:
        raise HubError(f"entry {entry.get('name')} has no install command")
    proc = subprocess.run(install_cmd, shell=True, capture_output=True, text=True)
    if proc.returncode != 0:
        raise HubError(f"install failed: {proc.stderr.strip()}")


_INSTALLERS = {
    "pip": _install_pip,
    "npm": _install_npm,
    "uv": _install_uv,
    "bundled": _install_bundled,
    "generic": _install_generic,
}


def install_entry(entry: dict[str, Any]) -> str:
    """Install one entry; record it in the local state file."""
    strategy = _strategy_for(entry)
    _INSTALLERS[strategy](entry)
    state = _load_state()
    state[str(entry.get("name"))] = {
        "strategy": strategy,
        "entry_point": entry.get("entry_point"),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save_state(state)
    return strategy


def uninstall_entry(name: str) -> str:
    """Uninstall one entry by its recorded strategy."""
    state = _load_state()
    record = state.get(name)
    strategy = (record or {}).get("strategy") or "pip"
    if strategy == "pip":
        proc = _run([sys.executable, "-m", "pip", "uninstall", "-y", f"cli-anything-{name}"])
        if proc.returncode != 0:
            raise HubError(f"pip uninstall failed: {proc.stderr.strip()}")
    elif strategy == "npm":
        npm = shutil.which("npm")
        if npm:
            proc = _run([npm, "uninstall", "-g", name])
            if proc.returncode != 0:
                raise HubError(f"npm uninstall failed: {proc.stderr.strip()}")
    elif strategy == "uv":
        uv = shutil.which("uv")
        if uv:
            proc = _run([uv, "tool", "uninstall", name])
            if proc.returncode != 0:
                raise HubError(f"uv uninstall failed: {proc.stderr.strip()}")
    state.pop(name, None)
    _save_state(state)
    return strategy


# ---------------------------------------------------------------------------
# CLI surface.
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(__version__, prog_name="cli-hub")
@click.pass_context
def hub(ctx: click.Context) -> None:
    """Browse, install, and inspect CLI-Anything harnesses."""
    ctx.ensure_object(dict)


def _emit(payload: Any, as_json: bool, text: str) -> None:
    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo(text)


@hub.command("list")
@click.option("--category", default=None, help="Filter by category.")
@click.option("--source", "source", type=click.Choice(["harness", "public"]), default=None, help="Filter by registry source.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
def cmd_list(category: str | None, source: str | None, as_json: bool) -> None:
    """List CLIs in the catalog."""
    entries = load_merged_catalog()
    if category:
        entries = [e for e in entries if str(e.get("category", "")).lower() == category.lower()]
    if source:
        entries = [e for e in entries if e.get("source") == source]
    text = "\n".join(
        f"{e.get('name')} ({e.get('version')}) [{e.get('source')}] — {e.get('description', '')}"
        for e in entries
    ) or "No CLIs found."
    _emit(entries, as_json, text)


@hub.command("search")
@click.argument("query")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
def cmd_search(query: str, as_json: bool) -> None:
    """Search the catalog by name, description, or category."""
    results = search_catalog(query)
    text = "\n".join(
        f"{e.get('name')} ({e.get('version')}) — {e.get('description', '')}"
        for e in results
    ) or "No matches."
    _emit(results, as_json, text)


@hub.command("info")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
def cmd_info(name: str, as_json: bool) -> None:
    """Show full registry metadata and install details for one CLI."""
    entry = find_entry(name)
    if entry is None:
        raise click.ClickException(f"no CLI named {name!r} in the catalog")
    text = "\n".join(f"{k}: {v}" for k, v in entry.items())
    _emit(entry, as_json, text)


@hub.command("install")
@click.argument("name")
def cmd_install(name: str) -> None:
    """Install a CLI by name (pip / npm / uv / bundled / generic)."""
    entry = find_entry(name)
    if entry is None:
        raise click.ClickException(f"no CLI named {name!r} in the catalog")
    strategy = install_entry(entry)
    click.echo(f"Installed {name} via {strategy}.")


@hub.command("uninstall")
@click.argument("name")
def cmd_uninstall(name: str) -> None:
    """Remove an installed CLI."""
    strategy = uninstall_entry(name)
    click.echo(f"Uninstalled {name} (strategy was {strategy}).")


@hub.command("update")
@click.argument("name")
def cmd_update(name: str) -> None:
    """Update an installed CLI (re-install via its strategy)."""
    entry = find_entry(name)
    if entry is None:
        raise click.ClickException(f"no CLI named {name!r} in the catalog")
    strategy = install_entry(entry)
    click.echo(f"Updated {name} via {strategy}.")


@hub.command("launch")
@click.argument("name")
@click.argument("args", nargs=-1)
def cmd_launch(name: str, args: tuple[str, ...]) -> None:
    """Run an installed CLI, passing arguments through."""
    entry = find_entry(name)
    if entry is None:
        raise click.ClickException(f"no CLI named {name!r} in the catalog")
    command = entry.get("entry_point") or f"cli-anything-{name}"
    exe = shutil.which(str(command))
    if not exe:
        raise click.ClickException(
            f"entry point {command!r} is not on PATH; install {name} first"
        )
    proc = subprocess.run([exe, *args])
    raise SystemExit(proc.returncode)


# --- Previews: read-only consumer (never a render path) --------------------


@hub.group("previews")
def previews_group() -> None:
    """Read-only inspection of preview-bundle/v1 bundles."""


@previews_group.command("inspect")
@click.argument("target")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
def cmd_previews_inspect(target: str, as_json: bool) -> None:
    """Inspect a bundle or live session directory (read-only)."""
    info = inspect_bundle(target)
    text = (
        f"Bundle: {info['bundle_id']} ({info['status']})\n"
        f"Software: {info['software']} / recipe: {info['recipe']}\n"
        f"Headline: {info['summary'].get('headline', '')}"
    )
    _emit(info, as_json, text)


@previews_group.command("html")
@click.argument("target")
@click.option("--output", "output", type=click.Path(), default=None, help="Output HTML path.")
def cmd_previews_html(target: str, output: str | None) -> None:
    """Render a read-only HTML report for a bundle."""
    out = render_html(target, output)
    click.echo(f"Wrote {out}")


@previews_group.command("watch")
@click.argument("target")
@click.option("--interval", type=float, default=0.5, show_default=True, help="Poll interval in seconds.")
@click.option("--cycles", type=int, default=1, show_default=True, help="Poll cycles.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
def cmd_previews_watch(target: str, interval: float, cycles: int, as_json: bool) -> None:
    """Poll a live session.json and report observed snapshots."""
    snapshots = watch_session(target, interval=interval, max_cycles=cycles)
    text = "\n".join(
        f"{s.get('current_bundle_id')} (active={s.get('active')})" for s in snapshots
    ) or "No live session found."
    _emit(snapshots, as_json, text)


@previews_group.command("open")
@click.argument("target")
def cmd_previews_open(target: str) -> None:
    """Print the bundle directory and HTML report path (read-only)."""
    info = inspect_bundle(target)
    html_path = render_html(target)
    click.echo(f"Bundle: {info['bundle_dir']}")
    click.echo(f"HTML report: {html_path}")


# --- Matrix: surface only, semantics deferred (spec section 8.2) -----------


def _matrix_deferred(command: str) -> None:
    raise click.ClickException(
        f"cli-hub matrix {command} is deferred to a later phase "
        "(spec section 8.2): the command surface exists, but the matrix "
        "data model, preflight rules, and doctor diagnostics were not "
        "traced, so the semantics are not implemented."
    )


@hub.group("matrix")
def matrix_group() -> None:
    """Capability/recipe subsystem (deferred — surface only)."""


@matrix_group.command("list")
def cmd_matrix_list() -> None:
    """List matrix entries (deferred)."""
    _matrix_deferred("list")


@matrix_group.command("search")
@click.argument("query")
def cmd_matrix_search(query: str) -> None:
    """Search matrix entries (deferred)."""
    _matrix_deferred("search")


@matrix_group.command("info")
@click.argument("name")
def cmd_matrix_info(name: str) -> None:
    """Show a matrix entry (deferred)."""
    _matrix_deferred("info")


@matrix_group.command("preflight")
@click.argument("name")
def cmd_matrix_preflight(name: str) -> None:
    """Preflight checks for a matrix install (deferred)."""
    _matrix_deferred("preflight")


@matrix_group.command("install")
@click.argument("name")
def cmd_matrix_install(name: str) -> None:
    """Install a CLI with a capability set (deferred)."""
    _matrix_deferred("install")


@matrix_group.command("doctor")
def cmd_matrix_doctor() -> None:
    """Diagnose matrix health (deferred)."""
    _matrix_deferred("doctor")


@matrix_group.command("recipes")
def cmd_matrix_recipes() -> None:
    """List matrix recipes (deferred)."""
    _matrix_deferred("recipes")


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (spec section 8.6: the `main` form)."""
    hub(argv, standalone_mode=True)
