use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use std::path::Path;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;
use tokio::process::Command;
use tokio::time::timeout;

pub(crate) const GPU_INSPECT_TOOL_NAME: &str = "gpu_inspect";
pub(crate) const GPU_INVENTORY_TOOL_NAME: &str = "gpu_inventory";
pub(crate) const GPU_LIST_METRICS_TOOL_NAME: &str = "gpu_list_metrics";
pub(crate) const GPU_PROFILE_TOOL_NAME: &str = "gpu_profile";
pub(crate) const GPU_VALIDATE_TOOL_NAME: &str = "gpu_validate";

const CODEX_INTELLIKIT_BRIDGE_MODULE_ENV_VAR: &str = "CODEX_INTELLIKIT_BRIDGE_MODULE";
const CODEX_INTELLIKIT_BRIDGE_SCRIPT_ENV_VAR: &str = "CODEX_INTELLIKIT_BRIDGE_SCRIPT";
const CODEX_INTELLIKIT_PYTHON_ENV_VAR: &str = "CODEX_INTELLIKIT_PYTHON";
const CODEX_INTELLIKIT_ROOT_ENV_VAR: &str = "CODEX_INTELLIKIT_ROOT";
const DEFAULT_BRIDGE_SCRIPT: &str = "scripts/intellikit_codex_bridge.py";
const BRIDGE_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub(crate) enum IntelliKitTool {
    Inventory,
    ListMetrics,
    Profile,
    Inspect,
    Validate,
}

impl IntelliKitTool {
    pub(crate) fn from_tool_name(tool_name: &str) -> Option<Self> {
        match tool_name {
            GPU_INVENTORY_TOOL_NAME => Some(Self::Inventory),
            GPU_LIST_METRICS_TOOL_NAME => Some(Self::ListMetrics),
            GPU_PROFILE_TOOL_NAME => Some(Self::Profile),
            GPU_INSPECT_TOOL_NAME => Some(Self::Inspect),
            GPU_VALIDATE_TOOL_NAME => Some(Self::Validate),
            _ => None,
        }
    }

    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Inventory => GPU_INVENTORY_TOOL_NAME,
            Self::ListMetrics => GPU_LIST_METRICS_TOOL_NAME,
            Self::Profile => GPU_PROFILE_TOOL_NAME,
            Self::Inspect => GPU_INSPECT_TOOL_NAME,
            Self::Validate => GPU_VALIDATE_TOOL_NAME,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct IntelliKitRequest {
    pub(crate) tool: IntelliKitTool,
    pub(crate) arguments: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct IntelliKitResponse {
    pub(crate) message: String,
    pub(crate) success: bool,
}

#[derive(Debug, Clone, Default)]
pub(crate) struct IntelliKitRuntime {
    bridge_module: Option<String>,
    bridge_script: Option<PathBuf>,
    intellikit_root: Option<PathBuf>,
    python: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq)]
struct BridgeConfig {
    bridge_target: BridgeTarget,
    intellikit_root: Option<PathBuf>,
    python: PathBuf,
}

#[derive(Debug, Clone, PartialEq)]
enum BridgeTarget {
    Module(String),
    Script(PathBuf),
}

#[derive(Debug, Serialize)]
struct BridgeRequest<'a> {
    arguments: &'a Value,
    tool: &'a str,
}

#[derive(Debug, Deserialize)]
struct BridgeResponse {
    data: Option<Value>,
    message: Option<String>,
    success: bool,
}

impl IntelliKitRuntime {
    pub(crate) fn from_environment() -> Self {
        Self {
            bridge_module: read_trimmed_env(CODEX_INTELLIKIT_BRIDGE_MODULE_ENV_VAR),
            bridge_script: std::env::var_os(CODEX_INTELLIKIT_BRIDGE_SCRIPT_ENV_VAR)
                .map(PathBuf::from),
            intellikit_root: std::env::var_os(CODEX_INTELLIKIT_ROOT_ENV_VAR).map(PathBuf::from),
            python: std::env::var_os(CODEX_INTELLIKIT_PYTHON_ENV_VAR).map(PathBuf::from),
        }
    }

    pub(crate) async fn invoke(&self, request: IntelliKitRequest) -> IntelliKitResponse {
        match self.resolve_bridge_config() {
            Ok(config) => match invoke_bridge(&config, &request).await {
                Ok(response) => response,
                Err(message) => IntelliKitResponse {
                    message,
                    success: false,
                },
            },
            Err(message) => IntelliKitResponse {
                message: format!(
                    "The native IntelliKit runtime is not available for `{}`. {message}",
                    request.tool.as_str()
                ),
                success: false,
            },
        }
    }

