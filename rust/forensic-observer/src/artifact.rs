//! Removable-storage discovery, lease coordination, and incident persistence.

use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use fs2::FileExt;
use serde::Serialize;
use serde_json::Value;

use crate::config::ObserverConfig;
use crate::error::{ObserverError, Result};
use crate::ini::read_bounded_text;
use crate::snapshot::{ForensicSnapshot, slug_text};

/// Default lock shared with the independent removable-storage monitor.
pub const DEFAULT_STORAGE_LOCK_PATH: &str = "/run/lock/removable-storage-maintenance.lock";
/// Default Linux mount table.
pub const DEFAULT_MOUNTS_PATH: &str = "/proc/mounts";
const DEFAULT_FORENSIC_SUBDIR: &str = "venus-evcharger-forensics";
const SNAPSHOT_FILENAME: &str = "snapshot.json";
const REDACTED_CONFIG_FILENAME: &str = "config.redacted.ini";
const RECOVERY_FILENAME: &str = "recovery.json";
const WRITE_PROBE_FILENAME: &str = ".write-test";
const MAX_MOUNTS_BYTES: u64 = 1_048_576;
const SECRET_KEYS: [&str; 4] = ["password", "token", "secret", "auth"];
const MOUNT_PREFIXES: [&str; 3] = ["/media/", "/run/media/", "/mnt/"];
const DEVICE_PREFIXES: [&str; 3] = ["/dev/sd", "/dev/mmcblk", "/dev/disk/"];

/// Non-blocking shared lease held while one incident writes to removable storage.
pub struct StorageLease {
    file: File,
}

impl StorageLease {
    /// Try to acquire a shared lease; return `None` while maintenance is exclusive.
    ///
    /// # Errors
    ///
    /// Returns an error when the lock directory or file cannot be opened, or
    /// when the operating system rejects the lock operation.
    pub fn try_acquire(path: &Path) -> Result<Option<Self>> {
        if let Some(parent) = path.parent().filter(|value| !value.as_os_str().is_empty()) {
            fs::create_dir_all(parent)
                .map_err(|error| ObserverError::storage("create storage-lock directory", &error))?;
        }
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .mode(0o600)
            .open(path)
            .map_err(|error| ObserverError::storage("open storage lock", &error))?;
        match FileExt::try_lock_shared(&file) {
            Ok(()) => Ok(Some(Self { file })),
            Err(error) if error.kind() == ErrorKind::WouldBlock => Ok(None),
            Err(error) => Err(ObserverError::storage("acquire storage lock", &error)),
        }
    }
}

impl Drop for StorageLease {
    fn drop(&mut self) {
        let _ignored = FileExt::unlock(&self.file);
    }
}

/// Read a bounded mount-table snapshot or return an empty string.
#[must_use]
pub fn read_mounts(path: &Path) -> String {
    read_bounded_text(path, MAX_MOUNTS_BYTES, "mount table").unwrap_or_default()
}

/// Return removable-storage mount points accepted for forensic artifacts.
#[must_use]
pub fn mounted_storage_candidates(text: &str) -> Vec<PathBuf> {
    text.lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let device = fields.next()?;
            let mount = fields.next()?.replace("\\040", " ");
            (DEVICE_PREFIXES
                .iter()
                .any(|prefix| device.starts_with(prefix))
                && MOUNT_PREFIXES
                    .iter()
                    .any(|prefix| mount.starts_with(prefix)))
            .then(|| PathBuf::from(mount))
        })
        .collect()
}

/// Return the first candidate that permits a small write probe.
#[must_use]
pub fn first_writable_log_dir(candidates: &[PathBuf]) -> Option<PathBuf> {
    candidates.iter().find_map(|candidate| {
        let directory = candidate.join(DEFAULT_FORENSIC_SUBDIR);
        let probe = directory.join(WRITE_PROBE_FILENAME);
        let result = fs::create_dir_all(&directory)
            .and_then(|()| File::create(&probe))
            .and_then(|_file| fs::remove_file(&probe));
        result.is_ok().then_some(directory)
    })
}

/// Redact secret-bearing INI assignments while preserving line structure.
#[must_use]
pub fn redact_config_text(text: &str) -> String {
    let mut output = text
        .lines()
        .map(redacted_line)
        .collect::<Vec<_>>()
        .join("\n");
    output.push('\n');
    output
}

