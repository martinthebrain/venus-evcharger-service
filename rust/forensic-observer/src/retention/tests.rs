//! Retention contracts, including protected artifacts and storage coordination.

use super::*;
use std::os::unix::fs::symlink;
use tempfile::tempdir;

type TestResult = std::result::Result<(), Box<dyn std::error::Error>>;

fn policy(age: u64, bytes: u64) -> RetentionPolicy {
    RetentionPolicy {
        max_age: Duration::from_secs(age),
        max_bytes: bytes,
    }
}

fn bundle(root: &Path, name: &str, recovered_at: u64) -> io::Result<PathBuf> {
    let path = root.join(name);
    fs::create_dir(&path)?;
    fs::write(path.join("snapshot.json"), "{}")?;
    fs::write(path.join("config.redacted.ini"), "[DEFAULT]\n")?;
    fs::write(
        path.join("recovery.json"),
        format!(
            "{{\"schema_version\":1,\"state\":\"recovered\",\"incident_started_at\":0,\"recovered_at\":{recovered_at}}}"
        ),
    )?;
    Ok(path)
}

#[test]
fn defaults_and_strict_config_bounds() -> TestResult {
    let defaults = RetentionPolicy::from_ini(&IniDocument::parse("[DEFAULT]\n")?)?;
    assert_eq!(defaults, policy(30 * 86_400, 100 * 1_048_576));
    let configured = RetentionPolicy::from_ini(&IniDocument::parse(
        "[DEFAULT]\nForensicRetentionDays=7\nForensicMaxTotalMiB=20\n",
    )?)?;
    assert_eq!(configured, policy(7 * 86_400, 20 * 1_048_576));
    for key in ["ForensicRetentionDays", "ForensicMaxTotalMiB"] {
        for invalid in ["", "0", "-1", "1.5", "invalid", "18446744073709551615"] {
            let ini = IniDocument::parse(&format!("[DEFAULT]\n{key}={invalid}\n"))?;
            assert!(
                RetentionPolicy::from_ini(&ini).is_err(),
                "{key} accepted invalid input"
            );
        }
    }
    let directory = tempdir()?;
    let config = directory.path().join("config.ini");
    fs::write(&config, "[DEFAULT]\nForensicRetentionDays=0\n")?;
    assert!(crate::runtime::validate_config(&config).is_err());
    Ok(())
}

#[test]
fn schedule_is_monotonic_and_runs_at_most_hourly() {
    let now = Instant::now();
    let mut schedule = RetentionSchedule::default();
    assert!(schedule.due(now));
    assert!(!schedule.due(now));
    assert!(!schedule.due(now + Duration::from_secs(3_599)));
    assert!(schedule.due(now + CHECK_INTERVAL));
    assert!(!schedule.due(now));
}

#[test]
fn age_uses_recovery_and_deletes_at_boundary() -> TestResult {
    let dir = tempdir()?;
    let expired = bundle(dir.path(), "incident-old", 100)?;
    let recent = bundle(dir.path(), "incident-recent", 101)?;
    let future = bundle(dir.path(), "incident-future", 999)?;
    prune_root(
        &open_directory(dir.path())?,
        None,
        policy(100, u64::MAX),
        Duration::from_secs(200),
    )?;
    assert!(!expired.exists());
    assert!(recent.exists());
    assert!(future.exists());
    Ok(())
}

#[test]
fn byte_budget_removes_oldest_completed_bundle_first() -> TestResult {
    let dir = tempdir()?;
    let old = bundle(dir.path(), "incident-z-old", 1)?;
    let new = bundle(dir.path(), "incident-a-new", 2)?;
    let (size, _) = bundle_size(&new)?;
    prune_root(
        &open_directory(dir.path())?,
        None,
        policy(1_000, size),
        Duration::from_secs(10),
    )?;
    assert!(!old.exists());
    assert!(new.exists());
    Ok(())
}