    fn resolve_bridge_config(&self) -> Result<BridgeConfig, String> {
        let python = match self.python.clone() {
            Some(path) => path,
            None => which::which("python3")
                .or_else(|_| which::which("python"))
                .map_err(|_| {
                    format!(
                        "No Python interpreter was found. Set {CODEX_INTELLIKIT_PYTHON_ENV_VAR} or install `python3`."
                    )
                })?,
        };

        let bridge_target = if let Some(script) = self.bridge_script.clone() {
            BridgeTarget::Script(resolve_script_path(
                &script,
                self.intellikit_root.as_deref(),
            ))
        } else if let Some(module) = self.bridge_module.clone() {
            BridgeTarget::Module(module)
        } else if let Some(script) = default_bridge_script() {
            BridgeTarget::Script(script)
        } else {
            return Err(format!(
                "No IntelliKit bridge target was found. Set {CODEX_INTELLIKIT_BRIDGE_SCRIPT_ENV_VAR} to a bridge script or {CODEX_INTELLIKIT_BRIDGE_MODULE_ENV_VAR} to an importable Python module."
            ));
        };

        Ok(BridgeConfig {
            bridge_target,
            intellikit_root: self.intellikit_root.clone(),
            python,
        })
    }
}

async fn invoke_bridge(
    config: &BridgeConfig,
    request: &IntelliKitRequest,
) -> Result<IntelliKitResponse, String> {
    let request_json = serde_json::to_string(&BridgeRequest {
        tool: request.tool.as_str(),
        arguments: &request.arguments,
    })
    .map_err(|err| format!("failed to serialize IntelliKit request: {err}"))?;

    let mut command = Command::new(&config.python);
    command
        .stdin(Stdio::null())
        .stderr(Stdio::piped())
        .stdout(Stdio::piped());

    match &config.bridge_target {
        BridgeTarget::Module(module) => {
            command.arg("-m").arg(module);
        }
        BridgeTarget::Script(path) => {
            command.arg(path);
        }
    }

    command.arg("--request-json").arg(&request_json);

    if let Some(root) = &config.intellikit_root {
        command.current_dir(root);
    }

    let output = timeout(BRIDGE_TIMEOUT, command.output())
        .await
        .map_err(|_| {
            format!(
                "IntelliKit bridge timed out after {} seconds while running `{}`.",
                BRIDGE_TIMEOUT.as_secs(),
                request.tool.as_str()
            )
        })?
        .map_err(|err| format!("failed to launch IntelliKit bridge: {err}"))?;

    if !output.status.success() {
        return Err(format_process_failure(
            request.tool,
            output.status,
            &output.stdout,
            &output.stderr,
        ));
    }

    parse_bridge_response(&output.stdout)
}

fn format_process_failure(
    tool: IntelliKitTool,
    status: std::process::ExitStatus,
    stdout: &[u8],
    stderr: &[u8],
) -> String {
    let stdout = String::from_utf8_lossy(stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(stderr).trim().to_string();
    let mut details = vec![format!(
        "IntelliKit bridge failed for `{}` with status {status}.",
        tool.as_str()
    )];

    if !stderr.is_empty() {
        details.push(format!("stderr: {stderr}"));
    }
    if !stdout.is_empty() {
        details.push(format!("stdout: {stdout}"));
    }

    details.join(" ")
}

fn parse_bridge_response(stdout: &[u8]) -> Result<IntelliKitResponse, String> {
    let response: BridgeResponse = serde_json::from_slice(stdout).map_err(|err| {
        let stdout = String::from_utf8_lossy(stdout).trim().to_string();
        if stdout.is_empty() {
            format!("IntelliKit bridge returned invalid JSON: {err}")
        } else {
            format!("IntelliKit bridge returned invalid JSON: {err}. stdout: {stdout}")
        }
    })?;

    let mut sections = Vec::new();
    if let Some(message) = response
        .message
        .as_deref()
        .map(str::trim)
        .filter(|message| !message.is_empty())
    {
        sections.push(message.to_string());
    }
    if let Some(data) = response.data {
        let data = serde_json::to_string_pretty(&data)
            .map_err(|err| format!("failed to render IntelliKit response data: {err}"))?;
        sections.push(data);
    }

    let message = if sections.is_empty() {
        match response.success {
            true => "IntelliKit bridge completed successfully.".to_string(),
            false => "IntelliKit bridge reported a failure without a message.".to_string(),
        }
    } else {
        sections.join("\n\n")
    };

    Ok(IntelliKitResponse {
        message,
        success: response.success,
    })
}

fn read_trimmed_env(key: &str) -> Option<String> {
    let value = std::env::var(key).ok()?;
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

fn default_bridge_script() -> Option<PathBuf> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../")
        .join(DEFAULT_BRIDGE_SCRIPT);
    path.is_file().then_some(path)
}

fn resolve_script_path(script: &Path, intellikit_root: Option<&Path>) -> PathBuf {
    if script.is_absolute() {
        return script.to_path_buf();
    }

    match intellikit_root {
        Some(root) => root.join(script),
        None => script.to_path_buf(),
    }
}

#[cfg(test)]
mod tests;
