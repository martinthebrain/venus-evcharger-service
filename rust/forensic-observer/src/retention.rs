//! Conservative, bounded retention of completed forensic bundles.

use std::collections::BinaryHeap;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::Deserialize;

use crate::artifact::{
    DEFAULT_FORENSIC_SUBDIR, StorageLease, mounted_storage_candidates, read_mounts,
};
use crate::error::{ObserverError, Result};
use crate::ini::IniDocument;

const CHECK_INTERVAL: Duration = Duration::from_secs(3_600);
const MAX_BATCH: usize = 128;
const MAX_RECOVERY_BYTES: u64 = 8_192;
const BUNDLE_FILES: [&str; 3] = ["snapshot.json", "config.redacted.ini", "recovery.json"];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RetentionPolicy {
    max_age: Duration,
    max_bytes: u64,
}

impl RetentionPolicy {
    pub(crate) fn from_ini(ini: &IniDocument) -> Result<Self> {
        Ok(Self {
            max_age: Duration::from_secs(
                positive_setting(ini, "ForensicRetentionDays", 30, 36_500)? * 86_400,
            ),
            max_bytes: positive_setting(ini, "ForensicMaxTotalMiB", 100, 1_048_576)? * 1_048_576,
        })
    }
}

fn positive_setting(ini: &IniDocument, key: &str, default: u64, maximum: u64) -> Result<u64> {
    let Some(text) = ini.default_value(key) else {
        return Ok(default);
    };
    text.trim()
        .parse::<u64>()
        .ok()
        .filter(|value| (1..=maximum).contains(value))
        .ok_or_else(|| {
            ObserverError::Configuration(format!(
                "{key} must be an integer between 1 and {maximum}"
            ))
        })
}

#[derive(Default)]
pub struct RetentionSchedule {
    last_check: Option<Instant>,
}

impl RetentionSchedule {
    pub(crate) fn due(&mut self, now: Instant) -> bool {
        if self
            .last_check
            .is_some_and(|previous| now.saturating_duration_since(previous) < CHECK_INTERVAL)
        {
            return false;
        }
        self.last_check = Some(now);
        true
    }
}

pub fn prune(
    policy: RetentionPolicy,
    mounts_path: &Path,
    lock_path: &Path,
    active: Option<&Path>,
) -> Result<()> {
    let Some(_lease) = StorageLease::try_acquire_exclusive(lock_path)? else {
        return Ok(());
    };
    let mounts = read_mounts(mounts_path);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    for mount in mounted_storage_candidates(&mounts) {
        let result = prune_mount(&mount, active, policy, now, || {
            read_mounts(mounts_path) == mounts
        });
        if let Err(error) = result {
            return Err(ObserverError::storage("retain forensic bundles", &error));
        }
    }
    Ok(())
}

fn prune_mount(
    mount: &Path,
    active: Option<&Path>,
    policy: RetentionPolicy,
    now: Duration,
    still_mounted: impl Fn() -> bool,
) -> io::Result<()> {
    let Ok(mount_fd) = open_directory(mount) else {
        return Ok(());
    };
    let Ok(root) = open_directory(&fd_path(&mount_fd).join(DEFAULT_FORENSIC_SUBDIR)) else {
        return Ok(());
    };
    if root.metadata()?.dev() != mount_fd.metadata()?.dev() || !still_mounted() {
        return Ok(());
    }
    let active_name = active
        .filter(|path| path.parent() == Some(mount.join(DEFAULT_FORENSIC_SUBDIR).as_path()))
        .and_then(Path::file_name);
    prune_root(&root, active_name, policy, now)
}

fn open_directory(path: &Path) -> io::Result<File> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
        .open(path)
}

fn fd_path(file: &File) -> PathBuf {
    // Pin IO to the opened filesystem, never to a replacement mount or its backing directory.
    PathBuf::from(format!("/proc/self/fd/{}", file.as_raw_fd()))
}

#[derive(Debug, Eq, PartialEq, Ord, PartialOrd)]
struct Candidate {
    recovered_at: Duration,
    name: OsString,
    bytes: u64,
    inode: u64,
}

