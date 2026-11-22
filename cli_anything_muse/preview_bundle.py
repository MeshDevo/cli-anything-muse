"""Preview protocol v1 producer, session/trajectory persistence, and read-only consumer."""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import click

PROTOCOL_VERSION = "preview-bundle/v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return slug or "default"


def compute_fingerprint(data: Any) -> str:
    """Compute a short deterministic 12-char SHA-256 fingerprint."""
    h = hashlib.sha256()
    if isinstance(data, Path):
        if data.exists() and data.is_file():
            h.update(data.read_bytes())
        else:
            h.update(str(data).encode("utf-8"))
    elif isinstance(data, (bytes, bytearray)):
        h.update(bytes(data))
    else:
        h.update(json.dumps(data, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()[:12]


def preview_root(software: str, project_dir: Path | None = None) -> Path:
    base = Path(project_dir) if project_dir else Path.home()
    return base / ".cli-anything" / "previews" / _safe_slug(software)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def find_cached_bundle(
    software: str,
    recipe: str,
    cache_key: str,
    project_dir: Path | None = None,
) -> dict[str, Any] | None:
    recipe_dir = preview_root(software, project_dir) / _safe_slug(recipe)
    if not recipe_dir.is_dir():
        return None
    candidates: list[tuple[str, dict[str, Any], Path]] = []
    for child in recipe_dir.iterdir():
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = _read_json(manifest_path)
        except Exception:
            continue
        if (
            manifest.get("protocol_version") == PROTOCOL_VERSION
            and manifest.get("software") == _safe_slug(software)
            and manifest.get("recipe") == recipe
            and manifest.get("bundle_kind") == "capture"
            and manifest.get("status") in {"ok", "partial"}
            and manifest.get("cache_key") == cache_key
        ):
            candidates.append((manifest.get("created_at", ""), manifest, child))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, manifest, bdir = candidates[0]
    return {
        "cache_hit": True,
        "bundle_id": manifest["bundle_id"],
        "bundle_dir": str(bdir),
        "manifest_path": str(bdir / "manifest.json"),
        "summary_path": str(bdir / manifest.get("summary_path", "summary.json")),
        "manifest": manifest,
    }


def create_bundle(
    *,
    software: str,
    recipe: str,
    bundle_kind: str = "capture",
    status: str = "ok",
    cli_command: str,
    harness_version: str,
    backend_info: dict[str, Any] | None = None,
    source_identity: str = "",
    source_data: Any = "",
    artifacts: list[dict[str, Any]] | None = None,
    headline: str = "",
    facts: list[str] | None = None,
    warnings: list[str] | None = None,
    next_actions: list[str] | None = None,
    context: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    labels: list[str] | None = None,
    source_bundles: list[str] | None = None,
    cache_key: str | None = None,
    project_dir: Path | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    sw = _safe_slug(software)
    fp = compute_fingerprint(source_data if source_data != "" else source_identity)
    effective_cache_key = cache_key or f"{sw}:{recipe}:{fp}"

    if use_cache and bundle_kind == "capture":
        cached = find_cached_bundle(sw, recipe, effective_cache_key, project_dir)
        if cached is not None:
            return cached

    bundle_id = f"{_utc_compact()}-{fp}-{_safe_slug(recipe)}"
    bdir = preview_root(sw, project_dir) / _safe_slug(recipe) / bundle_id
    bdir.mkdir(parents=True, exist_ok=True)

    processed_artifacts: list[dict[str, Any]] = []
    for art in artifacts or []:
        art_copy = dict(art)
        if "path" in art_copy:
            p = Path(art_copy["path"])
            if not p.is_absolute():
                p = bdir / p
            if p.exists() and p.is_file():
                art_copy.setdefault("size_bytes", p.stat().st_size)
                art_copy.setdefault("sha256", hashlib.sha256(p.read_bytes()).hexdigest())
            try:
                art_copy["path"] = str(p.relative_to(bdir))
            except ValueError:
                art_copy["path"] = str(p)
        processed_artifacts.append(art_copy)

    summary = {
        "headline": headline or f"{sw} {recipe} ({status})",
        "facts": facts or [],
        "warnings": warnings or [],
        "next_actions": next_actions or [],
    }
    summary_path = bdir / "summary.json"
    _write_json(summary_path, summary)

    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "bundle_id": bundle_id,
        "bundle_kind": bundle_kind,
        "software": sw,
        "recipe": recipe,
        "status": status,
        "created_at": _utc_now(),
        "generator": {
            "command": cli_command,
            "harness_version": harness_version,
            "backend": backend_info or {},
        },
        "source": {
            "identity": source_identity,
            "fingerprint": fp,
        },
        "cache_key": effective_cache_key,
        "artifacts": processed_artifacts,
        "summary_path": "summary.json",
        "source_bundles": source_bundles or [],
        "context": context or {},
        "warnings": warnings or [],
        "metrics": metrics or {},
        "labels": labels or [],
    }
    manifest_path = bdir / "manifest.json"
    _write_json(manifest_path, manifest)

    return {
        "cache_hit": False,
        "bundle_id": bundle_id,
        "bundle_dir": str(bdir),
        "manifest_path": str(manifest_path),
        "summary_path": str(summary_path),
        "manifest": manifest,
    }


def latest_bundle(
    software: str,
    recipe: str | None = None,
    project_dir: Path | None = None,
) -> dict[str, Any] | None:
    root = preview_root(software, project_dir)
    if not root.is_dir():
        return None
    dirs = [root / _safe_slug(recipe)] if recipe else [p for p in root.iterdir() if p.is_dir() and p.name != "live"]
    candidates: list[tuple[str, dict[str, Any], Path]] = []
    for rdir in dirs:
        if not rdir.is_dir():
            continue
        for child in rdir.iterdir():
            mp = child / "manifest.json"
            if mp.is_file():
                try:
                    m = _read_json(mp)
                    candidates.append((m.get("created_at", ""), m, child))
                except Exception:
                    continue
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, m, bdir = candidates[0]
    return {
        "bundle_id": m["bundle_id"],
        "bundle_dir": str(bdir),
        "manifest_path": str(bdir / "manifest.json"),
        "summary_path": str(bdir / m.get("summary_path", "summary.json")),
        "manifest": m,
    }


def live_session_dir(software: str, project_dir: Path | None = None) -> Path:
    return preview_root(software, project_dir) / "live"


def _append_trajectory(sdir: Path, entry: dict[str, Any]) -> dict[str, Any]:
    tpath = sdir / "trajectory.json"
    if tpath.is_file():
        traj = _read_json(tpath)
    else:
        traj = {"protocol_version": PROTOCOL_VERSION, "entries": []}
    entries = traj.setdefault("entries", [])
    entry_with_seq = {"seq": len(entries) + 1, **entry}
    entries.append(entry_with_seq)
    _write_json(tpath, traj)
    return {"path": str(tpath), "count": len(entries), "latest": entry_with_seq}


def live_start(
    software: str,
    recipe: str,
    bundle_result: dict[str, Any],
    command_meta: str = "preview live start",
    project_dir: Path | None = None,
) -> dict[str, Any]:
    sdir = live_session_dir(software, project_dir)
    sdir.mkdir(parents=True, exist_ok=True)
    traj_info = _append_trajectory(
        sdir,
        {
            "timestamp": _utc_now(),
            "command": command_meta,
            "reason": "live_start",
            "bundle_id": bundle_result["bundle_id"],
            "bundle_dir": bundle_result["bundle_dir"],
            "status": bundle_result["manifest"]["status"],
        },
    )
    session = {
        "protocol_version": PROTOCOL_VERSION,
        "active": True,
        "software": _safe_slug(software),
        "recipe": recipe,
        "session_dir": str(sdir),
        "session_path": str(sdir / "session.json"),
        "trajectory_path": traj_info["path"],
        "current_bundle_id": bundle_result["bundle_id"],
        "current_bundle_dir": bundle_result["bundle_dir"],
        "current_manifest_path": bundle_result["manifest_path"],
        "current_summary_path": bundle_result["summary_path"],
        "publish_reason": "live_start",
        "latest_command": command_meta,
        "trajectory_summary": {
            "total_entries": traj_info["count"],
            "latest": traj_info["latest"],
        },
        "updated_at": _utc_now(),
    }
    _write_json(sdir / "session.json", session)
    return session


def live_push(
    software: str,
    recipe: str,
    bundle_result: dict[str, Any],
    command_meta: str = "preview live push",
    reason: str = "live_push",
    project_dir: Path | None = None,
) -> dict[str, Any]:
    sdir = live_session_dir(software, project_dir)
    spath = sdir / "session.json"
    if not spath.is_file():
        return live_start(software, recipe, bundle_result, command_meta=command_meta, project_dir=project_dir)
    session = _read_json(spath)
    traj_info = _append_trajectory(
        sdir,
        {
            "timestamp": _utc_now(),
            "command": command_meta,
            "reason": reason,
            "bundle_id": bundle_result["bundle_id"],
            "bundle_dir": bundle_result["bundle_dir"],
            "status": bundle_result["manifest"]["status"],
        },
    )
    session.update(
        {
            "active": True,
            "recipe": recipe,
            "current_bundle_id": bundle_result["bundle_id"],
            "current_bundle_dir": bundle_result["bundle_dir"],
            "current_manifest_path": bundle_result["manifest_path"],
            "current_summary_path": bundle_result["summary_path"],
            "publish_reason": reason,
            "latest_command": command_meta,
            "trajectory_summary": {
                "total_entries": traj_info["count"],
                "latest": traj_info["latest"],
            },
            "updated_at": _utc_now(),
        }
    )
    _write_json(spath, session)
    return session


def live_status(software: str, project_dir: Path | None = None) -> dict[str, Any]:
    sdir = live_session_dir(software, project_dir)
    spath = sdir / "session.json"
    if not spath.is_file():
        return {
            "exists": False,
            "active": False,
            "software": _safe_slug(software),
            "session_dir": str(sdir),
            "session_path": str(spath),
        }
    session = _read_json(spath)
    return {"exists": True, **session}


def live_stop(software: str, project_dir: Path | None = None) -> dict[str, Any]:
    sdir = live_session_dir(software, project_dir)
    spath = sdir / "session.json"
    if not spath.is_file():
        return {"exists": False, "active": False, "session_dir": str(sdir), "session_path": str(spath)}
    session = _read_json(spath)
    session["active"] = False
    session["publish_reason"] = "live_stop"
    session["updated_at"] = _utc_now()
    _write_json(spath, session)
    return {"exists": True, **session}


# --- Read-only consumer functions (used by cli-hub previews) ---


def _resolve_bundle_target(target: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    p = Path(target)
    if p.is_file() and p.name == "session.json":
        session = _read_json(p)
        bdir = Path(session["current_bundle_dir"])
    elif p.is_file() and p.name == "manifest.json":
        bdir = p.parent
    elif (p / "session.json").is_file():
        session = _read_json(p / "session.json")
        bdir = Path(session["current_bundle_dir"])
    else:
        bdir = p
    manifest = _read_json(bdir / "manifest.json")
    summary_file = bdir / manifest.get("summary_path", "summary.json")
    summary = _read_json(summary_file) if summary_file.is_file() else {}
    return bdir, manifest, summary


def inspect_bundle(target: str | Path) -> dict[str, Any]:
    """Read-only inspection of a preview bundle or live session directory."""
    bdir, manifest, summary = _resolve_bundle_target(target)
    return {
        "bundle_dir": str(bdir),
        "bundle_id": manifest.get("bundle_id"),
        "protocol_version": manifest.get("protocol_version"),
        "software": manifest.get("software"),
        "recipe": manifest.get("recipe"),
        "status": manifest.get("status"),
        "summary": summary,
        "artifacts": manifest.get("artifacts", []),
        "metrics": manifest.get("metrics", {}),
        "warnings": manifest.get("warnings", []),
    }


def render_html(target: str | Path, output_path: str | Path | None = None) -> Path:
    """Render a read-only HTML report for an existing bundle (never synthesizes artifacts)."""
    info = inspect_bundle(target)
    summary = info["summary"]
    headline = html_lib.escape(str(summary.get("headline", info["bundle_id"])))
    facts_li = "".join(f"<li>{html_lib.escape(str(f))}</li>" for f in summary.get("facts", []))
    warn_li = "".join(f"<li>{html_lib.escape(str(w))}</li>" for w in summary.get("warnings", []))
    art_rows = "".join(
        f"<tr><td>{html_lib.escape(str(a.get('name', '')))}</td>"
        f"<td>{html_lib.escape(str(a.get('kind', '')))}</td>"
        f"<td>{html_lib.escape(str(a.get('path', '')))}</td>"
        f"<td>{html_lib.escape(str(a.get('size_bytes', '')))}</td></tr>"
        for a in info.get("artifacts", [])
    )
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{headline}</title>"
        "<style>body{font-family:sans-serif;max-width:900px;margin:2rem auto;}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:6px;text-align:left}"
        ".warn{color:#a15c00}</style></head><body>"
        f"<h1>{headline}</h1>"
        f"<p><strong>Software:</strong> {html_lib.escape(str(info['software']))} | "
        f"<strong>Recipe:</strong> {html_lib.escape(str(info['recipe']))}</p>"
        f"<p><strong>Bundle:</strong> {html_lib.escape(str(info['bundle_id']))} | "
        f"<strong>Status:</strong> {html_lib.escape(str(info['status']))}</p>"
        f"<h2>Facts</h2><ul>{facts_li}</ul>"
        f"<h2 class='warn'>Warnings</h2><ul>{warn_li}</ul>"
        "<h2>Artifacts</h2><table><tr><th>Name</th><th>Kind</th><th>Path</th><th>Bytes</th></tr>"
        f"{art_rows}</table></body></html>\n"
    )
    out = Path(output_path) if output_path else Path(info["bundle_dir"]) / "preview.html"
    out.write_text(doc, encoding="utf-8")
    return out


def watch_session(
    session_target: str | Path,
    interval: float = 0.5,
    max_cycles: int = 1,
) -> list[dict[str, Any]]:
    """Read-only poll of session.json returning observed snapshots."""
    p = Path(session_target)
    spath = p if p.name == "session.json" else p / "session.json"
    snapshots: list[dict[str, Any]] = []
    last_id: str | None = None
    for i in range(max(1, max_cycles)):
        if spath.is_file():
            data = _read_json(spath)
            cid = data.get("current_bundle_id")
            if cid != last_id or i == 0:
                snapshots.append(data)
                last_id = cid
        if i + 1 < max_cycles:
            time.sleep(interval)
    return snapshots


def register_preview_commands(
    cli_group: click.Group,
    software: str,
    version: str,
    recipes_fn: Callable[[], list[dict[str, str]]],
    capture_fn: Callable[[str, Path | None, bool], dict[str, Any]],
) -> None:
    """Attach standard producer preview commands to a harness Click group."""

    @cli_group.group("preview")
    @click.pass_context
    def preview_group(ctx: click.Context) -> None:
        """Produce truthful preview bundles and live trajectories."""

    @preview_group.command("recipes")
    @click.pass_context
    def cmd_recipes(ctx: click.Context) -> None:
        """List available preview recipes."""
        recipes = recipes_fn()
        if ctx.obj.get("json"):
            click.echo(json.dumps({"software": software, "recipes": recipes}, indent=2))
        else:
            for r in recipes:
                click.echo(f"{r['name']}: {r.get('description', '')}")

    @preview_group.command("capture")
    @click.option("--recipe", default="default", show_default=True, help="Recipe name.")
    @click.option("--no-cache", is_flag=True, help="Bypass cached bundle lookup.")
    @click.pass_context
    def cmd_capture(ctx: click.Context, recipe: str, no_cache: bool) -> None:
        """Capture a preview bundle through the real backend."""
        proj = ctx.obj.get("project_dir")
        res = capture_fn(recipe, Path(proj) if proj else None, not no_cache)
        if ctx.obj.get("json"):
            click.echo(json.dumps(res, indent=2))
        else:
            click.echo(f"Bundle {res['bundle_id']} -> {res['bundle_dir']}")

    @preview_group.command("latest")
    @click.option("--recipe", default=None, help="Filter by recipe.")
    @click.pass_context
    def cmd_latest(ctx: click.Context, recipe: str | None) -> None:
        """Return the latest existing preview bundle without rendering."""
        proj = ctx.obj.get("project_dir")
        res = latest_bundle(software, recipe=recipe, project_dir=Path(proj) if proj else None)
        if res is None:
            raise click.ClickException("No preview bundles found.")
        if ctx.obj.get("json"):
            click.echo(json.dumps(res, indent=2))
        else:
            click.echo(f"Latest: {res['bundle_id']} ({res['bundle_dir']})")

    @preview_group.command("diff")
    @click.argument("bundle_a")
    @click.argument("bundle_b")
    @click.pass_context
    def cmd_diff(ctx: click.Context, bundle_a: str, bundle_b: str) -> None:
        """Publish an immutable diff bundle comparing two bundles."""
        a = inspect_bundle(bundle_a)
        b = inspect_bundle(bundle_b)
        proj = ctx.obj.get("project_dir")
        res = create_bundle(
            software=software,
            recipe="diff",
            bundle_kind="diff",
            status="ok",
            cli_command=f"cli-anything-{_safe_slug(software)} preview diff",
            harness_version=version,
            source_identity=f"{a['bundle_id']}..{b['bundle_id']}",
            source_data={"a": a, "b": b},
            headline=f"Diff {a['bundle_id']} -> {b['bundle_id']}",
            facts=[f"A status={a['status']}", f"B status={b['status']}"],
            source_bundles=[a["bundle_dir"], b["bundle_dir"]],
            project_dir=Path(proj) if proj else None,
            use_cache=False,
        )
        if ctx.obj.get("json"):
            click.echo(json.dumps(res, indent=2))
        else:
            click.echo(f"Diff bundle {res['bundle_id']} -> {res['bundle_dir']}")

    @preview_group.group("live")
    def live_group() -> None:
        """Manage live preview sessions and trajectories."""

    @live_group.command("start")
    @click.option("--recipe", default="default", show_default=True)
    @click.pass_context
    def cmd_live_start(ctx: click.Context, recipe: str) -> None:
        proj = ctx.obj.get("project_dir")
        pdir = Path(proj) if proj else None
        bundle = capture_fn(recipe, pdir, False)
        res = live_start(software, recipe, bundle, project_dir=pdir)
        click.echo(json.dumps(res, indent=2) if ctx.obj.get("json") else f"Live started: {res['current_bundle_id']}")

    @live_group.command("push")
    @click.option("--recipe", default="default", show_default=True)
    @click.pass_context
    def cmd_live_push(ctx: click.Context, recipe: str) -> None:
        proj = ctx.obj.get("project_dir")
        pdir = Path(proj) if proj else None
        bundle = capture_fn(recipe, pdir, False)
        res = live_push(software, recipe, bundle, project_dir=pdir)
        click.echo(json.dumps(res, indent=2) if ctx.obj.get("json") else f"Live pushed: {res['current_bundle_id']}")

    @live_group.command("status")
    @click.pass_context
    def cmd_live_status(ctx: click.Context) -> None:
        proj = ctx.obj.get("project_dir")
        res = live_status(software, project_dir=Path(proj) if proj else None)
        click.echo(json.dumps(res, indent=2) if ctx.obj.get("json") else f"Active: {res.get('active')}")

    @live_group.command("stop")
    @click.pass_context
    def cmd_live_stop(ctx: click.Context) -> None:
        proj = ctx.obj.get("project_dir")
        res = live_stop(software, project_dir=Path(proj) if proj else None)
        click.echo(json.dumps(res, indent=2) if ctx.obj.get("json") else f"Active: {res.get('active')}")