/// Persist one incident under a held storage lease and current mount snapshot.
///
/// # Errors
///
/// Returns an error when lock coordination, mount validation, serialization,
/// or atomic artifact persistence fails.
pub fn write_incident_with_lease(
    config: &ObserverConfig,
    snapshot: &ForensicSnapshot,
    reasons: &[String],
    mounts_path: &Path,
    lock_path: &Path,
) -> Result<Option<PathBuf>> {
    let Some(_lease) = StorageLease::try_acquire(lock_path)? else {
        return Ok(None);
    };
    let mounts = read_mounts(mounts_path);
    let candidates = mounted_storage_candidates(&mounts);
    let Some(log_dir) = first_writable_log_dir(&candidates) else {
        return Ok(None);
    };
    write_incident(&log_dir, config, snapshot, reasons).map(Some)
}

/// Add one atomic recovery record to the incident that opened an episode.
///
/// The incident path must still belong to a recognized mounted removable
/// storage device. A busy maintenance lock or unavailable original incident
/// directory is reported as a successful deferral.
///
/// # Errors
///
/// Returns an error when lock coordination, serialization, or atomic
/// persistence fails.
pub(crate) fn write_recovery_with_lease(
    incident_path: &Path,
    incident_started_at: f64,
    initial_reasons: &[String],
    snapshot: &ForensicSnapshot,
    duration: Duration,
    mounts_path: &Path,
    lock_path: &Path,
) -> Result<bool> {
    let Some(_lease) = StorageLease::try_acquire(lock_path)? else {
        return Ok(false);
    };
    let mounts = read_mounts(mounts_path);
    let candidates = mounted_storage_candidates(&mounts);
    let accepted_parent = candidates
        .iter()
        .map(|candidate| candidate.join(DEFAULT_FORENSIC_SUBDIR))
        .any(|directory| incident_path.parent() == Some(directory.as_path()));
    if !accepted_parent || !incident_path.is_dir() {
        return Ok(false);
    }
    write_recovery(
        incident_path,
        incident_started_at,
        initial_reasons,
        snapshot,
        duration,
    )?;
    Ok(true)
}

/// Write one immutable, atomically published incident bundle.
///
/// # Errors
///
/// Returns an error for invalid timestamps or failed directory, encoding, and
/// atomic publication operations.
pub fn write_incident(
    log_dir: &Path,
    config: &ObserverConfig,
    snapshot: &ForensicSnapshot,
    reasons: &[String],
) -> Result<PathBuf> {
    if !snapshot.timestamp.is_finite() {
        return Err(ObserverError::Storage(
            "forensic snapshot timestamp must be finite".to_owned(),
        ));
    }
    fs::create_dir_all(log_dir)
        .map_err(|error| ObserverError::storage("create forensic directory", &error))?;
    let base_name = incident_name(snapshot.timestamp, reasons);
    let final_path = unique_incident_path(log_dir, &base_name);
    let staging_path = log_dir.join(format!(".{base_name}.staging-{}", std::process::id()));
    if staging_path.exists() {
        fs::remove_dir_all(&staging_path)
            .map_err(|error| ObserverError::storage("remove stale incident staging", &error))?;
    }
    fs::create_dir(&staging_path)
        .map_err(|error| ObserverError::storage("create incident staging", &error))?;
    let result = write_incident_files(&staging_path, config, snapshot, reasons).and_then(|()| {
        fs::rename(&staging_path, &final_path)
            .map_err(|error| ObserverError::storage("publish incident", &error))
    });
    if result.is_err() {
        let _ignored = fs::remove_dir_all(&staging_path);
    }
    result.map(|()| final_path)
}

fn write_incident_files(
    directory: &Path,
    config: &ObserverConfig,
    snapshot: &ForensicSnapshot,
    reasons: &[String],
) -> Result<()> {
    let mut payload = snapshot.to_value()?;
    let object = payload.as_object_mut().ok_or_else(|| {
        ObserverError::Storage("snapshot did not serialize as an object".to_owned())
    })?;
    object.insert(
        "reasons".to_owned(),
        Value::Array(reasons.iter().cloned().map(Value::String).collect()),
    );
    let mut snapshot_file = File::create(directory.join(SNAPSHOT_FILENAME))
        .map_err(|error| ObserverError::storage("create snapshot artifact", &error))?;
    serde_json::to_writer_pretty(&mut snapshot_file, &payload)
        .map_err(|error| ObserverError::Storage(format!("encode snapshot artifact: {error}")))?;
    snapshot_file
        .write_all(b"\n")
        .map_err(|error| ObserverError::storage("finish snapshot artifact", &error))?;
    fs::write(
        directory.join(REDACTED_CONFIG_FILENAME),
        redact_config_text(&config.source_text),
    )
    .map_err(|error| ObserverError::storage("write redacted config", &error))?;
    Ok(())
}

