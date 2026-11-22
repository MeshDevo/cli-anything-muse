---
name: "cli-hub"
description: "Browse, search, install, update, launch, and inspect preview bundles for CLI-Anything harnesses via cli-hub."
---

# cli-hub (Catalog & Preview Consumer Skill)

`cli-hub` manages discovery, installation, execution, and read-only preview inspection for `cli-anything-*` harnesses.

## Prerequisites

- Python >= 3.10 inside a virtual environment (PEP 668 compliant).
- `cli-hub` installed from the `cli-anything-muse` package.

## Commands

- `cli-hub list [--category CAT] [--source harness|public] [--json]` — List cached/remote registries (1-hour TTL cache under `~/.cli-anything-hub/cache/`).
- `cli-hub search <query> [--json]` — Case-insensitive substring search across name, display name, description, and category.
- `cli-hub info <name> [--json]` — Show full registry metadata and install details.
- `cli-hub install <name>` — Install via `pip` (into active or per-CLI `.venv`), `npm`, `uv`, `bundled`, or `generic` strategy.
- `cli-hub update <name>` / `cli-hub uninstall <name>` — Update or remove a tracked CLI.
- `cli-hub launch <name> [ARGS...]` — Execute an installed CLI and pass through arguments.
- `cli-hub previews inspect|html|watch|open <target>` — Read-only inspection of `preview-bundle/v1` bundles, sessions, and trajectories. Never renders or fabricates artifacts.

## Registry Configuration

Override default hosted registry URLs with `CLI_HUB_REGISTRY_URL` and `CLI_HUB_PUBLIC_REGISTRY_URL` (HTTP/HTTPS or local `file://` URLs).