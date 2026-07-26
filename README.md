# A-Flow CLI

A-Flow reviews an immutable plan and creates additive companion artifacts. It
does not accept tasks, rewrite plans, redesign systems, execute user code, or
write through its MCP tool.

Supported platform: Windows x64. The deterministic CLI/MCP review needs no API
key, cloud service, or local model.

## Judge quick test

Use the release wheel when one is attached to the repository release. This is
the judge path and does not require Ollama:

```powershell
winget install --id astral-sh.uv -e
.\scripts\judge-smoke.ps1 -WheelPath .\theoneshot_aflow-0.1.0-py3-none-any.whl
```

The script installs the wheel into an isolated `uv` tool environment, runs the
demo, and validates its bundle. It makes no source-code changes. Until a
release wheel is attached, test a checkout with:

```powershell
uv tool run --from . aflow demo --output-dir .\aflow-demo
uv tool run --from . aflow validate-bundle --output-dir .\aflow-demo
```

## Windows quick start

```powershell
uv sync --extra dev
uv run aflow review --plan .\plan.md --plan-id release-plan --output-dir .\aflow-output
uv run aflow validate-bundle --output-dir .\aflow-output
```

`aflow setup --yes` is optional. It may install Ollama through WinGet and
downloads `qwen2.5-coder:7b` for local semantic notes. It records only the
local endpoint and model name; no API key is persisted.

## Tool calling

```powershell
uv run aflow mcp
```

After installing the wheel, add this to Codex `config.toml` and restart Codex:

```toml
[mcp_servers.aflow]
command = "aflow"
args = ["mcp"]
startup_timeout_sec = 20.0
tool_timeout_sec = 90.0
```

The server exposes exactly one STDIO tool, `aflow_review`. Its input requires
only `original_plan` and `plan_id`. It returns an additive review bundle in
memory; the caller keeps and preserves the original plan.

## Automatic trigger policy

The host classifies each request with
`schemas/v1/aflow-trigger-context.schema.json` before invoking A-Flow.

- An eligible supplied **plan** is sent directly to `aflow_review`.
- An eligible implementation **task** tells the host to draft a candidate
  immutable plan. That task does not call A-Flow. When the plan appears, the
  separate plan context is sent to `aflow_review`.
- Read-only questions, research, and work with no expected mutation are
  skipped.

A-Flow triggers only when a plan exists. It never receives a raw task and never
executes the resulting plan. The host owns task interpretation and
implementation; A-Flow owns additive plan review only.

## Companion bundle

The CLI writes `manifest.json`, gap review/fulfillment, evaluation, fixtures,
test specifications, and an additive plan update. A `decision-required.json`
file is returned when resolving a gap would change design.

## Development checks

```powershell
uv run pytest -q
uv run aflow demo --output-dir .\demo-output
uv run aflow validate-bundle --output-dir .\demo-output
```

## Independent verification

`aflow verify --operation-id <operation_id>` executes versioned validation and
fixture contracts, records historical attempts, assesses required criteria,
and updates the existing work state. `aflow status --operation-id` is
read-only; `aflow resume --operation-id` reloads continuity and reports the
next exact action.

Validation commands use structured executable arrays, closed stdin, explicit
timeouts, bounded output, environment allowlists, and repository-contained
working directories. Legacy descriptive artifacts are reported as unsupported
instead of being guessed into commands. Semantic observations remain advisory
until repository evidence confirms them.

## Docker and Codex

Run `.\scripts\install-aflow.ps1` after Docker Desktop is running. The
Compose bundle starts A-Flow, Ollama, and model setup, persists operations and
models in named volumes, keeps Ollama private, and prints the stdio MCP
registration command. The default local model is `qwen2.5-coder:7b`; no cloud
API key is required. Codex integration is agent-invoked through MCP
instructions in `docs/codex-integration.md`; no automatic host hook is claimed.
