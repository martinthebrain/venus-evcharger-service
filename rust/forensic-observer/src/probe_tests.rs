use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::mpsc;
use std::thread;

use super::{BackendProbe, MAX_HTTP_BODY_BYTES, split_host_port};
use crate::config::{BackendSelection, ObserverConfig};
use crate::ini::IniDocument;

fn config(text: &str) -> ObserverConfig {
    ObserverConfig {
        path: PathBuf::from("/tmp/config.ini"),
        source_text: text.to_owned(),
        ini: IniDocument::parse(text).unwrap_or_default(),
    }
}

fn selection(switch_type: &str) -> BackendSelection {
    BackendSelection {
        mode: "split".to_owned(),
        meter_type: Some("shelly_meter".to_owned()),
        switch_type: Some(switch_type.to_owned()),
        charger_type: None,
        meter_config_path: None,
        switch_config_path: None,
        charger_config_path: None,
    }
}

#[test]
fn direct_probe_is_disabled_by_default() {
    assert_eq!(
        BackendProbe::configured(
            &config("[DEFAULT]\nHost=192.0.2.1\n"),
            Some(&selection("shelly_switch"))
        ),
        BackendProbe::Disabled {
            reason_code: "direct-probe-disabled".to_owned()
        }
    );
}

#[test]
fn non_shelly_backend_fails_before_network_access() {
    let configured = config(
        "[DEFAULT]\nForensicBackendProbe=shelly-rpc\nForensicBackendProbeRole=switch\nHost=192.0.2.1\n",
    );
    let probe = BackendProbe::configured(&configured, Some(&selection("tuya_switch")));
    assert_eq!(probe.probe().reason_code, "backend-type-mismatch");
}

#[test]
fn hosts_are_bounded_to_authorities() {
    assert_eq!(
        split_host_port("example.test"),
        Some(("example.test".to_owned(), 80))
    );
    assert_eq!(
        split_host_port("192.0.2.1:8080"),
        Some(("192.0.2.1".to_owned(), 8080))
    );
    assert_eq!(
        split_host_port("[2001:db8::1]:8080"),
        Some(("2001:db8::1".to_owned(), 8080))
    );
    for invalid in [
        "http://example.test",
        "example.test/path",
        "bad host",
        "[broken",
    ] {
        assert_eq!(split_host_port(invalid), None);
    }
}

#[test]
fn explicit_shelly_probe_uses_fixed_path_and_bounds_the_body() {
    let listener = TcpListener::bind("127.0.0.1:0");
    assert!(listener.is_ok());
    let Some(listener) = listener.ok() else {
        return;
    };
    let address = listener.local_addr();
    assert!(address.is_ok());
    let Some(address) = address.ok() else {
        return;
    };
    let (request_sender, request_receiver) = mpsc::channel();
    let server = thread::spawn(move || {
        let Ok((mut stream, _peer)) = listener.accept() else {
            return;
        };
        let mut request = [0_u8; 1_024];
        let count = stream.read(&mut request).unwrap_or(0);
        let _sent = request_sender.send(String::from_utf8_lossy(&request[..count]).into_owned());
        let body = vec![b'x'; MAX_HTTP_BODY_BYTES + 4_096];
        let header = format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            body.len()
        );
        let _header_result = stream.write_all(header.as_bytes());
        let _body_result = stream.write_all(&body);
    });
    let configured = config(&format!(
        "[DEFAULT]\nForensicBackendProbe=shelly-rpc\nForensicBackendProbeRole=switch\nHost={address}\n"
    ));
    let result = BackendProbe::configured(&configured, Some(&selection("shelly_switch"))).probe();
    assert_eq!(result.status, "ok");
    assert_eq!(result.payload.len(), MAX_HTTP_BODY_BYTES);
    let request = request_receiver.recv();
    assert!(request.is_ok());
    assert!(
        request
            .ok()
            .is_some_and(|value| value.starts_with("GET /rpc/Shelly.GetStatus HTTP/1.1\r\n"))
    );
    assert!(server.join().is_ok());
}