#[derive(Serialize)]
struct RecoveryRecord<'a> {
    schema_version: u8,
    state: &'static str,
    incident_started_at: f64,
    recovered_at: f64,
    duration_seconds: f64,
    initial_reasons: &'a [String],
}

fn write_recovery(
    incident_path: &Path,
    incident_started_at: f64,
    initial_reasons: &[String],
    snapshot: &ForensicSnapshot,
    duration: Duration,
) -> Result<()> {
    if !incident_started_at.is_finite() || !snapshot.timestamp.is_finite() {
        return Err(ObserverError::Storage(
            "forensic recovery timestamps must be finite".to_owned(),
        ));
    }
    let final_path = incident_path.join(RECOVERY_FILENAME);
    if final_path.is_file() {
        return Ok(());
    }
    let staging_path = incident_path.join(format!(
        ".{RECOVERY_FILENAME}.staging-{}",
        std::process::id()
    ));
    let mut file = File::create(&staging_path)
        .map_err(|error| ObserverError::storage("create recovery staging artifact", &error))?;
    let record = RecoveryRecord {
        schema_version: 1,
        state: "recovered",
        incident_started_at,
        recovered_at: snapshot.timestamp,
        duration_seconds: duration.as_secs_f64(),
        initial_reasons,
    };
    let result = serde_json::to_writer_pretty(&mut file, &record)
        .map_err(|error| ObserverError::Storage(format!("encode recovery artifact: {error}")))
        .and_then(|()| {
            file.write_all(b"\n")
                .map_err(|error| ObserverError::storage("finish recovery artifact", &error))
        })
        .and_then(|()| {
            fs::rename(&staging_path, &final_path)
                .map_err(|error| ObserverError::storage("publish recovery artifact", &error))
        });
    if result.is_err() {
        let _ignored = fs::remove_file(&staging_path);
    }
    result
}

fn redacted_line(line: &str) -> String {
    let Some(separator) = line.find('=').or_else(|| line.find(':')) else {
        return line.to_owned();
    };
    let key = &line[..separator];
    let delimiter = &line[separator..=separator];
    let normalized = key.trim().to_ascii_lowercase();
    if SECRET_KEYS.iter().any(|secret| normalized.contains(secret)) {
        format!("{key}{delimiter}<redacted>")
    } else {
        line.to_owned()
    }
}

fn incident_name(timestamp: f64, reasons: &[String]) -> String {
    const MAX_SUPPORTED_EPOCH_SECONDS: i64 = 253_402_300_799;
    const MAX_SUPPORTED_EPOCH_SECONDS_F64: f64 = 253_402_300_799.0;
    let bounded = timestamp.clamp(0.0, MAX_SUPPORTED_EPOCH_SECONDS_F64);
    let seconds = i64::try_from(Duration::from_secs_f64(bounded).as_secs())
        .unwrap_or(MAX_SUPPORTED_EPOCH_SECONDS);
    let stamp = utc_stamp(seconds);
    let reason_slug = slug_text(&reasons.join("-"));
    let bounded_slug = reason_slug.chars().take(80).collect::<String>();
    format!("incident-{stamp}-{bounded_slug}")
}

fn unique_incident_path(directory: &Path, base_name: &str) -> PathBuf {
    let direct = directory.join(base_name);
    if !direct.exists() {
        return direct;
    }
    (2_u32..=u32::MAX)
        .map(|suffix| directory.join(format!("{base_name}-{suffix}")))
        .find(|candidate| !candidate.exists())
        .unwrap_or(direct)
}

fn utc_stamp(seconds: i64) -> String {
    let days = seconds.div_euclid(86_400);
    let day_seconds = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = day_seconds / 3_600;
    let minute = day_seconds % 3_600 / 60;
    let second = day_seconds % 60;
    format!("{year:04}{month:02}{day:02}-{hour:02}{minute:02}{second:02}")
}

fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let shifted = days_since_epoch + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::{
        StorageLease, mounted_storage_candidates, redact_config_text, utc_stamp, write_incident,
        write_recovery,
    };
    use crate::config::ObserverConfig;
    use crate::ini::IniDocument;
    use crate::snapshot::ForensicSnapshot;
    use fs2::FileExt;
    use std::fs::{self, OpenOptions};
    use std::path::PathBuf;
    use tempfile::tempdir;

    #[test]
    fn removable_mount_filter_matches_the_existing_contract() {
        let mounts = concat!(
            "/dev/root /media/root ext4 rw 0 0\n",
            "/dev/sdb1 /srv/not-removable ext4 rw 0 0\n",
            "/dev/sda1 /media/Card\\040One vfat rw 0 0\n",
            "/dev/mmcblk0p1 /run/media/card ext4 rw 0 0\n",
            "/dev/disk/by-id/x /mnt/archive ext4 rw 0 0\n",
        );
        assert_eq!(
            mounted_storage_candidates(mounts),
            vec![
                PathBuf::from("/media/Card One"),
                PathBuf::from("/run/media/card"),
                PathBuf::from("/mnt/archive")
            ]
        );
    }

    #[test]
    fn secret_redaction_is_case_insensitive_and_line_preserving() {
        assert_eq!(
            redact_config_text("plain\nHost=x\nPassword=p=tail\nAPI_TOKEN:t\nAuthorization=a\n"),
            "plain\nHost=x\nPassword=<redacted>\nAPI_TOKEN:<redacted>\nAuthorization=<redacted>\n"
        );
        assert_eq!(redact_config_text(""), "\n");
    }

    #[test]
    fn incident_timestamp_is_stable_utc() {
        assert_eq!(utc_stamp(0), "19700101-000000");
        assert_eq!(utc_stamp(951_827_696), "20000229-123456");
    }

    #[test]
    fn exclusive_storage_maintenance_defers_the_observer() {
        let directory = tempdir();
        assert!(directory.is_ok());
        let Some(directory) = directory.ok() else {
            return;
        };
        let lock_path = directory.path().join("maintenance.lock");
        let owner = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(&lock_path);
        assert!(owner.is_ok());
        let Some(owner) = owner.ok() else {
            return;
        };
        assert!(FileExt::lock_exclusive(&owner).is_ok());
        assert!(matches!(StorageLease::try_acquire(&lock_path), Ok(None)));
        assert!(FileExt::unlock(&owner).is_ok());
        assert!(matches!(StorageLease::try_acquire(&lock_path), Ok(Some(_))));
    }

    #[test]
    fn incident_bundle_is_atomic_redacted_and_collision_safe() {
        let directory = tempdir();
        assert!(directory.is_ok());
        let Some(directory) = directory.ok() else {
            return;
        };
        let source = "[DEFAULT]\nPassword=do-not-persist\nControlApiAdminToken:also-secret\n";
        let config = ObserverConfig {
            path: directory.path().join("config.ini"),
            source_text: source.to_owned(),
            ini: IniDocument::parse(source).unwrap_or_default(),
        };
        let mut snapshot = ForensicSnapshot::collect(&config);
        snapshot.timestamp = 951_827_696.0;
        let reasons = vec!["contract-failure".to_owned()];
        let first = write_incident(directory.path(), &config, &snapshot, &reasons);
        let second = write_incident(directory.path(), &config, &snapshot, &reasons);
        assert!(first.is_ok());
        assert!(second.is_ok());
        let paths = [first.ok(), second.ok()];
        assert!(paths.iter().all(Option::is_some));
        assert_ne!(paths[0], paths[1]);
        let Some(first_path) = paths[0].as_ref() else {
            return;
        };
        let redacted = fs::read_to_string(first_path.join("config.redacted.ini"));
        assert!(redacted.is_ok());
        let redacted = redacted.unwrap_or_default();
        assert!(!redacted.contains("do-not-persist"));
        assert!(!redacted.contains("also-secret"));
        assert!(redacted.contains("Password=<redacted>"));
        assert!(first_path.join("snapshot.json").is_file());
        let mut recovery_snapshot = snapshot.clone();
        recovery_snapshot.timestamp = 951_827_756.0;
        assert!(
            write_recovery(
                first_path,
                snapshot.timestamp,
                &reasons,
                &recovery_snapshot,
                std::time::Duration::from_secs(60),
            )
            .is_ok()
        );
        assert!(
            write_recovery(
                first_path,
                snapshot.timestamp,
                &reasons,
                &recovery_snapshot,
                std::time::Duration::from_secs(60),
            )
            .is_ok()
        );
        let recovery = fs::read_to_string(first_path.join("recovery.json"));
        assert!(recovery.is_ok());
        let recovery: serde_json::Value = recovery
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default();
        assert_eq!(recovery["schema_version"], 1);
        assert_eq!(recovery["state"], "recovered");
        assert_eq!(recovery["incident_started_at"], 951_827_696.0);
        assert_eq!(recovery["recovered_at"], 951_827_756.0);
        assert_eq!(recovery["duration_seconds"], 60.0);
        assert_eq!(recovery["initial_reasons"][0], "contract-failure");
        let entries = fs::read_dir(directory.path());
        assert!(entries.is_ok());
        assert!(entries.ok().is_some_and(|mut values| values.all(|entry| {
            entry
                .ok()
                .is_some_and(|value| !value.file_name().to_string_lossy().contains(".staging-"))
        })));
    }
}