#[derive(Deserialize)]
struct Recovery {
    schema_version: u8,
    state: String,
    incident_started_at: f64,
    recovered_at: f64,
}

fn recovery_time(directory: &Path, now: Duration) -> Option<Duration> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(directory.join("recovery.json"))
        .ok()?;
    let metadata = file.metadata().ok()?;
    if !metadata.is_file() || metadata.len() > MAX_RECOVERY_BYTES {
        return None;
    }
    let mut bytes = Vec::new();
    file.take(MAX_RECOVERY_BYTES + 1)
        .read_to_end(&mut bytes)
        .ok()?;
    if bytes.len() as u64 > MAX_RECOVERY_BYTES {
        return None;
    }
    let record: Recovery = serde_json::from_slice(&bytes).ok()?;
    let start = Duration::try_from_secs_f64(record.incident_started_at).ok()?;
    let end = Duration::try_from_secs_f64(record.recovered_at).ok()?;
    (record.schema_version == 1 && record.state == "recovered" && end >= start && end <= now)
        .then_some(end)
}

fn bundle_size(directory: &Path) -> io::Result<(u64, bool)> {
    let mut bytes = 0_u64;
    let mut count = 0;
    let mut recognized = true;
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let metadata = fs::symlink_metadata(entry.path())?;
        if metadata.is_file() {
            bytes = bytes.saturating_add(metadata.len());
        }
        recognized &=
            metadata.is_file() && BUNDLE_FILES.iter().any(|name| entry.file_name() == *name);
        count += 1;
    }
    Ok((bytes, recognized && count == BUNDLE_FILES.len()))
}

fn prune_root(
    root: &File,
    active: Option<&std::ffi::OsStr>,
    policy: RetentionPolicy,
    now: Duration,
) -> io::Result<()> {
    let root_path = fd_path(root);
    let device = root.metadata()?.dev();
    let mut total = 0_u64;
    let mut oldest = BinaryHeap::new();
    for entry in fs::read_dir(&root_path)? {
        let entry = entry?;
        let name = entry.file_name();
        if !name.to_string_lossy().starts_with("incident-") {
            continue;
        }
        let Ok(directory) = open_directory(&entry.path()) else {
            continue;
        };
        let metadata = directory.metadata()?;
        if metadata.dev() != device {
            continue;
        }
        let path = fd_path(&directory);
        let (bytes, complete) = bundle_size(&path)?;
        total = total.saturating_add(bytes);
        if !complete || active == Some(name.as_os_str()) {
            continue;
        }
        let Some(recovered_at) = recovery_time(&path, now) else {
            continue;
        };
        oldest.push(Candidate {
            recovered_at,
            name,
            bytes,
            inode: metadata.ino(),
        });
        if oldest.len() > MAX_BATCH {
            oldest.pop();
        }
    }
    for candidate in oldest.into_sorted_vec() {
        if now.saturating_sub(candidate.recovered_at) < policy.max_age && total <= policy.max_bytes
        {
            break;
        }
        if remove_bundle(&root_path, device, &candidate)? {
            total = total.saturating_sub(candidate.bytes);
        }
    }
    Ok(())
}

fn remove_bundle(root: &Path, device: u64, candidate: &Candidate) -> io::Result<bool> {
    let path = root.join(&candidate.name);
    let directory = open_directory(&path)?;
    let metadata = directory.metadata()?;
    if metadata.dev() != device || metadata.ino() != candidate.inode {
        return Ok(false);
    }
    let pinned = fd_path(&directory);
    let (bytes, complete) = bundle_size(&pinned)?;
    if !complete || bytes != candidate.bytes {
        return Ok(false);
    }
    // Never recurse into unknown content; incomplete bundles remain protected after IO failure.
    for name in BUNDLE_FILES {
        fs::remove_file(pinned.join(name))?;
    }
    fs::remove_dir(path)?;
    Ok(true)
}

#[cfg(test)]
mod tests;
