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
import contextlib
import importlib.metadata
import importlib.util
import io
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
GPU_LIST_METRICS_TOOL = "gpu_list_metrics"
GPU_PROFILE_TOOL = "gpu_profile"
GPU_VALIDATE_TOOL = "gpu_validate"
KNOWN_TOOLS = {
    GPU_INVENTORY_TOOL,
    GPU_INSPECT_TOOL,
    GPU_LIST_METRICS_TOOL,
    GPU_PROFILE_TOOL,
    GPU_VALIDATE_TOOL,
}
COMMAND_TIMEOUT_SECONDS = 5
COMMAND_OUTPUT_LIMIT = 4000
PROFILE_TIMEOUT_SECONDS = 300
PROFILE_KERNEL_LIMIT = 10
DEFAULT_ARTIFACT_DIR_NAME = "codex-intellikit-artifacts"
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

    if tool == GPU_LIST_METRICS_TOOL:
        try:
            message, data = handle_gpu_list_metrics()
        except Exception as err:
            return emit_response(
                success=False,
                message=f"`{tool}` failed: {err}",
                data={
                    "tool": tool,
                    "metrix": probe_python_module("metrix"),
                },
            )
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

    if tool == GPU_INSPECT_TOOL:
        error = validate_gpu_inspect_arguments(arguments)
        if error is not None:
            return emit_response(
                success=False,
                message=error,
                data={"tool": tool, "arguments": arguments},
            )
        try:
            message, data = handle_gpu_inspect(arguments)
        except Exception as err:  # pragma: no cover - exercised in GPU environment
            return emit_response(
                success=False,
                message=f"`{tool}` failed: {err}",
                data={"tool": tool, "arguments": arguments},
            )
        return emit_response(success=True, message=message, data=data)

    if tool == GPU_VALIDATE_TOOL:
        error = validate_gpu_validate_arguments(arguments)
        if error is not None:
            return emit_response(
                success=False,
                message=error,
                data={"tool": tool, "arguments": arguments},
            )
        try:
            message, data = handle_gpu_validate(arguments)
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
    target = arguments.get("command")
    if not isinstance(target, str) or not target.strip():
        return "Request field `arguments.target` must be a non-empty string for `gpu_profile`."

    for key in ("workload", "objective"):
        value = arguments.get(key)
        if value is not None and not isinstance(value, str):
            return f"Request field `arguments.{key}` must be a string when provided."

    return None


def validate_gpu_inspect_arguments(arguments: dict[str, Any]) -> str | None:
    focus = arguments.get("focus")
    if not isinstance(focus, str) or not focus.strip():
        return "Request field `arguments.focus` must be a non-empty string for `gpu_inspect`."

    artifact_id = arguments.get("artifact_id")
    if artifact_id is not None and not isinstance(artifact_id, str):
        return "Request field `arguments.artifact_id` must be a string when provided."

    return None


def validate_gpu_validate_arguments(arguments: dict[str, Any]) -> str | None:
    target = arguments.get("command")
    if not isinstance(target, str) or not target.strip():
        return "Request field `arguments.target` must be a non-empty string for `gpu_validate`."

    for key in ("baseline_artifact_id", "expectation"):
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


