//! Explicit, fail-closed backend-specific forensic probes.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Serialize;

use crate::config::{BackendSelection, ObserverConfig};
use crate::ini::{IniDocument, read_bounded_text};

const SHELLY_PROBE: &str = "shelly-rpc";
const SHELLY_STATUS_PATH: &str = "/rpc/Shelly.GetStatus";
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_HTTP_HEADER_BYTES: usize = 16_384;
const MAX_HTTP_BODY_BYTES: usize = 65_536;
const MAX_BACKEND_CONFIG_BYTES: u64 = 1_048_576;

/// Stable bounded result of one optional backend probe.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BackendProbeResult {
    /// Disabled, skipped, successful, or failed probe state.
    pub status: String,
    /// Configured probe implementation.
    pub probe_type: String,
    /// Selected backend role.
    pub role: String,
    /// Normalized backend type.
    pub backend_type: String,
    /// Stable machine-readable reason.
    pub reason_code: String,
    /// Bounded response or error detail.
    pub payload: String,
}

/// Validated optional probe selected from normalized backend semantics.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BackendProbe {
    /// No direct probe was requested.
    Disabled {
        /// Stable explanation for disabling the probe.
        reason_code: String,
    },
    /// Requested probe was rejected before any network access.
    Rejected {
        /// Requested probe implementation.
        probe_type: String,
        /// Requested backend role.
        role: String,
        /// Selected backend type.
        backend_type: String,
        /// Stable rejection reason.
        reason_code: String,
    },
    /// Explicit Shelly RPC probe for a validated Shelly backend.
    Shelly {
        /// Validated network authority.
        host: String,
        /// Selected backend role.
        role: String,
        /// Selected Shelly backend type.
        backend_type: String,
    },
}

impl BackendProbe {
    /// Select an optional probe without making a network request.
    #[must_use]
    pub fn configured(config: &ObserverConfig, selection: Option<&BackendSelection>) -> Self {
        let Some(selection) = selection else {
            return Self::Disabled {
                reason_code: "backend-diagnostics-unavailable".to_owned(),
            };
        };
        let configured = config.ini.default_value("ForensicBackendProbe");
        let Some(probe_type) = configured else {
            return disabled();
        };
        let probe_type = probe_type.trim().to_ascii_lowercase();
        if ["", "disabled", "none", "off"].contains(&probe_type.as_str()) {
            return disabled();
        }
        let role = config
            .ini
            .default_value("ForensicBackendProbeRole")
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("switch")
            .to_ascii_lowercase();
        let Some((backend_type, backend_path)) = selection.role(&role) else {
            return rejected(&probe_type, "", "", "invalid-probe-role");
        };
        if probe_type != SHELLY_PROBE {
            return rejected(&probe_type, &role, backend_type, "unsupported-probe-type");
        }
        if !backend_type.starts_with("shelly") {
            return rejected(&probe_type, &role, backend_type, "backend-type-mismatch");
        }
        let host = backend_host(config, backend_path);
        let Some(host) = host else {
            return rejected(&probe_type, &role, backend_type, "backend-host-missing");
        };
        if split_host_port(&host).is_none() {
            return rejected(&probe_type, &role, backend_type, "backend-host-invalid");
        }
        Self::Shelly {
            host,
            role,
            backend_type: backend_type.to_owned(),
        }
    }

    /// Execute the selected bounded probe.
    #[must_use]
    pub fn probe(&self) -> BackendProbeResult {
        match self {
            Self::Disabled { reason_code } => BackendProbeResult {
                status: "disabled".to_owned(),
                probe_type: "none".to_owned(),
                role: String::new(),
                backend_type: String::new(),
                reason_code: reason_code.clone(),
                payload: String::new(),
            },
            Self::Rejected {
                probe_type,
                role,
                backend_type,
                reason_code,
            } => BackendProbeResult {
                status: "skipped".to_owned(),
                probe_type: probe_type.clone(),
                role: role.clone(),
                backend_type: backend_type.clone(),
                reason_code: reason_code.clone(),
                payload: String::new(),
            },
            Self::Shelly {
                host,
                role,
                backend_type,
            } => match shelly_status(host) {
                Ok(payload) => BackendProbeResult {
                    status: "ok".to_owned(),
                    probe_type: SHELLY_PROBE.to_owned(),
                    role: role.clone(),
                    backend_type: backend_type.clone(),
                    reason_code: String::new(),
                    payload,
                },
                Err(error) => BackendProbeResult {
                    status: "error".to_owned(),
                    probe_type: SHELLY_PROBE.to_owned(),
                    role: role.clone(),
                    backend_type: backend_type.clone(),
                    reason_code: "backend-probe-failed".to_owned(),
                    payload: bounded_text(&error, MAX_HTTP_BODY_BYTES),
                },
            },
        }
    }
}

fn disabled() -> BackendProbe {
    BackendProbe::Disabled {
        reason_code: "direct-probe-disabled".to_owned(),
    }
}

fn rejected(probe_type: &str, role: &str, backend_type: &str, reason_code: &str) -> BackendProbe {
    BackendProbe::Rejected {
        probe_type: probe_type.to_owned(),
        role: role.to_owned(),
        backend_type: backend_type.to_owned(),
        reason_code: reason_code.to_owned(),
    }
}

