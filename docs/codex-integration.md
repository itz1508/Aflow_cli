# Codex integration

A-Flow is agent-invoked through MCP instructions. Before implementation, call
`aflow_review`; after meaningful validated work, call
`aflow_update_work_state`; before claiming completion, call `aflow_verify`; and
when continuing, call `aflow_resume`.

No automatic host lifecycle hook is claimed. For Docker, use the stdio command
printed by `scripts/install-aflow.ps1`:

```text
docker compose run --rm -T aflow mcp
```

The mounted repository is read-only for A-Flow review. Operation state lives in
the persistent `aflow-operations` volume.
