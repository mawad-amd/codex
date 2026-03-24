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

    if tool in {GPU_PROFILE_TOOL, GPU_INSPECT_TOOL, GPU_VALIDATE_TOOL}:
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
