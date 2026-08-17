//! Stable forensic snapshot assembly and incident-reason evaluation.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::Serialize;
use serde_json::Value;

use crate::command::{
    CommandPayload, ProcessPayload, command_output, matching_processes, read_json_object,
    tail_log_dir,
};
use crate::config::{BackendSelection, BackendSelectionPayload, ObserverConfig};
use crate::gateway::GatewayDiagnostics;
use crate::probe::{BackendProbe, BackendProbeResult};

const RUNTIME_LOG_DIR: &str = "/var/volatile/log/dbus-venus-evcharger";
const LOG_TAIL_BYTES: usize = 20_000;
const COMMAND_TIMEOUT: Duration = Duration::from_secs(3);
const TRACE_MARKERS: [&str; 5] = [
    "Traceback",
    "malloc()",
    "NoReply",
    "dbus down",
    "Watchdog recovery",
];

/// Stable gateway-diagnostics envelope in one forensic artifact.
#[derive(Clone, Debug, Serialize)]
#[serde(untagged)]
pub enum GatewayDiagnosticsPayload {
    /// Semantic gateway snapshot was available and valid.
    Available {
        /// Always true for this variant.
        available: bool,
        /// Validated semantic gateway snapshot.
        snapshot: Box<GatewayDiagnostics>,
    },
    /// Gateway file was unavailable or violated its schema.
    Unavailable {
        /// Always false for this variant.
        available: bool,
        /// Bounded read or contract error.
        error: String,
    },
}

/// Stable backend-selection envelope in one forensic artifact.
#[derive(Clone, Debug, Serialize)]
#[serde(untagged)]
pub enum BackendDiagnosticsPayload {
    /// Backend roles normalized successfully.
    Available {
        /// Always true for this variant.
        available: bool,
        /// Normalized role selection.
        selection: BackendSelectionPayload,
    },
    /// Backend configuration was invalid.
    Unavailable {
        /// Always false for this variant.
        available: bool,
        /// Stable backend configuration reason.
        reason_code: String,
        /// Bounded configuration detail.
        error: String,
    },
}

/// Complete bounded observer snapshot.
#[derive(Clone, Debug, Serialize)]
pub struct ForensicSnapshot {
    /// Epoch timestamp used for cooldown and incident naming.
    pub timestamp: f64,
    config_path: String,
    /// Semantic gateway diagnostics.
    pub gateway_diagnostics: GatewayDiagnosticsPayload,
    backend_diagnostics: BackendDiagnosticsPayload,
    backend_probe: BackendProbeResult,
    auto_input_snapshot: Value,
    runtime_state: Value,
    helper_processes: Vec<ProcessPayload>,
    /// runit status used as one incident signal.
    pub svstat: CommandPayload,
    ps: CommandPayload,
    uptime: CommandPayload,
    runtime_logs: BTreeMap<String, String>,
    /// Stable markers found in bounded runtime logs.
    pub trace_markers: Vec<String>,
}

impl ForensicSnapshot {
    /// Collect every bounded read-only source for one observer cycle.
    #[must_use]
    pub fn collect(config: &ObserverConfig) -> Self {
        let selection = config.backend_selection();
        let backend_diagnostics = backend_payload(selection.as_ref());
        let probe = BackendProbe::configured(config, selection.as_ref().ok()).probe();
        let gateway_diagnostics = read_gateway(&config.gateway_diagnostics_path());
        let runtime_logs = tail_log_dir(Path::new(RUNTIME_LOG_DIR), LOG_TAIL_BYTES);
        let ps = command_output(&["ps", "w"], COMMAND_TIMEOUT);
        let helper_processes = matching_processes(ps.stdout(), "venus-evcharger-auto-input-helper");
        Self {
            timestamp: epoch_seconds(),
            config_path: config.path.to_string_lossy().into_owned(),
            gateway_diagnostics,
            backend_diagnostics,
            backend_probe: probe,
            auto_input_snapshot: read_json_object(&config.auto_input_snapshot_path()),
            runtime_state: read_json_object(&config.runtime_state_path()),
            helper_processes,
            svstat: command_output(
                &["svstat", "/service/dbus-venus-evcharger"],
                COMMAND_TIMEOUT,
            ),
            ps,
            uptime: command_output(&["uptime"], COMMAND_TIMEOUT),
            trace_markers: runtime_markers(&runtime_logs),
            runtime_logs,
        }
    }

