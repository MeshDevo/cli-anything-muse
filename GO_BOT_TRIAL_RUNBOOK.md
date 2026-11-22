# go-bot Trial Runbook

Trial target for the Muse-native CLI-Anything generator: drive the
generator on the real target **go-bot** (a service-client harness),
install it in a venv, and check `--help`, offline commands, and graceful
failure of live commands when the service is unreachable.

- Target source: `~/workspace/go-bot` (local copy of
  https://github.com/xyc999-ai/go-bot, Go implementation of the DeepSeek
  Harness: Controller + Redis + one Docker Host per account).
- Generator: `cli-anything-muse build` (this repo, Phases 0–7 + 6.5).
- Trial harness shape: **service** (stateless API client; connection
  parameters from `<SOFTWARE>_HOST` env vars, CLI options override them).

## Known state of go-bot (2026-11-22, verified)

- A Codespace runs the Controller + Redis:
  `https://literate-parakeet-7g45gw65vrg3xjrj-8080.app.github.dev/`
  (port 8080, Private — needs GitHub sign-in as MeshDevo; sleeps after
  ~30 min idle).
- Account `henry` exists in Redis; the main chat opens after typing it.
- **The model is broken:** go-bot speaks OpenAI Responses or Anthropic
  Messages only. The saved "Gemini" endpoint (gemini-2.5-flash-lite)
  fails because Gemini's OpenAI-compatible API does not implement the
  Responses API (POST `.../v1beta/openai/responses` returns 404). The
  agent cannot reply until the model is fixed (patch go-bot for Chat
  Completions, a Responses-implementing provider, or an Anthropic key).
- A go-bot CLI already exists from the adapted skill at
  `~/workspace/cli-anything-gobot/` (85 API operations, 29/29 tests
  pass). This trial re-generates it with the Muse-native generator and
  compares.

## Steps

### 1. Acquire + build

```bash
cd ~/workspace/cli-anything-muse-repo
source .venv/bin/activate
cli-anything-muse build ~/workspace/go-bot --out ~/workspace/go-bot-trial
```

Expected: shape detected as `service`; SOP at `go-bot-trial/GO-BOT.md`;
harness at `go-bot-trial/agent-harness/`; skill at
`go-bot-trial/skills/cli-anything-go-bot/SKILL.md`.

### 2. Install in a venv

```bash
python3 -m venv ~/workspace/go-bot-trial/agent-harness/.venv
source ~/workspace/go-bot-trial/agent-harness/.venv/bin/activate
pip install -e ~/workspace/go-bot-trial/agent-harness
which cli-anything-go-bot
```

Expected: the command is on PATH.

### 3. `--help` lists all groups

```bash
cli-anything-go-bot --help
```

Expected: groups listed (server, api, config, account, session, model,
schedule, files, repl — or the generated equivalents), plus `info`,
`list`, `status`.

### 4. Offline commands work

```bash
cli-anything-go-bot --json info
cli-anything-go-bot --json status
cli-anything-go-bot --json server status   # against an unreachable host
```

Expected: `info`/`status` print the connection summary as JSON;
`server status` against an unreachable host fails gracefully (clear
"unreachable" error, non-zero exit) — it must NOT hang, crash, or fake
a success.

### 5. Live commands fail gracefully when unreachable

```bash
cli-anything-go-bot --host 127.0.0.1 --port 1 server status
cli-anything-go-bot --host 127.0.0.1 --port 1 list
```

Expected: non-zero exit, stderr names the host/port and says the
service is unreachable, with the remedy (start the service / check
host and port). No stack trace, no silent empty success.

### 6. Live commands against the real Codespace (only when awake)

Wake the Codespace first (open its URL in a signed-in browser), then:

```bash
export GO_BOT_HOST=literate-parakeet-7g45gw65vrg3xjrj-8080.app.github.dev
export GO_BOT_PORT=443
export GO_BOT_SCHEME=https
cli-anything-go-bot --json server status
```

Note: the port is Private behind GitHub auth, so a plain CLI call from
this host will fail at the GitHub login redirect. That failure is
expected and must be reported as "not ok" (not as a hang). Live use
needs the port public or a permanent server with a public URL.

### 7. Four-layer suite, forced-installed

```bash
cd ~/workspace/go-bot-trial/agent-harness
CLI_ANYTHING_FORCE_INSTALLED=1 python3 -m pytest tests/ -v
```

Expected: 100% pass; the `_resolve_cli` output shows the installed
command path (not the module fallback). Append the full output to
`tests/TEST.md` part 2.

## What was NOT verified (record honestly)

- The go-bot agent replying end to end (blocked: model incompatibility,
  see above).
- Live commands against the real Controller from this host (blocked:
  Private Codespace port; see step 6).
- Any step that needs a model key (never stored; Henry provides it
  transiently or via the Secure Vault flow).
