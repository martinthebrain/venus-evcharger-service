//! Bounded atomic file publication shared by snapshot and command writers.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use crate::error::{HelperError, Result};

/// Replace one file atomically with explicitly constrained permissions.
///
/// # Errors
///
/// Returns an error when the parent directory, temporary file, write, or
/// atomic rename cannot be completed.
pub fn write_atomic(path: &Path, bytes: &[u8], mode: u32, label: &str) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| HelperError::Storage(format!("{label} path has no parent directory")))?;
    fs::create_dir_all(parent)
        .map_err(|error| HelperError::storage(&format!("create {label} directory"), &error))?;
    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("auto-input");
    let temporary = parent.join(format!(".{file_name}.tmp.{}", std::process::id()));
    let result = (|| -> Result<()> {
        let mut file = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&temporary)
            .map_err(|error| {
                HelperError::storage(&format!("create {label} temporary file"), &error)
            })?;
        file.set_permissions(fs::Permissions::from_mode(mode))
            .map_err(|error| HelperError::storage(&format!("set {label} permissions"), &error))?;
        file.write_all(bytes)
            .map_err(|error| HelperError::storage(&format!("write {label}"), &error))?;
        fs::rename(&temporary, path)
            .map_err(|error| HelperError::storage(&format!("replace {label}"), &error))?;
        Ok(())
    })();
    if result.is_err() {
        let _ignored = fs::remove_file(&temporary);
    }
    result
}