fn backend_host(config: &ObserverConfig, backend_path: Option<&Path>) -> Option<String> {
    if let Some(path) = backend_path {
        let resolved = resolve_relative(&config.path, path);
        let text =
            read_bounded_text(&resolved, MAX_BACKEND_CONFIG_BYTES, "backend probe config").ok()?;
        let adapter = IniDocument::parse(&text).ok()?;
        return adapter
            .get_case_insensitive("Adapter", "Host")
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned);
    }
    config
        .ini
        .default_value("Host")
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn resolve_relative(main_config_path: &Path, backend_path: &Path) -> PathBuf {
    if backend_path.is_absolute() {
        backend_path.to_path_buf()
    } else {
        main_config_path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(backend_path)
    }
}

fn shelly_status(host: &str) -> std::result::Result<String, String> {
    let (hostname, port) =
        split_host_port(host).ok_or_else(|| "backend host is invalid".to_owned())?;
    let addresses = (hostname.as_str(), port)
        .to_socket_addrs()
        .map_err(|error| format!("backend address resolution failed: {error}"))?;
    let mut stream = connect(addresses)?;
    stream
        .set_read_timeout(Some(HTTP_TIMEOUT))
        .map_err(|error| format!("backend read timeout setup failed: {error}"))?;
    stream
        .set_write_timeout(Some(HTTP_TIMEOUT))
        .map_err(|error| format!("backend write timeout setup failed: {error}"))?;
    let request = format!(
        "GET {SHELLY_STATUS_PATH} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("backend request failed: {error}"))?;
    read_http_response(&mut stream)
}

fn connect(
    addresses: impl Iterator<Item = std::net::SocketAddr>,
) -> std::result::Result<TcpStream, String> {
    let mut last_error = None;
    for address in addresses {
        match TcpStream::connect_timeout(&address, HTTP_TIMEOUT) {
            Ok(stream) => return Ok(stream),
            Err(error) => last_error = Some(error),
        }
    }
    Err(last_error.map_or_else(
        || "backend address resolution returned no endpoints".to_owned(),
        |error| format!("backend connection failed: {error}"),
    ))
}

fn read_http_response(stream: &mut TcpStream) -> std::result::Result<String, String> {
    let limit = MAX_HTTP_HEADER_BYTES + MAX_HTTP_BODY_BYTES;
    let mut response = Vec::with_capacity(4_096);
    let mut chunk = [0_u8; 2_048];
    let header_end = loop {
        let count = stream
            .read(&mut chunk)
            .map_err(|error| format!("backend response failed: {error}"))?;
        if count == 0 {
            break find_header_end(&response);
        }
        let remaining = limit.saturating_sub(response.len());
        response.extend_from_slice(&chunk[..count.min(remaining)]);
        if let Some(end) = find_header_end(&response) {
            break Some(end);
        }
        if response.len() >= MAX_HTTP_HEADER_BYTES {
            return Err("backend response headers exceed the limit".to_owned());
        }
    }
    .ok_or_else(|| "backend response lacks complete HTTP headers".to_owned())?;
    let header = String::from_utf8_lossy(&response[..header_end]);
    let status = header.lines().next().unwrap_or_default();
    if !status
        .split_whitespace()
        .nth(1)
        .is_some_and(|code| code.starts_with('2'))
    {
        return Err(format!(
            "backend returned non-success status: {}",
            bounded_text(status, 128)
        ));
    }
    while response.len() < limit {
        let count = stream
            .read(&mut chunk)
            .map_err(|error| format!("backend response failed: {error}"))?;
        if count == 0 {
            break;
        }
        let remaining = limit - response.len();
        response.extend_from_slice(&chunk[..count.min(remaining)]);
    }
    let body_start = header_end + 4;
    let body_end = response
        .len()
        .min(body_start.saturating_add(MAX_HTTP_BODY_BYTES));
    Ok(String::from_utf8_lossy(&response[body_start..body_end]).into_owned())
}

fn find_header_end(bytes: &[u8]) -> Option<usize> {
    bytes.windows(4).position(|window| window == b"\r\n\r\n")
}

fn split_host_port(value: &str) -> Option<(String, u16)> {
    let value = value.trim();
    if value.is_empty()
        || value.contains("//")
        || value.contains('/')
        || value.chars().any(char::is_whitespace)
        || value.chars().any(char::is_control)
    {
        return None;
    }
    if let Some(rest) = value.strip_prefix('[') {
        let end = rest.find(']')?;
        let host = rest[..end].to_owned();
        let suffix = &rest[end + 1..];
        let port = if suffix.is_empty() {
            80
        } else {
            suffix.strip_prefix(':')?.parse().ok()?
        };
        return (!host.is_empty()).then_some((host, port));
    }
    let colon_count = value.bytes().filter(|byte| *byte == b':').count();
    if colon_count == 0 {
        return Some((value.to_owned(), 80));
    }
    if colon_count == 1 {
        let (host, port) = value.rsplit_once(':')?;
        return (!host.is_empty())
            .then(|| port.parse().ok().map(|port| (host.to_owned(), port)))
            .flatten();
    }
    None
}

fn bounded_text(value: &str, max_bytes: usize) -> String {
    if value.len() <= max_bytes {
        return value.to_owned();
    }
    String::from_utf8_lossy(&value.as_bytes()[..max_bytes]).into_owned()
}

#[cfg(test)]
#[path = "probe_tests.rs"]
mod tests;
