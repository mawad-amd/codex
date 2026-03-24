use serde_json::Value;
use std::path::PathBuf;

pub(crate) const GPU_INSPECT_TOOL_NAME: &str = "gpu_inspect";
pub(crate) const GPU_INVENTORY_TOOL_NAME: &str = "gpu_inventory";
pub(crate) const GPU_PROFILE_TOOL_NAME: &str = "gpu_profile";
pub(crate) const GPU_VALIDATE_TOOL_NAME: &str = "gpu_validate";

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub(crate) enum IntelliKitTool {
    Inventory,
    Profile,
    Inspect,
    Validate,
}

impl IntelliKitTool {
    pub(crate) fn from_tool_name(tool_name: &str) -> Option<Self> {
        match tool_name {
            GPU_INVENTORY_TOOL_NAME => Some(Self::Inventory),
            GPU_PROFILE_TOOL_NAME => Some(Self::Profile),
            GPU_INSPECT_TOOL_NAME => Some(Self::Inspect),
            GPU_VALIDATE_TOOL_NAME => Some(Self::Validate),
            _ => None,
        }
    }

    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Inventory => GPU_INVENTORY_TOOL_NAME,
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
    sidecar_path: Option<PathBuf>,
}

impl IntelliKitRuntime {
    pub(crate) fn from_environment() -> Self {
        Self {
            sidecar_path: std::env::var_os("CODEX_INTELLIKIT_SIDECAR").map(PathBuf::from),
        }
    }

    pub(crate) async fn invoke(&self, request: IntelliKitRequest) -> IntelliKitResponse {
        let configured_sidecar = self
            .sidecar_path
            .as_ref()
            .map(|path| format!("Configured sidecar path: {}.", path.display()))
            .unwrap_or_else(|| {
                "No IntelliKit sidecar is configured; set CODEX_INTELLIKIT_SIDECAR once the bridge exists.".to_string()
            });

        IntelliKitResponse {
            message: format!(
                "The native IntelliKit GPU integration is scaffolded, but the execution bridge is not implemented yet. Requested tool: {}. {configured_sidecar}",
                request.tool.as_str()
            ),
            success: false,
        }
    }
}