#[test]
fn active_open_unknown_and_malformed_bundles_are_protected() -> TestResult {
    let dir = tempdir()?;
    let active = bundle(dir.path(), "incident-active", 1)?;
    let open = bundle(dir.path(), "incident-open", 1)?;
    fs::remove_file(open.join("recovery.json"))?;
    let unknown = bundle(dir.path(), "incident-unknown", 1)?;
    fs::write(unknown.join("keep.txt"), "foreign content")?;
    let nested = bundle(dir.path(), "incident-nested", 1)?;
    fs::create_dir(nested.join("nested"))?;
    let malformed = bundle(dir.path(), "incident-malformed", 1)?;
    fs::write(malformed.join("recovery.json"), "{}")?;
    let oversized = bundle(dir.path(), "incident-oversized", 1)?;
    fs::write(oversized.join("recovery.json"), vec![b' '; 8_193])?;
    let foreign = bundle(dir.path(), "unrelated", 1)?;
    let eligible = bundle(dir.path(), "incident-eligible", 1)?;
    prune_root(
        &open_directory(dir.path())?,
        active.file_name(),
        policy(1, 1),
        Duration::from_secs(10),
    )?;
    for path in [active, open, unknown, nested, malformed, oversized, foreign] {
        assert!(path.exists());
    }
    assert!(!eligible.exists());
    Ok(())
}

#[test]
fn symlinks_are_neither_followed_nor_removed() -> TestResult {
    let dir = tempdir()?;
    let outside = tempdir()?;
    let target = bundle(outside.path(), "keep", 1)?;
    symlink(&target, dir.path().join("incident-link"))?;
    let linked_file = bundle(dir.path(), "incident-file-link", 1)?;
    fs::remove_file(linked_file.join("recovery.json"))?;
    symlink(
        target.join("recovery.json"),
        linked_file.join("recovery.json"),
    )?;
    prune_root(
        &open_directory(dir.path())?,
        None,
        policy(1, 1),
        Duration::from_secs(10),
    )?;
    assert!(target.join("recovery.json").exists());
    assert!(dir.path().join("incident-link").is_symlink());
    assert!(linked_file.join("recovery.json").is_symlink());
    symlink(outside.path(), dir.path().join(DEFAULT_FORENSIC_SUBDIR))?;
    prune_mount(
        dir.path(),
        None,
        policy(1, 1),
        Duration::from_secs(10),
        || true,
    )?;
    assert!(target.exists());
    Ok(())
}

#[test]
fn invalid_recovery_contracts_never_authorize_deletion() -> TestResult {
    let dir = tempdir()?;
    let path = bundle(dir.path(), "incident-test", 1)?;
    for record in [
        r#"{"schema_version":2,"state":"recovered","incident_started_at":0,"recovered_at":1}"#,
        r#"{"schema_version":1,"state":"active","incident_started_at":0,"recovered_at":1}"#,
        r#"{"schema_version":1,"state":"recovered","incident_started_at":2,"recovered_at":1}"#,
        r#"{"schema_version":1,"state":"recovered","incident_started_at":-1,"recovered_at":1}"#,
        r#"{"schema_version":1,"state":"recovered","incident_started_at":0,"recovered_at":1e100}"#,
    ] {
        fs::write(path.join("recovery.json"), record)?;
        assert_eq!(recovery_time(&path, Duration::from_secs(10)), None);
    }
    Ok(())
}

#[test]
fn cleanup_is_bounded_and_continues_on_next_pass() -> TestResult {
    let dir = tempdir()?;
    for index in 0..MAX_BATCH + 2 {
        bundle(dir.path(), &format!("incident-{index}"), 1)?;
    }
    let root = open_directory(dir.path())?;
    prune_root(&root, None, policy(1, 1), Duration::from_secs(10))?;
    assert_eq!(fs::read_dir(dir.path())?.count(), 2);
    prune_root(&root, None, policy(1, 1), Duration::from_secs(10))?;
    assert_eq!(fs::read_dir(dir.path())?.count(), 0);
    Ok(())
}

