#!/usr/bin/env python3
"""Codex <-> IntelliKit bridge scaffold.

This script implements the JSON contract expected by the native IntelliKit
runtime in `codex-rs/core/src/intellikit/mod.rs`.

It is intentionally lightweight:
- `gpu_inventory` returns useful bring-up data today
- the remaining GPU tools return structured "not implemented" responses
- no IntelliKit dependency is required just to run the script
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REQUEST_KEYS = {"tool", "arguments"}
GPU_INVENTORY_TOOL = "gpu_inventory"
GPU_INSPECT_TOOL = "gpu_inspect"
GPU_PROFILE_TOOL = "gpu_profile"
GPU_VALIDATE_TOOL = "gpu_validate"
KNOWN_TOOLS = {
    GPU_INVENTORY_TOOL,
    GPU_INSPECT_TOOL,
    GPU_PROFILE_TOOL,
    GPU_VALIDATE_TOOL,
}
COMMAND_TIMEOUT_SECONDS = 5
COMMAND_OUTPUT_LIMIT = 4000
PROFILE_TIMEOUT_SECONDS = 300
PROFILE_KERNEL_LIMIT = 10
TOOLKIT_PACKAGES = {
    "accordo": {"module": "accordo", "entrypoints": ["accordo", "accordo-mcp"]},
    "kerncap": {"module": "kerncap", "entrypoints": ["kerncap", "kerncap-mcp"]},
    "linex": {"module": "linex", "entrypoints": ["linex-mcp"]},
    "metrix": {"module": "metrix", "entrypoints": ["metrix", "metrix-mcp"]},
    "nexus": {"module": "nexus", "entrypoints": ["nexus-mcp"]},
    "rocm_mcp": {
        "module": "rocm_mcp",
        "entrypoints": ["rocminfo-mcp", "hip-compiler-mcp", "hip-docs-mcp"],
    },
    "uprof_mcp": {"module": "uprof_mcp", "entrypoints": ["uprof-profiler-mcp"]},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-json", required=True)
    args = parser.parse_args()

    try:
        request = json.loads(args.request_json)
    except json.JSONDecodeError as err:
        return emit_response(
            success=False,
            message=f"Invalid --request-json payload: {err}",
            data=None,
        )

    error = validate_request(request)
    if error is not None:
        return emit_response(success=False, message=error, data={"request": request})

    tool = request["tool"]
    arguments = request["arguments"]

    if tool == GPU_INVENTORY_TOOL:
        message, data = handle_gpu_inventory()
        return emit_response(success=True, message=message, data=data)

    if tool == GPU_PROFILE_TOOL:
        error = validate_gpu_profile_arguments(arguments)
        if error is not None:
            return emit_response(
                success=False,
                message=error,
                data={"tool": tool, "arguments": arguments},
            )
        try:
            message, data = handle_gpu_profile(arguments)
        except Exception as err:  # pragma: no cover - exercised in GPU environment
            return emit_response(
                success=False,
                message=f"`{tool}` failed: {err}",
                data={
                    "tool": tool,
                    "arguments": arguments,
                    "metrix": probe_python_module("metrix"),
                    "rocprofv3": probe_command("rocprofv3", ["--version"]),
                },
            )
        return emit_response(success=True, message=message, data=data)

    if tool in {GPU_INSPECT_TOOL, GPU_VALIDATE_TOOL}:
        return emit_response(
            success=False,
            message=(
                f"`{tool}` is not implemented in the Python bridge yet. "
                "The Codex-side integration is ready; implement the IntelliKit "
                "execution path here on the GPU machine."
            ),
            data={"tool": tool, "arguments": arguments},
        )

    return emit_response(
        success=False,
        message=f"Unknown IntelliKit tool: {tool}",
        data={"tool": tool, "arguments": arguments},
    )


def validate_request(request: Any) -> str | None:
    if not isinstance(request, dict):
        return "Request payload must be a JSON object."

    missing = sorted(key for key in REQUEST_KEYS if key not in request)
    if missing:
        return f"Request payload is missing required keys: {', '.join(missing)}"

    if set(request.keys()) != REQUEST_KEYS:
        extra = sorted(set(request.keys()) - REQUEST_KEYS)
        if extra:
            return f"Request payload contains unexpected keys: {', '.join(extra)}"

    tool = request["tool"]
    if not isinstance(tool, str):
        return "Request field `tool` must be a string."
    if tool not in KNOWN_TOOLS:
        return f"Request field `tool` must be one of: {', '.join(sorted(KNOWN_TOOLS))}"

    arguments = request["arguments"]
    if not isinstance(arguments, dict):
        return "Request field `arguments` must be a JSON object."

    return None


def validate_gpu_profile_arguments(arguments: dict[str, Any]) -> str | None:
    target = arguments.get("target")
    if not isinstance(target, str) or not target.strip():
        return "Request field `arguments.target` must be a non-empty string for `gpu_profile`."

    for key in ("workload", "objective"):
        value = arguments.get(key)
        if value is not None and not isinstance(value, str):
            return f"Request field `arguments.{key}` must be a string when provided."

    return None


def handle_gpu_inventory() -> tuple[str, dict[str, Any]]:
    toolkit_components = {
        name: probe_toolkit_component(name, spec["module"], spec["entrypoints"])
        for name, spec in TOOLKIT_PACKAGES.items()
    }
    rocm_import = probe_python_module("rocm")
    command_probes = {
        "rocminfo": probe_command("rocminfo", ["--support"]),
        "rocm-smi": probe_command("rocm-smi", ["--showproductname"]),
        "amd-smi": probe_command("amd-smi", ["list", "--json"]),
    }

    available_commands = sorted(
        name for name, probe in command_probes.items() if probe["available"]
    )
    available_components = sorted(
        name
        for name, probe in toolkit_components.items()
        if probe["module"]["available"]
        or any(entrypoint["available"] for entrypoint in probe["entrypoints"].values())
    )

    message = (
        "GPU inventory bridge completed. "
        f"Detected IntelliKit components: {', '.join(available_components) if available_components else 'none'}. "
        f"Discovered GPU command-line tools: {', '.join(available_commands) if available_commands else 'none'}."
    )

    data = {
        "bridge": {
            "cwd": os.getcwd(),
            "pythonExecutable": sys.executable,
            "scriptPath": str(Path(__file__).resolve()),
        },
        "platform": {
            "machine": platform.machine(),
            "node": platform.node(),
            "pythonVersion": platform.python_version(),
            "release": platform.release(),
            "system": platform.system(),
        },
        "environment": {
            "codexIntelliKitPython": os.environ.get("CODEX_INTELLIKIT_PYTHON"),
            "codexIntelliKitRoot": os.environ.get("CODEX_INTELLIKIT_ROOT"),
            "pythonPath": os.environ.get("PYTHONPATH"),
        },
        "imports": {
            "rocm": rocm_import,
        },
        "toolkitComponents": toolkit_components,
        "commands": command_probes,
    }
    return message, data


def handle_gpu_profile(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        from metrix import Metrix
    except Exception as err:  # pragma: no cover - exercised in GPU environment
        raise RuntimeError(
            "Metrix is not importable in this interpreter. Install the IntelliKit "
            "`metrix` package with its runtime dependencies before using `gpu_profile`. "
            f"Original error: {err}"
        ) from err

    target = arguments["target"].strip()
    workload = arguments.get("workload")
    objective = arguments.get("objective")
    command = build_profile_command(target, workload)
    selected_profile, time_only, rationale = select_profile_mode(objective)
    timeout_seconds = int(
        os.environ.get("CODEX_INTELLIKIT_PROFILE_TIMEOUT", PROFILE_TIMEOUT_SECONDS)
    )

    started = time.time()
    profiler = Metrix()
    results = profiler.profile(
        command=command,
        profile=selected_profile,
        time_only=time_only,
        num_replays=1,
        aggregate_by_kernel=True,
        timeout_seconds=timeout_seconds,
    )
    elapsed_seconds = time.time() - started
    kernels = sort_profiled_kernels(results.kernels)
    displayed_kernels = kernels[:PROFILE_KERNEL_LIMIT]
    mode = "time-only mode" if time_only else f"profile `{selected_profile}`"

    if results.total_kernels == 0:
        message = (
            f"GPU profile completed with Metrix using {mode}, but no GPU kernels were captured "
            f"for `{command}`."
        )
    else:
        message = (
            f"GPU profile completed with Metrix using {mode}. "
            f"Profiled {results.total_kernels} kernels for `{command}`."
        )

    data = {
        "command": command,
        "target": target,
        "workload": workload,
        "objective": objective,
        "arch": profiler.arch,
        "elapsedSeconds": round(elapsed_seconds, 3),
        "availableProfiles": profiler.list_profiles(),
        "selectedMode": {
            "profile": selected_profile,
            "timeOnly": time_only,
            "reason": rationale,
            "timeoutSeconds": timeout_seconds,
        },
        "kernelCount": results.total_kernels,
        "kernels": [serialize_kernel_results(kernel) for kernel in displayed_kernels],
        "truncatedKernelCount": max(0, len(kernels) - len(displayed_kernels)),
        "metrix": probe_python_module("metrix"),
        "rocprofv3": probe_command("rocprofv3", ["--version"]),
    }
    return message, data


def build_profile_command(target: str, workload: str | None) -> str:
    command_parts = [target.strip()]
    if workload is not None and workload.strip():
        command_parts.append(workload.strip())
    return " ".join(command_parts)


def select_profile_mode(objective: str | None) -> tuple[str | None, bool, str]:
    if objective is None or not objective.strip():
        return "quick", False, "defaulted to the quick Metrix profile"

    normalized = objective.strip().lower()
    if any(keyword in normalized for keyword in ("latency", "time", "timing", "dispatch")):
        return None, True, f"mapped objective `{objective}` to timing-focused collection"
    if "bandwidth" in normalized:
        return "memory_bandwidth", False, f"mapped objective `{objective}` to memory bandwidth"
    if "cache" in normalized:
        return "memory_cache", False, f"mapped objective `{objective}` to cache analysis"
    if any(
        keyword in normalized
        for keyword in ("memory", "hbm", "coalesc", "lds", "atomic")
    ):
        return "memory", False, f"mapped objective `{objective}` to the memory profile"
    if any(
        keyword in normalized
        for keyword in ("compute", "flop", "throughput", "arithmetic", "intensity")
    ):
        return "compute", False, f"mapped objective `{objective}` to the compute profile"
    return "quick", False, f"mapped objective `{objective}` to the quick profile"


def sort_profiled_kernels(kernels: list[Any]) -> list[Any]:
    return sorted(kernels, key=lambda kernel: kernel.duration_us.avg, reverse=True)


def serialize_kernel_results(kernel: Any) -> dict[str, Any]:
    return {
        "name": kernel.name,
        "durationUs": serialize_statistics(kernel.duration_us),
        "metrics": {
            name: serialize_statistics(stats)
            for name, stats in sorted(kernel.metrics.items())
        },
    }


def serialize_statistics(stats: Any) -> dict[str, Any]:
    return {
        "min": stats.min,
        "max": stats.max,
        "avg": stats.avg,
        "count": stats.count,
    }


def probe_toolkit_component(
    component_name: str, module_name: str, entrypoints: list[str]
) -> dict[str, Any]:
    del component_name
    entrypoint_probes = {
        entrypoint: probe_executable(entrypoint) for entrypoint in entrypoints
    }
    return {
        "module": probe_python_module(module_name),
        "entrypoints": entrypoint_probes,
    }


def probe_python_module(module_name: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return {
            "available": False,
            "error": f"Module `{module_name}` is not importable in this interpreter.",
            "location": None,
            "version": None,
        }

    package_name = module_name.split(".", maxsplit=1)[0]
    try:
        version = importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        version = None

    return {
        "available": True,
        "error": None,
        "location": spec.origin,
        "version": version,
    }


def probe_executable(name: str) -> dict[str, Any]:
    executable = shutil.which(name)
    return {
        "available": executable is not None,
        "path": executable,
    }


def probe_command(command: str, args: list[str]) -> dict[str, Any]:
    executable = shutil.which(command)
    if executable is None:
        return {
            "available": False,
            "command": command,
            "error": "not found on PATH",
            "exitCode": None,
            "path": None,
            "stderr": None,
            "stdout": None,
        }

    try:
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "available": True,
            "command": command,
            "error": f"timed out after {COMMAND_TIMEOUT_SECONDS} seconds",
            "exitCode": None,
            "path": executable,
            "stderr": None,
            "stdout": None,
        }
    except OSError as err:
        return {
            "available": True,
            "command": command,
            "error": str(err),
            "exitCode": None,
            "path": executable,
            "stderr": None,
            "stdout": None,
        }

    return {
        "available": True,
        "command": command,
        "error": None,
        "exitCode": completed.returncode,
        "path": executable,
        "stderr": trim_output(completed.stderr),
        "stdout": trim_output(completed.stdout),
    }


def trim_output(output: str) -> str | None:
    trimmed = output.strip()
    if not trimmed:
        return None
    if len(trimmed) <= COMMAND_OUTPUT_LIMIT:
        return trimmed
    return f"{trimmed[:COMMAND_OUTPUT_LIMIT]}... [truncated]"


def emit_response(*, success: bool, message: str, data: Any) -> int:
    payload = {
        "success": success,
        "message": message,
        "data": data,
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
