use crate::function_tool::FunctionCallError;
use crate::intellikit::IntelliKitRequest;
use crate::intellikit::IntelliKitRuntime;
use crate::intellikit::IntelliKitTool;
use crate::tools::context::FunctionToolOutput;
use crate::tools::context::ToolInvocation;
use crate::tools::context::ToolPayload;
use crate::tools::handlers::parse_arguments;
use crate::tools::registry::ToolHandler;
use crate::tools::registry::ToolKind;
use async_trait::async_trait;
use serde::Deserialize;
use serde_json::Value;

pub struct IntelliKitHandler;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GpuInventoryArgs {}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GpuProfileArgs {
    target: String,
    #[serde(default)]
    workload: Option<String>,
    #[serde(default)]
    objective: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GpuInspectArgs {
    #[serde(default)]
    artifact_id: Option<String>,
    focus: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GpuValidateArgs {
    target: String,
    #[serde(default)]
    baseline_artifact_id: Option<String>,
    #[serde(default)]
    expectation: Option<String>,
}

#[async_trait]
impl ToolHandler for IntelliKitHandler {
    type Output = FunctionToolOutput;

    fn kind(&self) -> ToolKind {
        ToolKind::Function
    }

    async fn handle(&self, invocation: ToolInvocation) -> Result<Self::Output, FunctionCallError> {
        let ToolInvocation {
            tool_name, payload, ..
        } = invocation;

        let arguments = match payload {
            ToolPayload::Function { arguments } => arguments,
            _ => {
                return Err(FunctionCallError::RespondToModel(
                    "intellikit handler received unsupported payload".to_string(),
                ));
            }
        };

        let arguments: Value = parse_arguments(&arguments)?;
        let request = request_from_tool_name(&tool_name, arguments)?;
        let runtime = IntelliKitRuntime::from_environment();
        let response = runtime.invoke(request).await;

        Ok(FunctionToolOutput::from_text(
            response.message,
            Some(response.success),
        ))
    }
}

fn request_from_tool_name(
    tool_name: &str,
    arguments: Value,
) -> Result<IntelliKitRequest, FunctionCallError> {
    let Some(tool) = IntelliKitTool::from_tool_name(tool_name) else {
        return Err(FunctionCallError::RespondToModel(format!(
            "unknown intellikit tool: {tool_name}"
        )));
    };

    validate_arguments(tool, &arguments)?;

    Ok(IntelliKitRequest { tool, arguments })
}

fn validate_arguments(tool: IntelliKitTool, arguments: &Value) -> Result<(), FunctionCallError> {
    match tool {
        IntelliKitTool::Inventory => {
            serde_json::from_value::<GpuInventoryArgs>(arguments.clone())
                .map_err(invalid_arguments)?;
        }
        IntelliKitTool::Profile => {
            let GpuProfileArgs {
                target,
                workload,
                objective,
            } = serde_json::from_value::<GpuProfileArgs>(arguments.clone())
                .map_err(invalid_arguments)?;
            let _ = (target, workload, objective);
        }
        IntelliKitTool::Inspect => {
            let GpuInspectArgs { artifact_id, focus } =
                serde_json::from_value::<GpuInspectArgs>(arguments.clone())
                    .map_err(invalid_arguments)?;
            let _ = (artifact_id, focus);
        }
        IntelliKitTool::Validate => {
            let GpuValidateArgs {
                target,
                baseline_artifact_id,
                expectation,
            } = serde_json::from_value::<GpuValidateArgs>(arguments.clone())
                .map_err(invalid_arguments)?;
            let _ = (target, baseline_artifact_id, expectation);
        }
    }

    Ok(())
}

fn invalid_arguments(err: serde_json::Error) -> FunctionCallError {
    FunctionCallError::RespondToModel(format!("invalid intellikit tool arguments: {err}"))
}