#[test]
fn missing_storage_and_changed_mount_do_not_create_or_delete() -> TestResult {
    let dir = tempdir()?;
    let missing = dir.path().join("missing");
    prune_mount(
        &missing,
        None,
        policy(1, 1),
        Duration::from_secs(10),
        || true,
    )?;
    assert!(!missing.exists());
    prune_mount(
        dir.path(),
        None,
        policy(1, 1),
        Duration::from_secs(10),
        || true,
    )?;
    let root = dir.path().join(DEFAULT_FORENSIC_SUBDIR);
    assert!(!root.exists());
    fs::create_dir(&root)?;
    let incident = bundle(&root, "incident-keep", 1)?;
    prune_mount(
        dir.path(),
        None,
        policy(1, 1),
        Duration::from_secs(10),
        || false,
    )?;
    assert!(incident.exists());
    prune_mount(
        dir.path(),
        Some(&incident),
        policy(1, 1),
        Duration::from_secs(10),
        || true,
    )?;
    assert!(incident.exists());
    Ok(())
}

#[test]
fn exclusive_cleanup_lease_defers_to_writers_and_maintenance() -> TestResult {
    let dir = tempdir()?;
    let lock = dir.path().join("lock");
    let writer = StorageLease::try_acquire(&lock)?;
    assert!(writer.is_some());
    assert!(StorageLease::try_acquire_exclusive(&lock)?.is_none());
    prune(
        policy(1, 1),
        &dir.path().join("missing-mounts"),
        &lock,
        None,
    )?;
    drop(writer);
    let maintenance = StorageLease::try_acquire_exclusive(&lock)?;
    assert!(maintenance.is_some());
    assert!(StorageLease::try_acquire(&lock)?.is_none());
    assert!(StorageLease::try_acquire_exclusive(&lock)?.is_none());
    drop(maintenance);
    assert!(StorageLease::try_acquire(&lock)?.is_some());
    Ok(())
}

#[test]
fn open_directory_keeps_cleanup_on_original_filesystem_path() -> TestResult {
    let dir = tempdir()?;
    let root_path = dir.path().join("root");
    fs::create_dir(&root_path)?;
    bundle(&root_path, "incident-old", 1)?;
    let root = open_directory(&root_path)?;
    let moved = dir.path().join("moved");
    fs::rename(&root_path, &moved)?;
    fs::create_dir(&root_path)?;
    let replacement = bundle(&root_path, "incident-old", 1)?;
    prune_root(&root, None, policy(1, 1), Duration::from_secs(10))?;
    assert!(replacement.exists());
    assert!(!moved.join("incident-old").exists());
    Ok(())
}

#[test]
fn changed_bundle_identity_or_content_cancels_deletion() -> TestResult {
    let dir = tempdir()?;
    let path = bundle(dir.path(), "incident-test", 1)?;
    let metadata = path.metadata()?;
    let candidate = Candidate {
        recovered_at: Duration::from_secs(1),
        name: OsString::from("incident-test"),
        bytes: bundle_size(&path)?.0,
        inode: metadata.ino(),
    };
    fs::rename(&path, dir.path().join("saved"))?;
    let replacement = bundle(dir.path(), "incident-test", 1)?;
    assert!(!remove_bundle(dir.path(), metadata.dev(), &candidate)?);
    assert!(replacement.join("snapshot.json").exists());
    let candidate = Candidate {
        inode: replacement.metadata()?.ino(),
        ..candidate
    };
    fs::write(replacement.join("extra.txt"), "do not remove")?;
    assert!(!remove_bundle(dir.path(), metadata.dev(), &candidate)?);
    assert!(replacement.join("snapshot.json").exists());
    Ok(())
}
