//! Bounded process, log-tail, and JSON-file readers.

use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{Map, Value};

use crate::ini::read_bounded_text;

const COMMAND_TAIL_BYTES: usize = 4_000;
const JSON_FILE_MAX_BYTES: u64 = 1_048_576;
const LOG_FILE_COUNT: usize = 4;

/// Bounded subprocess result stored in one forensic snapshot.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(untagged)]
pub enum CommandPayload {
    /// Process terminated with a status code.
    Completed {
        /// Whether the exit code was zero.
        ok: bool,
        /// Process exit code, or -1 when terminated by a signal.
        returncode: i32,
        /// Last 4,000 bytes of standard output.
        stdout: String,
        /// Last 4,000 bytes of standard error.
        stderr: String,
    },
    /// Process could not be started or timed out.
    Error {
        /// Always false for invocation failures.
        ok: bool,
        /// Bounded operating-system error text.
        error: String,
    },
}

impl CommandPayload {
    /// Return successful standard output, or an empty string.
    #[must_use]
    pub fn stdout(&self) -> &str {
        match self {
            Self::Completed { stdout, .. } => stdout,
            Self::Error { .. } => "",
        }
    }

    /// Return whether the subprocess completed successfully.
    #[must_use]
    pub const fn is_ok(&self) -> bool {
        matches!(self, Self::Completed { ok: true, .. })
    }
}

/// One process selected from the bounded process listing.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ProcessPayload {
    pid: String,
    line: String,
}

/// Execute one command with bounded output and elapsed time.
#[must_use]
pub fn command_output(arguments: &[&str], timeout: Duration) -> CommandPayload {
    let Some((program, command_arguments)) = arguments.split_first() else {
        return command_error("empty command");
    };
    let mut child = match Command::new(program)
        .args(command_arguments)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(value) => value,
        Err(error) => return command_error(&error.to_string()),
    };
    let stdout_reader = child.stdout.take().map(spawn_bounded_reader);
    let stderr_reader = child.stderr.take().map(spawn_bounded_reader);
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
            Ok(None) => {
                let _ignored = child.kill();
                let _ignored = child.wait();
                break Err("command timed out".to_owned());
            }
            Err(error) => break Err(error.to_string()),
        }
    };
    let stdout = join_reader(stdout_reader);
    let stderr = join_reader(stderr_reader);
    match status {
        Ok(status) => CommandPayload::Completed {
            ok: status.success(),
            returncode: status.code().unwrap_or(-1),
            stdout,
            stderr,
        },
        Err(error) => command_error(&error),
    }
}

/// Read the final bytes of one file and decode invalid UTF-8 lossily.
#[must_use]
pub fn tail_file(path: &Path, max_bytes: usize) -> String {
    match tail_file_result(path, max_bytes) {
        Ok(value) => value,
        Err(error) => format!("<unavailable: {error}>"),
    }
}

/// Read up to four newest regular files from one runtime log directory.
#[must_use]
pub fn tail_log_dir(path: &Path, max_bytes: usize) -> BTreeMap<String, String> {
    let Ok(entries) = fs::read_dir(path) else {
        return BTreeMap::new();
    };
    let mut files = entries
        .filter_map(std::result::Result::ok)
        .filter_map(|entry| {
            let metadata = entry.metadata().ok()?;
            metadata
                .is_file()
                .then(|| (metadata.modified().ok(), entry.path()))
        })
        .collect::<Vec<_>>();
    files.sort_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.cmp(&right.1)));
    files
        .into_iter()
        .rev()
        .take(LOG_FILE_COUNT)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .filter_map(|(_modified, file)| {
            let name = file.file_name()?.to_string_lossy().into_owned();
            Some((name, tail_file(&file, max_bytes)))
        })
        .collect()
}

/// Read one bounded JSON object and add stable availability metadata.
#[must_use]
pub fn read_json_object(path: &Path) -> Value {
    match read_bounded_text(path, JSON_FILE_MAX_BYTES, "observer JSON input")
        .and_then(|text| serde_json::from_str::<Value>(&text).map_err(Into::into))
    {
        Ok(Value::Object(mut object)) => {
            object.insert("ok".to_owned(), Value::Bool(true));
            object.insert(
                "path".to_owned(),
                Value::String(path.to_string_lossy().into_owned()),
            );
            Value::Object(object)
        }
        Ok(_) => json_error(path, "not-a-json-object"),
        Err(error) => json_error(path, &error.to_string()),
    }
}

/// Select process lines containing one literal marker.
#[must_use]
pub fn matching_processes(text: &str, marker: &str) -> Vec<ProcessPayload> {
    text.lines()
        .filter(|line| line.contains(marker))
        .filter_map(|line| {
            line.split_whitespace().next().map(|pid| ProcessPayload {
                pid: pid.to_owned(),
                line: line.to_owned(),
            })
        })
        .collect()
}

fn command_error(message: &str) -> CommandPayload {
    CommandPayload::Error {
        ok: false,
        error: bounded_lossy(message.as_bytes(), COMMAND_TAIL_BYTES),
    }
}

fn spawn_bounded_reader<R>(mut reader: R) -> thread::JoinHandle<std::io::Result<Vec<u8>>>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut tail = Vec::new();
        let mut chunk = [0_u8; 1_024];
        loop {
            let count = reader.read(&mut chunk)?;
            if count == 0 {
                return Ok(tail);
            }
            append_tail(&mut tail, &chunk[..count], COMMAND_TAIL_BYTES);
        }
    })
}