def handle_gpu_list_metrics() -> tuple[str, dict[str, Any]]:
    try:
        from metrix import Metrix
        from metrix.metrics import METRIC_CATALOG
    except Exception as err:
        raise RuntimeError(
            "Metrix is not importable in this interpreter. Install the IntelliKit "
            "`metrix` package with its runtime dependencies before using "
            f"`gpu_list_metrics`. Original error: {err}"
        ) from err

    try:
        profiler = Metrix()
        metrics = sorted(profiler.list_metrics())
    except (RuntimeError, Exception):
        metrics = sorted(METRIC_CATALOG.keys())

    by_category: dict[str, list[str]] = {}
    for name in metrics:
        meta = METRIC_CATALOG.get(name)
        if meta is not None:
            cat = meta["category"].value
        else:
            cat = name.split(".", 1)[0] if "." in name else "other"
        by_category.setdefault(cat, []).append(name)

    message = (
        f"Found {len(metrics)} available GPU performance metrics across "
        f"{len(by_category)} categories. Use these metric names with gpu_profile."
    )
    data = {
        "metrics": metrics,
        "byCategory": by_category,
        "note": "Pass these metric names to gpu_profile to collect specific metrics.",
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

    target = arguments["command"].strip()
    workload = arguments.get("workload")
    objective = arguments.get("objective")
    command = build_profile_command(target, workload)
    selected_profile, time_only, rationale = select_profile_mode(objective)
    timeout_seconds = int(
        os.environ.get("CODEX_INTELLIKIT_PROFILE_TIMEOUT", PROFILE_TIMEOUT_SECONDS)
    )

    started = time.time()
    metrix_logs = capture_python_output(
        lambda: run_metrix_profile(
            Metrix,
            command,
            selected_profile,
            time_only,
            timeout_seconds,
        )
    )
    profiler = metrix_logs["profiler"]
    results = metrix_logs["results"]
    elapsed_seconds = time.time() - started
    kernels = sort_profiled_kernels(results.kernels)
    displayed_kernels = kernels[:PROFILE_KERNEL_LIMIT]
    mode = "time-only mode" if time_only else f"profile `{selected_profile}`"
    available_profiles = profiler.list_profiles()

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

    artifact_payload = build_profile_artifact(
        command=command,
        target=target,
        workload=workload,
        objective=objective,
        arch=profiler.arch,
        available_profiles=available_profiles,
        selected_profile=selected_profile,
        time_only=time_only,
        rationale=rationale,
        timeout_seconds=timeout_seconds,
        elapsed_seconds=elapsed_seconds,
        kernels=kernels,
    )
    artifact_path = write_profile_artifact(artifact_payload)
    artifact_id = artifact_path.stem

    data = {
        "command": command,
        "target": target,
        "workload": workload,
        "objective": objective,
        "arch": profiler.arch,
        "elapsedSeconds": round(elapsed_seconds, 3),
        "availableProfiles": available_profiles,
        "selectedMode": {
            "profile": selected_profile,
            "timeOnly": time_only,
            "reason": rationale,
            "timeoutSeconds": timeout_seconds,
        },
        "kernelCount": results.total_kernels,
        "kernels": [serialize_kernel_results(kernel) for kernel in displayed_kernels],
        "truncatedKernelCount": max(0, len(kernels) - len(displayed_kernels)),
        "artifact": {
            "artifactId": artifact_id,
            "path": str(artifact_path),
        },
        "bridgeLogs": {
            "stderr": metrix_logs["stderr"] or None,
            "stdout": metrix_logs["stdout"] or None,
        },
        "metrix": probe_python_module("metrix"),
        "rocprofv3": probe_command("rocprofv3", ["--version"]),
    }
    return message, data


def run_metrix_profile(
    metrix_cls: Any,
    command: str,
    selected_profile: str | None,
    time_only: bool,
    timeout_seconds: int,
) -> tuple[Any, Any]:
    profiler = metrix_cls()
    results = profiler.profile(
        command=command,
        profile=selected_profile,
        time_only=time_only,
        num_replays=1,
        aggregate_by_kernel=True,
        timeout_seconds=timeout_seconds,
    )
    return profiler, results


def capture_python_output(callback: Any) -> dict[str, Any]:
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(
        stderr_buffer
    ):
        profiler, results = callback()
    return {
        "profiler": profiler,
        "results": results,
        "stdout": stdout_buffer.getvalue().strip(),
        "stderr": stderr_buffer.getvalue().strip(),
    }


def handle_gpu_inspect(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    artifact = load_profile_artifact(arguments.get("artifact_id"))
    focus = arguments["focus"].strip()
    matched_kernels = select_inspection_kernels(artifact["kernels"], focus)
    used_fallback = not matched_kernels
    displayed_kernels = (
        artifact["kernels"][:PROFILE_KERNEL_LIMIT] if used_fallback else matched_kernels
    )

    if used_fallback:
        message = (
            f"GPU inspect loaded artifact `{artifact['artifactId']}`, but no kernels matched "
            f"focus `{focus}` directly, so it returned the hottest kernels instead."
        )
    else:
        message = (
            f"GPU inspect loaded artifact `{artifact['artifactId']}` and matched "
            f"{len(matched_kernels)} kernels for focus `{focus}`."
        )
    data = {
        "artifact": {
            "artifactId": artifact["artifactId"],
            "path": artifact["path"],
            "createdAt": artifact["createdAt"],
            "command": artifact["command"],
            "arch": artifact["arch"],
        },
        "focus": focus,
        "summary": summarize_focus(
            artifact,
            focus,
            displayed_kernels,
            len(matched_kernels),
            used_fallback,
        ),
        "kernels": displayed_kernels,
        "truncatedKernelCount": max(0, len(matched_kernels) - PROFILE_KERNEL_LIMIT),
    }
    return message, data


def handle_gpu_validate(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    target = arguments["command"].strip()
    expectation = arguments.get("expectation")
    baseline_artifact_id = arguments.get("baseline_artifact_id")
    selected_profile, time_only, rationale = select_profile_mode(expectation)
    profile_message, profile_data = handle_gpu_profile(
        {
            "target": target,
            "objective": expectation,
        }
    )
    del profile_message

    baseline_artifact = (
        load_profile_artifact(baseline_artifact_id) if baseline_artifact_id else None
    )
    current_artifact = load_profile_artifact(profile_data["artifact"]["artifactId"])
    validation = compare_artifacts(
        baseline_artifact,
        current_artifact,
        expectation,
        selected_profile,
        time_only,
        rationale,
    )

    if baseline_artifact is None:
        message = (
            "GPU validate collected a current profile, but no baseline artifact was provided, "
            "so the result only summarizes the current run."
        )
    else:
        message = (
            f"GPU validate compared `{current_artifact['artifactId']}` against "
            f"`{baseline_artifact['artifactId']}`."
        )

    data = {
        "target": target,
        "expectation": expectation,
        "currentArtifact": {
            "artifactId": current_artifact["artifactId"],
            "path": current_artifact["path"],
        },
        "baselineArtifact": None
        if baseline_artifact is None
        else {
            "artifactId": baseline_artifact["artifactId"],
            "path": baseline_artifact["path"],
        },
        "validation": validation,
        "currentProfile": profile_data,
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


def build_profile_artifact(
    *,
    command: str,
    target: str,
    workload: str | None,
    objective: str | None,
    arch: str,
    available_profiles: list[str],
    selected_profile: str | None,
    time_only: bool,
    rationale: str,
    timeout_seconds: int,
    elapsed_seconds: float,
    kernels: list[Any],
) -> dict[str, Any]:
    artifact_id = f"profile-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    return {
        "artifactId": artifact_id,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "path": None,
        "command": command,
        "target": target,
        "workload": workload,
        "objective": objective,
        "arch": arch,
        "elapsedSeconds": round(elapsed_seconds, 3),
        "availableProfiles": available_profiles,
        "selectedMode": {
            "profile": selected_profile,
            "timeOnly": time_only,
            "reason": rationale,
            "timeoutSeconds": timeout_seconds,
        },
        "kernelCount": len(kernels),
        "kernels": [serialize_kernel_results(kernel) for kernel in kernels],
    }


def artifact_directory() -> Path:
    configured = os.environ.get("CODEX_INTELLIKIT_ARTIFACT_DIR")
    if configured:
        return Path(configured).expanduser()
    root = os.environ.get("CODEX_INTELLIKIT_ROOT")
    if root:
        return Path(root).expanduser() / DEFAULT_ARTIFACT_DIR_NAME
    return Path.cwd() / DEFAULT_ARTIFACT_DIR_NAME


def write_profile_artifact(payload: dict[str, Any]) -> Path:
    directory = artifact_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{payload['artifactId']}.json"
    payload["path"] = str(path)
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_profile_artifact(artifact_id: str | None) -> dict[str, Any]:
    directory = artifact_directory()
    if artifact_id is None or not artifact_id.strip():
        candidates = sorted(directory.glob("profile-*.json"))
        if not candidates:
            raise RuntimeError(
                f"No profiling artifacts were found in `{directory}` for `gpu_inspect`."
            )
        path = candidates[-1]
    else:
        path = directory / f"{artifact_id.strip()}.json"
        if not path.is_file():
            raise RuntimeError(f"Profiling artifact `{artifact_id}` was not found at `{path}`.")

    payload = json.loads(path.read_text())
    payload["path"] = str(path)
    return payload


def select_inspection_kernels(
    kernels: list[dict[str, Any]], focus: str
) -> list[dict[str, Any]]:
    normalized = focus.lower()
    matched = []
    for kernel in kernels:
        if normalized in kernel["name"].lower():
            matched.append(kernel)
            continue
        metric_names = " ".join(kernel["metrics"].keys()).lower()
        if normalized in metric_names:
            matched.append(kernel)
            continue
        if normalized in {"latency", "duration", "time"}:
            matched.append(kernel)
            continue
        if normalized in {"memory", "bandwidth", "cache", "compute"} and any(
            normalized in metric_name.lower() for metric_name in kernel["metrics"].keys()
        ):
            matched.append(kernel)
    return matched


def summarize_focus(
    artifact: dict[str, Any],
    focus: str,
    displayed_kernels: list[dict[str, Any]],
    matched_kernel_count: int,
    used_fallback: bool,
) -> dict[str, Any]:
    top_kernel = displayed_kernels[0] if displayed_kernels else None
    return {
        "artifactId": artifact["artifactId"],
        "focus": focus,
        "usedFallback": used_fallback,
        "matchedKernelCount": matched_kernel_count,
        "displayedKernelCount": len(displayed_kernels),
        "topKernel": None if top_kernel is None else top_kernel["name"],
        "topKernelDurationUs": None
        if top_kernel is None
        else top_kernel["durationUs"]["avg"],
    }


def compare_artifacts(
    baseline_artifact: dict[str, Any] | None,
    current_artifact: dict[str, Any],
    expectation: str | None,
    selected_profile: str | None,
    time_only: bool,
    rationale: str,
) -> dict[str, Any]:
    if baseline_artifact is None:
        return {
            "mode": {
                "profile": selected_profile,
                "timeOnly": time_only,
                "reason": rationale,
            },
            "status": "no_baseline",
            "summary": "No baseline artifact was provided.",
            "topKernel": current_artifact["kernels"][0] if current_artifact["kernels"] else None,
        }

    baseline_by_name = {kernel["name"]: kernel for kernel in baseline_artifact["kernels"]}
    current_by_name = {kernel["name"]: kernel for kernel in current_artifact["kernels"]}
    kernel_name = next(
        (
            kernel["name"]
            for kernel in current_artifact["kernels"]
            if kernel["name"] in baseline_by_name
        ),
        current_artifact["kernels"][0]["name"] if current_artifact["kernels"] else None,
    )

    if kernel_name is None:
        return {
            "mode": {
                "profile": selected_profile,
                "timeOnly": time_only,
                "reason": rationale,
            },
            "status": "no_kernels",
            "summary": "Neither artifact contained kernels to compare.",
        }

    baseline_kernel = baseline_by_name.get(kernel_name)
    current_kernel = current_by_name.get(kernel_name)
    duration_delta = compute_delta(
        baseline_kernel["durationUs"]["avg"] if baseline_kernel else None,
        current_kernel["durationUs"]["avg"] if current_kernel else None,
    )
    metric_name = select_validation_metric(expectation, current_kernel, baseline_kernel)
    metric_delta = None
    if metric_name and baseline_kernel and current_kernel:
        metric_delta = compute_delta(
            baseline_kernel["metrics"].get(metric_name, {}).get("avg"),
            current_kernel["metrics"].get(metric_name, {}).get("avg"),
        )
    evaluation = evaluate_expectation(expectation, duration_delta, metric_name, metric_delta)
    return {
        "mode": {
            "profile": selected_profile,
            "timeOnly": time_only,
            "reason": rationale,
        },
        "status": evaluation["status"],
        "summary": evaluation["summary"],
        "kernel": kernel_name,
        "durationDelta": duration_delta,
        "metric": metric_name,
        "metricDelta": metric_delta,
    }


def select_validation_metric(
    expectation: str | None,
    current_kernel: dict[str, Any] | None,
    baseline_kernel: dict[str, Any] | None,
) -> str | None:
    del baseline_kernel
    if current_kernel is None:
        return None

    normalized = "" if expectation is None else expectation.lower()
    if "bandwidth" in normalized:
        return "memory.hbm_bandwidth_utilization"
    if "cache" in normalized:
        return "memory.l2_hit_rate"
    if any(keyword in normalized for keyword in ("compute", "throughput", "flop")):
        for name in ("compute.hbm_gflops", "compute.total_flops"):
            if name in current_kernel["metrics"]:
                return name
    return None


def compute_delta(baseline: float | None, current: float | None) -> dict[str, Any] | None:
    if baseline is None or current is None:
        return None
    delta = current - baseline
    percent = None if baseline == 0 else (delta / baseline) * 100.0
    return {
        "baseline": baseline,
        "current": current,
        "absolute": delta,
        "percent": percent,
    }


def evaluate_expectation(
    expectation: str | None,
    duration_delta: dict[str, Any] | None,
    metric_name: str | None,
    metric_delta: dict[str, Any] | None,
) -> dict[str, Any]:
    if expectation is None or not expectation.strip():
        return {
            "status": "informational",
            "summary": "Collected a current profile and compared it to the baseline artifact.",
        }

    normalized = expectation.lower()
    if any(keyword in normalized for keyword in ("faster", "lower latency", "improve")):
        passed = duration_delta is not None and duration_delta["absolute"] < 0
        return {
            "status": "passed" if passed else "failed",
            "summary": "Expectation targeted lower runtime latency."
            if passed
            else "Expected lower runtime latency, but the current run was not faster.",
        }
    if any(keyword in normalized for keyword in ("regression", "slower", "higher latency")):
        passed = duration_delta is not None and duration_delta["absolute"] > 0
        return {
            "status": "passed" if passed else "failed",
            "summary": "Expectation targeted a runtime regression signal."
            if passed
            else "Expected a runtime regression signal, but the current run was not slower.",
        }
    if metric_name and metric_delta is not None:
        expects_higher = any(
            keyword in normalized for keyword in ("higher", "more", "better", "increase")
        )
        passed = metric_delta["absolute"] > 0 if expects_higher else metric_delta["absolute"] < 0
        direction = "higher" if expects_higher else "lower"
        return {
            "status": "passed" if passed else "failed",
            "summary": f"Expectation targeted {direction} `{metric_name}`.",
        }
    return {
        "status": "informational",
        "summary": f"Could not map expectation `{expectation}` onto a strict pass/fail rule.",
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
