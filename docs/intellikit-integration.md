# IntelliKit Integration

This repository now carries native scaffolding for an IntelliKit-backed GPU tool family inside `codex-rs/core`.

## Current State

- The integration is gated behind the `intellikit` feature flag.
- The following native tools are registered when the feature is enabled:
  - `gpu_inventory`
  - `gpu_profile`
  - `gpu_inspect`
  - `gpu_validate`
- These tools are first-class Codex built-ins, not MCP passthroughs.
- The current implementation is intentionally compile-state only: the handler validates arguments and returns a structured "runtime unavailable" response until the IntelliKit execution bridge is wired up.

## Immediate Goal

The current slice establishes Codex-side ownership for:

- tool names and descriptions
- argument schemas
- tool registration
- agent-facing instructions
- a future runtime boundary

This keeps later work focused on attaching a real sidecar or in-process bridge, rather than redesigning the agent surface.

## Next Step

Wire `IntelliKitRuntime` in `codex-rs/core/src/intellikit/mod.rs` to a real execution backend on a GPU-enabled system. The intended bridge contract is:

- Codex owns planning and tool selection.
- IntelliKit owns GPU-domain execution and artifact production.
- Codex receives typed results and feeds them back into the turn as native tool outputs.
