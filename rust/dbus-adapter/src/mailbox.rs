// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded atomic JSON mailboxes compatible with the Python transport.

use std::fmt::Write as _;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use fs2::FileExt;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

const MAX_COMMAND_BYTES: u64 = 65_536;
type PendingCommand = (PathBuf, Map<String, Value>);

pub struct Mailbox {
    directory: PathBuf,
    sequence: AtomicU64,
}

impl Mailbox {
    pub const fn new(directory: PathBuf) -> Self {
        Self {
            directory,
            sequence: AtomicU64::new(0),
        }
    }

    pub fn pending(&self) -> Result<Vec<PendingCommand>, String> {
        let mut paths = match fs::read_dir(&self.directory) {
            Ok(entries) => entries
                .filter_map(Result::ok)
                .map(|entry| entry.path())
                .filter(|path| path.extension().is_some_and(|value| value == "json"))
                .collect::<Vec<_>>(),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(error) => return Err(error.to_string()),
        };
        paths.sort();
        let mut result = Vec::new();
        for path in paths {
            let metadata = match fs::metadata(&path) {
                Ok(metadata) if metadata.is_file() && metadata.len() <= MAX_COMMAND_BYTES => {
                    metadata
                }
                Ok(_) | Err(_) => continue,
            };
            if metadata.len() == 0 {
                continue;
            }
            let Ok(payload) = fs::read(&path) else {
                continue;
            };
            let Ok(Value::Object(command)) = serde_json::from_slice::<Value>(&payload) else {
                continue;
            };
            result.push((path, command));
        }
        Ok(result)
    }

    pub fn remove(path: &Path) -> Result<(), String> {
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error.to_string()),
        }
    }

    pub fn enqueue_core_control(
        &self,
        name: &str,
        target: &str,
        value: &Value,
    ) -> Result<PathBuf, String> {
        fs::create_dir_all(&self.directory).map_err(|error| error.to_string())?;
        let coalesce_key = format!("core:{name}:{target}");
        let id = format!("coalesced-{}", sha_prefix(&coalesce_key, 12));
        let path = self.directory.join(format!("{id}.json"));
        let lock_path = self.directory.join(".mailbox.lock");
        let lock = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(&lock_path)
            .map_err(|error| error.to_string())?;
        lock.lock_exclusive().map_err(|error| error.to_string())?;
        let now = epoch_seconds()?;
        let sequence = self.sequence.fetch_add(1, Ordering::Relaxed);
        let mut payload = Map::new();
        payload.insert("schema_version".to_owned(), Value::from(1));
        payload.insert("id".to_owned(), Value::String(id));
        payload.insert("created_at".to_owned(), Value::from(now));
        payload.insert(
            "mailbox_revision".to_owned(),
            Value::String(format!("{}-{sequence}", std::process::id())),
        );
        payload.insert(
            "queue_class".to_owned(),
            Value::String("core-control".to_owned()),
        );
        payload.insert("kind".to_owned(), Value::String("user_command".to_owned()));
        payload.insert("name".to_owned(), Value::String(name.to_owned()));
        payload.insert("target".to_owned(), Value::String(target.to_owned()));
        payload.insert(
            "source".to_owned(),
            Value::String("control-surface".to_owned()),
        );
        payload.insert("origin".to_owned(), Value::String("gateway-gui".to_owned()));
        payload.insert("value".to_owned(), value.clone());
        payload.insert("priority".to_owned(), Value::String("user".to_owned()));
        payload.insert("coalesce_key".to_owned(), Value::String(coalesce_key));
        payload.insert(
            "lifecycle_state".to_owned(),
            Value::String("queued".to_owned()),
        );
        let result = atomic_json(&path, &Value::Object(payload));
        let _ignored = FileExt::unlock(&lock);
        result.map(|()| path)
    }
}

pub fn atomic_json(path: &Path, payload: &Value) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("output path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temporary = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("mailbox"),
        std::process::id(),
    ));
    let encoded = serde_json::to_vec(payload).map_err(|error| error.to_string())?;
    let result = (|| {
        let mut file = File::create(&temporary).map_err(|error| error.to_string())?;
        file.write_all(&encoded)
            .map_err(|error| error.to_string())?;
        file.flush().map_err(|error| error.to_string())?;
        fs::rename(&temporary, path).map_err(|error| error.to_string())
    })();
    if result.is_err() {
        let _ignored = fs::remove_file(&temporary);
    }
    result
}

fn epoch_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|error| error.to_string())
}

fn sha_prefix(value: &str, bytes: usize) -> String {
    let digest = Sha256::digest(value.as_bytes());
    let mut result = String::with_capacity(bytes.saturating_mul(2));
    for byte in digest.iter().take(bytes) {
        let _ignored = write!(result, "{byte:02x}");
    }
    result
}

#[cfg(test)]
mod tests {
    use serde_json::json;
    use tempfile::tempdir;

    use super::Mailbox;

    #[test]
    fn core_command_is_python_compatible_and_latest_wins() -> Result<(), String> {
        let directory = tempdir().map_err(|error| error.to_string())?;
        let mailbox = Mailbox::new(directory.path().to_path_buf());
        let first = mailbox.enqueue_core_control("set_mode", "mode", &json!(1))?;
        let second = mailbox.enqueue_core_control("set_mode", "mode", &json!(2))?;
        assert_eq!(first, second);
        let pending = mailbox.pending()?;
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].1.get("value"), Some(&json!(2)));
        assert_eq!(
            pending[0].1.get("queue_class"),
            Some(&json!("core-control"))
        );
        Ok(())
    }
}
