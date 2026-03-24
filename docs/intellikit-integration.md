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
- The current implementation includes a native subprocess bridge for IntelliKit-backed execution.
- The bridge is environment-driven for now:
  - `CODEX_INTELLIKIT_PYTHON` overrides the Python interpreter.
  - `CODEX_INTELLIKIT_BRIDGE_SCRIPT` points at an explicit Python bridge script.
  - `CODEX_INTELLIKIT_BRIDGE_MODULE` overrides the default bundled bridge script with an importable Python module.
  - `CODEX_INTELLIKIT_ROOT` sets the bridge working directory and is prepended to `PYTHONPATH`.
- `gpu_inventory` can execute end-to-end once the Python-side bridge exists on the target system. The other GPU tools use the same bridge contract and can be implemented on the IntelliKit side without changing Codex's tool surface again.
- This repo now includes a scaffold bridge script at `scripts/intellikit_codex_bridge.py` that you can point Codex at during bring-up.

## Immediate Goal

The current slice establishes Codex-side ownership for:

- tool names and descriptions
- argument schemas
- tool registration
- agent-facing instructions
- a future runtime boundary

This keeps later work focused on the Python/runtime side of IntelliKit, rather than redesigning the agent surface inside Codex.

## Next Step

Deploy a Python-side bridge on a GPU-enabled system that accepts `--request-json <json>` and writes a single JSON object to stdout:

- Codex owns planning and tool selection.
- IntelliKit owns GPU-domain execution and artifact production.
- Codex receives typed results and feeds them back into the turn as native tool outputs.

## Bring-Up Path

On a GPU-capable machine, start by pointing Codex at the scaffold bridge:

```bash
export CODEX_INTELLIKIT_PYTHON=/path/to/python3
export CODEX_INTELLIKIT_ROOT=/path/to/intellikit
export CODEX_INTELLIKIT_BRIDGE_SCRIPT=/path/to/codex/scripts/intellikit_codex_bridge.py
```

The scaffold script already implements:

- `gpu_inventory`
  - returns platform, interpreter, IntelliKit subcomponent detection, and ROCm command discovery data
- `gpu_profile`, `gpu_inspect`, `gpu_validate`
  - return structured "not implemented yet" responses until IntelliKit execution is wired

You can also test the bridge directly before launching Codex:

```bash
python scripts/intellikit_codex_bridge.py \
  --request-json '{"tool":"gpu_inventory","arguments":{}}'
```

The current bridge does not assume a top-level `intellikit` Python import. It probes the real installable subpackages and entrypoints from the IntelliKit monorepo such as `rocm_mcp`, `metrix`, `linex`, `nexus`, `kerncap`, `accordo`, and `uprof_mcp`.

The intended follow-up is to replace the placeholder handlers in the Python bridge with real IntelliKit API calls or command wrappers on the GPU machine.