fn join_reader(handle: Option<thread::JoinHandle<std::io::Result<Vec<u8>>>>) -> String {
    let bytes = handle
        .and_then(|reader| reader.join().ok())
        .and_then(std::result::Result::ok)
        .unwrap_or_default();
    bounded_lossy(&bytes, COMMAND_TAIL_BYTES)
}

fn append_tail(target: &mut Vec<u8>, bytes: &[u8], limit: usize) {
    if bytes.len() >= limit {
        target.clear();
        target.extend_from_slice(&bytes[bytes.len() - limit..]);
        return;
    }
    let overflow = target
        .len()
        .saturating_add(bytes.len())
        .saturating_sub(limit);
    if overflow > 0 {
        target.drain(..overflow);
    }
    target.extend_from_slice(bytes);
}

fn bounded_lossy(bytes: &[u8], limit: usize) -> String {
    let selected = if bytes.len() > limit {
        &bytes[bytes.len() - limit..]
    } else {
        bytes
    };
    String::from_utf8_lossy(selected).into_owned()
}

fn tail_file_result(path: &Path, max_bytes: usize) -> std::io::Result<String> {
    let mut file = File::open(path)?;
    let size = file.seek(SeekFrom::End(0))?;
    let count = u64::try_from(max_bytes).unwrap_or(u64::MAX).min(size);
    let offset = i64::try_from(count).unwrap_or(i64::MAX);
    file.seek(SeekFrom::End(-offset))?;
    let mut bytes = Vec::with_capacity(usize::try_from(count).unwrap_or(max_bytes));
    file.take(count).read_to_end(&mut bytes)?;
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

fn json_error(path: &Path, message: &str) -> Value {
    let mut object = Map::new();
    object.insert("ok".to_owned(), Value::Bool(false));
    object.insert(
        "path".to_owned(),
        Value::String(path.to_string_lossy().into_owned()),
    );
    object.insert(
        "error".to_owned(),
        Value::String(bounded_lossy(message.as_bytes(), COMMAND_TAIL_BYTES)),
    );
    Value::Object(object)
}

#[cfg(test)]
mod tests {
    use super::{
        CommandPayload, append_tail, command_output, matching_processes, read_json_object,
        tail_file, tail_log_dir,
    };
    use serde_json::json;
    use std::fs;
    use std::time::Duration;
    use tempfile::tempdir;

    #[test]
    fn tail_buffer_keeps_exactly_the_final_bytes() {
        let mut tail = b"1234".to_vec();
        append_tail(&mut tail, b"5678", 6);
        assert_eq!(tail, b"345678");
        append_tail(&mut tail, b"abcdefgh", 6);
        assert_eq!(tail, b"cdefgh");
    }

    #[test]
    fn file_and_json_reads_are_bounded_and_explicit() {
        let directory = tempdir();
        assert!(directory.is_ok());
        let Some(directory) = directory.ok() else {
            return;
        };
        let path = directory.path().join("state.json");
        assert!(fs::write(&path, b"{\"value\":1}").is_ok());
        assert_eq!(tail_file(&path, 4), "\":1}");
        assert_eq!(read_json_object(&path)["value"], json!(1));
        assert_eq!(read_json_object(&path)["ok"], json!(true));
        assert_eq!(
            read_json_object(&directory.path().join("missing"))["ok"],
            json!(false)
        );
    }

    #[test]
    fn helper_process_matching_is_literal_and_stable() {
        assert_eq!(
            matching_processes(
                "123 root helper marker\nnot-helper\nmarker-only\n",
                "marker"
            ),
            vec![
                super::ProcessPayload {
                    pid: "123".to_owned(),
                    line: "123 root helper marker".to_owned()
                },
                super::ProcessPayload {
                    pid: "marker-only".to_owned(),
                    line: "marker-only".to_owned()
                },
            ]
        );
    }

    #[test]
    fn command_output_is_bounded_and_preserves_the_exit_contract() {
        let output = command_output(
            &[
                "sh",
                "-c",
                "i=0; while [ $i -lt 5000 ]; do printf x; i=$((i+1)); done",
            ],
            Duration::from_secs(2),
        );
        assert!(matches!(
            output,
            CommandPayload::Completed {
                ok: true,
                returncode: 0,
                ref stdout,
                ref stderr,
            } if stdout.len() == 4_000 && stderr.is_empty()
        ));

        let failed = command_output(
            &["sh", "-c", "printf failure >&2; exit 7"],
            Duration::from_secs(2),
        );
        assert!(matches!(
            failed,
            CommandPayload::Completed {
                ok: false,
                returncode: 7,
                ref stderr,
                ..
            } if stderr == "failure"
        ));
    }

    #[test]
    fn log_tail_selects_only_the_four_newest_files() {
        let directory = tempdir();
        assert!(directory.is_ok());
        let Some(directory) = directory.ok() else {
            return;
        };
        for index in 0_u8..5 {
            let path = directory.path().join(format!("log-{index}"));
            assert!(fs::write(&path, format!("entry-{index}")).is_ok());
            std::thread::sleep(Duration::from_millis(2));
        }
        let logs = tail_log_dir(directory.path(), 5);
        assert_eq!(logs.len(), 4);
        assert!(!logs.contains_key("log-0"));
        assert_eq!(logs.get("log-4").map(String::as_str), Some("try-4"));
    }
}
