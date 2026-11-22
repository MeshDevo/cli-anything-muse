# CLI-Anything (Muse-Native Clean-Room Edition)

A clean-room, Muse-native implementation of CLI-Anything that turns local codebases, GUI applications, and network services into stateful, agent-ready Python CLIs (`cli-anything-<software>`) with JSON output, interactive REPLs, locked session persistence, preview bundles, four-layer test suites, and self-contained `SKILL.md` documentation.

## Key Muse Adaptations

- **Skill-first delivery:** Delivered as a Muse custom skill under `~/workspace/custom-skills/cli-anything/SKILL.md` (no Claude Code plugin shell or slash commands).
- **PEP 668 / venv-only Python:** All editable installs, dependencies, and test runs happen inside virtual environments (`python3 -m venv`), never system `pip`.
- **No Docker:** All backend invocations and service checks run directly on the host.
- **Stdlib-first service clients:** Generated service/API harnesses default to `urllib.request` with zero external HTTP dependencies unless a protocol (such as WebSockets) requires one.
- **Strict verification discipline:** No claim without an actual test run; live-backend E2E tests fail rather than skip when the target software is missing.

## Installation

### Simplest — install straight from GitHub

```bash
python3 -m venv ~/.venvs/cli-anything-muse
source ~/.venvs/cli-anything-muse/bin/activate
pip install git+https://github.com/MeshDevo/cli-anything-muse.git
cli-anything-muse --help
cli-hub --help
```

### From a local clone

```bash
git clone https://github.com/MeshDevo/cli-anything-muse.git
cd cli-anything-muse
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional extras:

```bash
pip install -e ".[interactive,templates]"   # prompt_toolkit REPL + Jinja2 skill templates
pip install -e ".[dev]"                     # pytest for the test suite
```

## Test Suite

```bash
source .venv/bin/activate
python3 -m pytest tests/ -v
```

For release verification, force the installed command (no module
fallback) so the subprocess tests prove the real installed entry point:

```bash
CLI_ANYTHING_FORCE_INSTALLED=1 python3 -m pytest tests/ -v
```

## Usage

```bash
cli-anything-muse build <path-or-github-url> [--out DIR]
cli-anything-muse list [--max-depth N] [--json]
cli-anything-muse refine <harness-path> [--focus "..."] [--yes]
cli-anything-muse test <harness-path>
cli-anything-muse validate <harness-path> [--shape auto|project|service] [--json]
cli-hub list [--category CAT] [--source harness|public] [--json]
cli-hub install <name>
```

## Layout

```text
.
├── README.md
├── setup.py                        # package cli-anything-muse
├── registry.json                   # harness registry (manually maintained)
├── GO_BOT_TRIAL_RUNBOOK.md         # trial runbook for the go-bot target
├── DELIVERY_STATUS.md              # what is here, what is missing, what is next
├── cli_anything_muse/
│   ├── __init__.py
│   ├── analyzer.py                 # Phase 0 + Phase 1
│   ├── generator.py                # Phases 2-3, 6.5, 7
│   ├── validator.py                # validate (8 categories)
│   ├── lifecycle.py                # list / refine / test
│   ├── skill_generator.py          # SKILL.md generation (AST + Jinja2/simple)
│   ├── repl_skin.py                # unified REPL skin
│   ├── preview_bundle.py           # preview-bundle/v1 producer + consumer
│   ├── cli.py                      # `cli-anything-muse` entry point
│   ├── cli_hub.py                  # `cli-hub` entry point
│   └── templates/
│       └── SKILL.md.template       # Jinja2 skill template
├── custom-skills/
│   ├── cli-anything/SKILL.md       # generator skill (+ guides/METHODOLOGY.md)
│   └── cli-hub/SKILL.md            # hub skill
└── tests/
    ├── TEST.md                     # test plan (part 1) + results (part 2)
    └── test_toolkit_core.py        # four-layer suite for this toolkit
```

## Verification Discipline

No claim without a run: a harness is reported as working only after its
tests have actually executed and passed in the venv. Live-backend E2E
tests fail rather than skip when the target software is missing. Gaps
are documented, not hidden — see `tests/TEST.md` part 2 and
`DELIVERY_STATUS.md`.