    /// Return sorted, deduplicated reasons that justify an incident bundle.
    #[must_use]
    pub fn incident_reasons(&self) -> Vec<String> {
        let mut reasons = BTreeSet::new();
        if let GatewayDiagnosticsPayload::Available { snapshot, .. } = &self.gateway_diagnostics {
            for name in snapshot.critical_unavailable_fields() {
                reasons.insert(format!("gateway-{}-unavailable", name.replace('_', "-")));
            }
            if matches!(snapshot.health.state.as_str(), "protective" | "unavailable") {
                reasons.insert(format!("gateway-health-{}", snapshot.health.state));
            }
        }
        match &self.svstat {
            CommandPayload::Error { .. } | CommandPayload::Completed { ok: false, .. } => {
                reasons.insert("runit-status-failed".to_owned());
            }
            CommandPayload::Completed { stdout, .. } if !format!(" {stdout} ").contains(" up ") => {
                reasons.insert("runit-not-up".to_owned());
            }
            CommandPayload::Completed { .. } => {}
        }
        for marker in &self.trace_markers {
            reasons.insert(format!("log-marker-{}", slug_text(marker)));
        }
        reasons.into_iter().collect()
    }

    /// Serialize the snapshot into a JSON object suitable for an artifact.
    ///
    /// # Errors
    ///
    /// Returns an error when a snapshot field cannot be represented as JSON.
    pub fn to_value(&self) -> crate::Result<Value> {
        serde_json::to_value(self).map_err(Into::into)
    }
}

/// Normalize arbitrary text into a safe incident path component.
#[must_use]
pub fn slug_text(text: &str) -> String {
    let mut result = String::new();
    let mut separator = false;
    for character in text.chars().flat_map(char::to_lowercase) {
        if character.is_ascii_lowercase() || character.is_ascii_digit() {
            if separator && !result.is_empty() {
                result.push('-');
            }
            result.push(character);
            separator = false;
        } else {
            separator = true;
        }
    }
    if result.is_empty() {
        "event".to_owned()
    } else {
        result
    }
}

fn read_gateway(path: &Path) -> GatewayDiagnosticsPayload {
    match GatewayDiagnostics::read(path) {
        Ok(snapshot) => GatewayDiagnosticsPayload::Available {
            available: true,
            snapshot: Box::new(snapshot),
        },
        Err(error) => GatewayDiagnosticsPayload::Unavailable {
            available: false,
            error: format!("gateway diagnostics unavailable: {error}"),
        },
    }
}

fn backend_payload(
    selection: std::result::Result<&BackendSelection, &crate::ObserverError>,
) -> BackendDiagnosticsPayload {
    match selection {
        Ok(selection) => BackendDiagnosticsPayload::Available {
            available: true,
            selection: selection.to_payload(),
        },
        Err(error) => BackendDiagnosticsPayload::Unavailable {
            available: false,
            reason_code: "backend-configuration-invalid".to_owned(),
            error: error.to_string(),
        },
    }
}

fn runtime_markers(logs: &BTreeMap<String, String>) -> Vec<String> {
    TRACE_MARKERS
        .iter()
        .filter(|marker| logs.values().any(|text| text.contains(**marker)))
        .map(|marker| (*marker).to_owned())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn epoch_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |duration| duration.as_secs_f64())
}

#[cfg(test)]
mod tests {
    use super::{ForensicSnapshot, GatewayDiagnosticsPayload, slug_text};
    use crate::command::CommandPayload;
    use crate::probe::BackendProbeResult;
    use serde_json::json;
    use std::collections::BTreeMap;
    use std::fs;
    use tempfile::tempdir;

