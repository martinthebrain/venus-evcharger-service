// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded local socket carrying transient semantic publications.

use std::collections::VecDeque;
use std::fmt::Write as _;
use std::fs;
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

const MAGIC: &[u8; 4] = b"EVCF";
const VERSION: u8 = 2;
const HEADER_BYTES: usize = 9;
const MAX_PAYLOAD_BYTES: usize = 64 * 1024;
const QUEUE_CAPACITY: usize = 64;
const REQUEST_TIMEOUT: Duration = Duration::from_millis(100);
const CONNECTIONS_PER_TICK: usize = 4;

pub struct FastPublicationServer {
    path: PathBuf,
    listener: UnixListener,
    queue: VecDeque<Map<String, Value>>,
}

impl FastPublicationServer {
    pub fn bind(path: &Path) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        match fs::remove_file(path) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.to_string()),
        }
        let listener = UnixListener::bind(path).map_err(|error| error.to_string())?;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))
            .map_err(|error| error.to_string())?;
        listener
            .set_nonblocking(true)
            .map_err(|error| error.to_string())?;
        Ok(Self {
            path: path.to_path_buf(),
            listener,
            queue: VecDeque::new(),
        })
    }

    pub fn accept_ready(&mut self) -> Result<usize, String> {
        let mut accepted = 0;
        while accepted < CONNECTIONS_PER_TICK {
            let stream = match self.listener.accept() {
                Ok((stream, _address)) => stream,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(error) => return Err(error.to_string()),
            };
            accepted += 1;
            self.handle_stream(stream);
        }
        Ok(accepted)
    }

    pub fn pop(&mut self) -> Option<Map<String, Value>> {
        self.queue.pop_front()
    }

    pub fn defer(&mut self, command: Map<String, Value>) {
        if self.queue.len() < QUEUE_CAPACITY {
            self.queue.push_front(command);
        }
    }

    fn handle_stream(&mut self, mut stream: UnixStream) {
        let response = match read_frame(&mut stream) {
            Ok(command) => self.enqueue(command),
            Err(error) => json!({"ok": false, "error": error}),
        };
        let _ignored = write_frame(&mut stream, &response);
    }

    fn enqueue(&mut self, command: Map<String, Value>) -> Value {
        let kind = command.get("kind").and_then(Value::as_str).unwrap_or("");
        if !matches!(kind, "publish_evcs_fields" | "publish_companion_fields") {
            return json!({"ok": false, "error": format!("unsupported command kind: {kind}")});
        }
        let valid_fields = command
            .get("fields")
            .and_then(Value::as_object)
            .is_some_and(|fields| !fields.is_empty() && fields.len() <= 512);
        if !valid_fields {
            return json!({"ok": false, "accepted": false, "reason": "invalid-fields"});
        }
        if self.queue.len() >= QUEUE_CAPACITY {
            return json!({"ok": false, "accepted": false, "reason": "queue-full"});
        }
        let command_id = command
            .get("id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map_or_else(|| fast_command_id(&command), str::to_owned);
        self.queue.push_back(command);
        json!({"ok": true, "accepted": true, "command_id": command_id, "reason": ""})
    }
}

impl Drop for FastPublicationServer {
    fn drop(&mut self) {
        let _ignored = fs::remove_file(&self.path);
    }
}

fn read_frame(stream: &mut UnixStream) -> Result<Map<String, Value>, String> {
    stream
        .set_nonblocking(false)
        .map_err(|error| error.to_string())?;
    stream
        .set_read_timeout(Some(REQUEST_TIMEOUT))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(REQUEST_TIMEOUT))
        .map_err(|error| error.to_string())?;
    let mut header = [0_u8; HEADER_BYTES];
    stream
        .read_exact(&mut header)
        .map_err(|error| error.to_string())?;
    if &header[..4] != MAGIC {
        return Err("invalid-frame-magic".to_owned());
    }
    if header[4] != VERSION {
        return Err("unsupported-frame-version".to_owned());
    }
    let size = u32::from_be_bytes(
        header[5..9]
            .try_into()
            .map_err(|_| "invalid-frame-header")?,
    ) as usize;
    if size == 0 {
        return Err("empty-frame".to_owned());
    }
    if size > MAX_PAYLOAD_BYTES {
        return Err("frame-too-large".to_owned());
    }
    let mut body = vec![0_u8; size];
    stream
        .read_exact(&mut body)
        .map_err(|error| error.to_string())?;
    match serde_json::from_slice::<Value>(&body).map_err(|error| error.to_string())? {
        Value::Object(command) => Ok(command),
        _ => Err("payload-must-be-object".to_owned()),
    }
}

fn write_frame(stream: &mut UnixStream, payload: &Value) -> Result<(), String> {
    let body = serde_json::to_vec(payload).map_err(|error| error.to_string())?;
    if body.is_empty() || body.len() > MAX_PAYLOAD_BYTES {
        return Err("response-frame-size-invalid".to_owned());
    }
    let size = u32::try_from(body.len()).map_err(|error| error.to_string())?;
    let mut frame = Vec::with_capacity(HEADER_BYTES + body.len());
    frame.extend_from_slice(MAGIC);
    frame.push(VERSION);
    frame.extend_from_slice(&size.to_be_bytes());
    frame.extend_from_slice(&body);
    stream.write_all(&frame).map_err(|error| error.to_string())
}

fn fast_command_id(command: &Map<String, Value>) -> String {
    let key = command
        .get("coalesce_key")
        .and_then(Value::as_str)
        .unwrap_or("fast-publication");
    let digest = Sha256::digest(key.as_bytes());
    let mut suffix = String::with_capacity(24);
    for byte in digest.iter().take(12) {
        let _ignored = write!(suffix, "{byte:02x}");
    }
    format!("fast-{suffix}")
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;

    use serde_json::json;

    use super::{HEADER_BYTES, MAGIC, VERSION, read_frame, write_frame};

    #[test]
    fn framing_matches_python_evcf_v2_contract() -> Result<(), String> {
        let (mut writer, mut reader) = UnixStream::pair().map_err(|error| error.to_string())?;
        write_frame(&mut writer, &json!({"ok": true}))?;
        let mut header = [0_u8; HEADER_BYTES];
        reader
            .read_exact(&mut header)
            .map_err(|error| error.to_string())?;
        assert_eq!(&header[..4], MAGIC);
        assert_eq!(header[4], VERSION);
        Ok(())
    }

    #[test]
    fn complete_object_frame_decodes() -> Result<(), String> {
        let (mut writer, mut reader) = UnixStream::pair().map_err(|error| error.to_string())?;
        let body = br#"{"kind":"publish_evcs_fields","fields":{"mode":2}}"#;
        writer.write_all(MAGIC).map_err(|error| error.to_string())?;
        writer
            .write_all(&[VERSION])
            .map_err(|error| error.to_string())?;
        let size = u32::try_from(body.len()).map_err(|error| error.to_string())?;
        writer
            .write_all(&size.to_be_bytes())
            .map_err(|error| error.to_string())?;
        writer.write_all(body).map_err(|error| error.to_string())?;
        let command = read_frame(&mut reader)?;
        assert_eq!(command["fields"]["mode"], json!(2));
        Ok(())
    }
}
