use super::*;
use pretty_assertions::assert_eq;
use serde_json::json;
use serial_test::serial;
use std::env;
use std::ffi::OsStr;
use std::fs;
use tempfile::tempdir;

struct EnvVarGuard {
    key: &'static str,
    original: Option<std::ffi::OsString>,
}

impl EnvVarGuard {
    fn set(key: &'static str, value: &OsStr) -> Self {
        let original = env::var_os(key);
        unsafe {
            env::set_var(key, value);
        }
        Self { key, original }
    }

    fn remove(key: &'static str) -> Self {
        let original = env::var_os(key);
        unsafe {
            env::remove_var(key);
        }
        Self { key, original }
    }
}

impl Drop for EnvVarGuard {
    fn drop(&mut self) {
        match self.original.take() {
            Some(value) => unsafe {
                env::set_var(self.key, value);
            },
            None => unsafe {
                env::remove_var(self.key);
            },
        }
    }
}

#[test]
fn parse_bridge_response_formats_message_and_data() {
    let response = parse_bridge_response(
        br#"{"success":true,"message":"inventory ready","data":{"gpuCount":2}}"#,
    )
    .expect("response should parse");

    assert_eq!(response.success, true);
    assert_eq!(
        response.message,
        "inventory ready\n\n{\n  \"gpuCount\": 2\n}".to_string()
    );
}

#[test]
#[serial(intellikit_env)]
fn resolve_bridge_config_prefers_script_and_explicit_python() {
    let _python = EnvVarGuard::set(CODEX_INTELLIKIT_PYTHON_ENV_VAR, OsStr::new("/tmp/python3"));
    let _script = EnvVarGuard::set(
        CODEX_INTELLIKIT_BRIDGE_SCRIPT_ENV_VAR,
        OsStr::new("bridges/codex_bridge.py"),
    );
    let _root = EnvVarGuard::set(CODEX_INTELLIKIT_ROOT_ENV_VAR, OsStr::new("/opt/intellikit"));
    let _module = EnvVarGuard::set(
        CODEX_INTELLIKIT_BRIDGE_MODULE_ENV_VAR,
        OsStr::new("ignored.module"),
    );

    let runtime = IntelliKitRuntime::from_environment();
    let config = runtime
        .resolve_bridge_config()
        .expect("bridge config should resolve");

    assert_eq!(config.python, PathBuf::from("/tmp/python3"));
    assert_eq!(
        config.intellikit_root,
        Some(PathBuf::from("/opt/intellikit"))
    );
    assert_eq!(
        config.bridge_target,
        BridgeTarget::Script(PathBuf::from("/opt/intellikit/bridges/codex_bridge.py"))
    );
}

#[test]
#[serial(intellikit_env)]
fn resolve_bridge_config_uses_explicit_module_when_set() {
    let _python = EnvVarGuard::set(CODEX_INTELLIKIT_PYTHON_ENV_VAR, OsStr::new("/tmp/python3"));
    let _script = EnvVarGuard::remove(CODEX_INTELLIKIT_BRIDGE_SCRIPT_ENV_VAR);
    let _root = EnvVarGuard::remove(CODEX_INTELLIKIT_ROOT_ENV_VAR);
    let _module = EnvVarGuard::set(
        CODEX_INTELLIKIT_BRIDGE_MODULE_ENV_VAR,
        OsStr::new("custom.intellikit_bridge"),
    );

    let runtime = IntelliKitRuntime::from_environment();
    let config = runtime
        .resolve_bridge_config()
        .expect("bridge config should resolve");

    assert_eq!(config.python, PathBuf::from("/tmp/python3"));
    assert_eq!(config.intellikit_root, None);
    assert_eq!(
        config.bridge_target,
        BridgeTarget::Module("custom.intellikit_bridge".to_string())
    );
}

#[tokio::test]
#[serial(intellikit_env)]
async fn invoke_runs_bridge_script() {
    let Some(python) = which::which("python3")
        .ok()
        .or_else(|| which::which("python").ok())
    else {
        return;
    };

    let temp = tempdir().expect("tempdir");
    let script_path = temp.path().join("bridge.py");
    fs::write(
        &script_path,
        r#"import json
import sys

request_json = None
args = sys.argv[1:]
for index, arg in enumerate(args):
    if arg == "--request-json":
        request_json = args[index + 1]
        break

request = json.loads(request_json)
print(json.dumps({
    "success": True,
    "message": f"handled {request['tool']}",
    "data": {"tool": request["tool"], "arguments": request["arguments"]},
}))
"#,
    )
    .expect("write bridge script");

    let _python = EnvVarGuard::set(CODEX_INTELLIKIT_PYTHON_ENV_VAR, python.as_os_str());
    let _script = EnvVarGuard::set(
        CODEX_INTELLIKIT_BRIDGE_SCRIPT_ENV_VAR,
        script_path.as_os_str(),
    );
    let _module = EnvVarGuard::remove(CODEX_INTELLIKIT_BRIDGE_MODULE_ENV_VAR);
    let _root = EnvVarGuard::remove(CODEX_INTELLIKIT_ROOT_ENV_VAR);

    let runtime = IntelliKitRuntime::from_environment();
    let response = runtime
        .invoke(IntelliKitRequest {
            tool: IntelliKitTool::Inventory,
            arguments: json!({}),
        })
        .await;

    assert_eq!(response.success, true);
    assert_eq!(
        response.message,
        "handled gpu_inventory\n\n{\n  \"arguments\": {},\n  \"tool\": \"gpu_inventory\"\n}"
            .to_string()
    );
}

#[test]
#[serial(intellikit_env)]
fn resolve_bridge_config_uses_bundled_script_by_default() {
    let _python = EnvVarGuard::set(
        CODEX_INTELLIKIT_PYTHON_ENV_VAR,
        OsStr::new("/path/that/does/not/exist/python3"),
    );
    let _script = EnvVarGuard::remove(CODEX_INTELLIKIT_BRIDGE_SCRIPT_ENV_VAR);
    let _module = EnvVarGuard::remove(CODEX_INTELLIKIT_BRIDGE_MODULE_ENV_VAR);
    let _root = EnvVarGuard::remove(CODEX_INTELLIKIT_ROOT_ENV_VAR);

    let runtime = IntelliKitRuntime::from_environment();
    let config = runtime
        .resolve_bridge_config()
        .expect("explicit python path should be accepted during config resolution");

    assert_eq!(
        config.python,
        PathBuf::from("/path/that/does/not/exist/python3")
    );
    assert_eq!(
        config.bridge_target,
        BridgeTarget::Script(default_bridge_script().expect("bundled bridge script should exist"))
    );
}

#[test]
fn extend_pythonpath_adds_src_layout_when_present() {
    let temp = tempdir().expect("tempdir");
    let src = temp.path().join("src");
    fs::create_dir(&src).expect("create src dir");

    let pythonpath = extend_pythonpath(temp.path()).expect("pythonpath should resolve");
    let paths: Vec<_> = std::env::split_paths(&pythonpath).collect();

    assert_eq!(paths, vec![temp.path().to_path_buf(), src]);
}