    fn snapshot(
        gateway: GatewayDiagnosticsPayload,
        svstat: CommandPayload,
        markers: &[&str],
    ) -> ForensicSnapshot {
        ForensicSnapshot {
            timestamp: 100.0,
            config_path: "config.ini".to_owned(),
            gateway_diagnostics: gateway,
            backend_diagnostics: super::BackendDiagnosticsPayload::Unavailable {
                available: false,
                reason_code: "test".to_owned(),
                error: "test".to_owned(),
            },
            backend_probe: BackendProbeResult {
                status: "disabled".to_owned(),
                probe_type: "none".to_owned(),
                role: String::new(),
                backend_type: String::new(),
                reason_code: "test".to_owned(),
                payload: String::new(),
            },
            auto_input_snapshot: json!({}),
            runtime_state: json!({}),
            helper_processes: Vec::new(),
            svstat,
            ps: CommandPayload::Error {
                ok: false,
                error: "test".to_owned(),
            },
            uptime: CommandPayload::Error {
                ok: false,
                error: "test".to_owned(),
            },
            runtime_logs: BTreeMap::new(),
            trace_markers: markers.iter().map(|value| (*value).to_owned()).collect(),
        }
    }

    #[test]
    fn slugs_match_the_existing_artifact_contract() {
        assert_eq!(slug_text("NoReply"), "noreply");
        assert_eq!(slug_text("malloc()"), "malloc");
        assert_eq!(slug_text("--"), "event");
    }

    #[test]
    fn learning_stale_is_not_an_incident_marker() {
        let mut logs = BTreeMap::new();
        logs.insert(
            "current".to_owned(),
            "learned_charge_power_reason=learning-stale".to_owned(),
        );

        assert!(super::runtime_markers(&logs).is_empty());
    }

    #[test]
    fn unavailable_gateway_is_not_itself_an_incident() {
        let artifact = snapshot(
            GatewayDiagnosticsPayload::Unavailable {
                available: false,
                error: "offline".to_owned(),
            },
            CommandPayload::Completed {
                ok: true,
                returncode: 0,
                stdout: "service up".to_owned(),
                stderr: String::new(),
            },
            &[],
        );
        assert!(artifact.incident_reasons().is_empty());
    }

    #[test]
    fn runit_and_markers_are_sorted_and_deduplicated() {
        let artifact = snapshot(
            GatewayDiagnosticsPayload::Unavailable {
                available: false,
                error: "offline".to_owned(),
            },
            CommandPayload::Completed {
                ok: true,
                returncode: 0,
                stdout: "service down".to_owned(),
                stderr: String::new(),
            },
            &["NoReply", "NoReply", "malloc()"],
        );
        assert_eq!(
            artifact.incident_reasons(),
            vec!["log-marker-malloc", "log-marker-noreply", "runit-not-up"]
        );
    }

    #[test]
    fn collected_snapshot_preserves_the_stable_artifact_schema() {
        let directory = tempdir();
        assert!(directory.is_ok());
        let Some(directory) = directory.ok() else {
            return;
        };
        let config_path = directory.path().join("config.ini");
        assert!(fs::write(&config_path, "[DEFAULT]\nDeviceInstance=77\n").is_ok());
        let config = crate::config::ObserverConfig::load(&config_path);
        assert!(config.is_ok());
        let Some(config) = config.ok() else {
            return;
        };
        let payload = ForensicSnapshot::collect(&config).to_value();
        assert!(payload.is_ok());
        let Some(object) = payload.ok().and_then(|value| value.as_object().cloned()) else {
            return;
        };
        assert_eq!(
            object
                .keys()
                .map(String::as_str)
                .collect::<std::collections::BTreeSet<_>>(),
            [
                "auto_input_snapshot",
                "backend_diagnostics",
                "backend_probe",
                "config_path",
                "gateway_diagnostics",
                "helper_processes",
                "ps",
                "runtime_logs",
                "runtime_state",
                "svstat",
                "timestamp",
                "trace_markers",
                "uptime",
            ]
            .into_iter()
            .collect()
        );
    }
}
