//! Recovery records for completed forensic incident episodes.

use std::fs::{self, File};
use std::io::Write;
use std::path::Path;
use std::time::Duration;

use serde::Serialize;

use crate::artifact::{
    DEFAULT_FORENSIC_SUBDIR, RECOVERY_FILENAME, StorageLease, mounted_storage_candidates,
    read_mounts,
};
use crate::error::{ObserverError, Result};
use crate::snapshot::ForensicSnapshot;

/// Add one atomic recovery record to the incident that opened an episode.
///
/// The incident path must still belong to a recognized mounted removable
/// storage device. A busy maintenance lock or unavailable original incident
/// directory is reported as a successful deferral.
pub fn write_recovery_with_lease(
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

#[derive(Serialize)]
struct RecoveryRecord<'a> {
    schema_version: u8,
    state: &'static str,
    incident_started_at: f64,
    recovered_at: f64,
    duration_seconds: f64,
    initial_reasons: &'a [String],
}

/// Persist one idempotent recovery record inside an existing incident.
pub fn write_recovery(
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
