# IntelliKit Integration

This repository now carries native scaffolding for an IntelliKit-backed GPU tool family inside `codex-rs/core`.

## Current State

- The integration is gated behind the `intellikit` feature flag.
- The following native tools are registered when the feature is enabled:
  - `gpu_inventory`
  - `gpu_list_metrics`
  - `gpu_profile`
  - `gpu_inspect`
  - `gpu_validate`
- These tools are first-class Codex built-ins, not MCP passthroughs.
- The current implementation includes a native subprocess bridge for IntelliKit-backed execution.
- The bridge is environment-driven for now:
  - `CODEX_INTELLIKIT_PYTHON` overrides the Python interpreter.
  - `CODEX_INTELLIKIT_BRIDGE_SCRIPT` points at an explicit Python bridge script.
  - `CODEX_INTELLIKIT_BRIDGE_MODULE` overrides the default bundled bridge script with an importable Python module.
  - `CODEX_INTELLIKIT_ROOT` only sets the bridge working directory and resolves relative script paths. It does not modify Python import paths.
- `gpu_inventory`, `gpu_list_metrics`, `gpu_profile`, `gpu_inspect`, and `gpu_validate` all execute end-to-end through the same Python-side bridge contract.
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
export CODEX_INTELLIKIT_BRIDGE_SCRIPT=/path/to/codex/scripts/intellikit_codex_bridge.py
```

If you use `CODEX_INTELLIKIT_ROOT`, treat it as a working directory only. IntelliKit packages must already be installed into the selected Python environment and discoverable by that interpreter without Codex patching `PYTHONPATH`.

The bridge script already implements:

- `gpu_inventory`
  - returns platform, interpreter, IntelliKit subcomponent detection, and ROCm command discovery data
- `gpu_list_metrics`
  - returns all available GPU performance metric names organized by category (compute, memory bandwidth, memory cache, memory pattern, memory LDS)
  - uses the Metrix backend to discover metrics supported by the detected GPU architecture, falling back to the Python metric catalog when no GPU is available
  - call this before `gpu_profile` to discover valid metric names
- `gpu_profile`
  - runs a real Metrix profiling step for the requested target and maps `objective` onto Metrix profiles such as `quick`, `memory`, `memory_bandwidth`, `memory_cache`, `compute`, or timing-only collection
  - requires the IntelliKit `metrix` package and its runtime dependencies to already be installed in the selected Python environment
- `gpu_inspect`
  - loads a saved profiling artifact by `artifact_id` or falls back to the newest saved run
  - filters kernels by `focus` across kernel names and collected metric names
  - returns the hottest kernels when no direct match exists, while marking that fallback in the summary
- `gpu_validate`
  - reruns a current profile for `target`
  - optionally compares the new run against `baseline_artifact_id`
  - maps `expectation` onto a Metrix mode and, when possible, into a strict pass/fail comparison for runtime or key metrics

Each `gpu_profile` run now persists a JSON artifact under `CODEX_INTELLIKIT_ARTIFACT_DIR` when set, otherwise under `CODEX_INTELLIKIT_ROOT/codex-intellikit-artifacts` or the current working directory. `gpu_inspect` and `gpu_validate` consume those persisted artifacts directly.

You can also test the bridge directly before launching Codex:

```bash
python scripts/intellikit_codex_bridge.py \
  --request-json '{"tool":"gpu_inventory","arguments":{}}'
```

The current bridge does not assume a top-level `intellikit` Python import. It probes the real installable subpackages and entrypoints from the IntelliKit monorepo such as `rocm_mcp`, `metrix`, `linex`, `nexus`, `kerncap`, `accordo`, and `uprof_mcp`.

This is intentionally still a bridge layer, not a final integrated Python SDK surface. The remaining follow-up is to harden the artifact schema, add richer focus/validation semantics, and validate the flow in the target GPU runtime environment where IntelliKit packages and ROCm tooling are fully installed.
