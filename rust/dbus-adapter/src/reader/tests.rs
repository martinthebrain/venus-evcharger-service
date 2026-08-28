// SPDX-License-Identifier: GPL-3.0-or-later

use std::time::{Duration, Instant};

use super::{EnergyReader, ReadKey, ReadMode};
use crate::broker::{DbusOperation, DbusResultValue};
use crate::config::IniConfig;
use crate::dbus::BusValue;
use crate::energy::{Clocks, MeasurementStatus};

fn reader() -> Result<EnergyReader, String> {
    IniConfig::parse(
        "[DEFAULT]\n\
AutoGridL1Path=\n\
AutoGridL2Path=\n\
AutoGridL3Path=\n\
AutoPvService=com.victronenergy.pvinverter.test\n\
AutoUseDcPv=false\n\
AutoBatteryService=\n\
AutoBatteryServicePrefix=\n\
DbusGatewayServiceListIntervalSeconds=900\n\
DbusGatewayMissingPvDiscoveryIntervalSeconds=60\n",
    )
    .map(|config| EnergyReader::from_config(&config))
}

#[test]
fn unvalidated_pv_uses_early_discovery_until_one_numeric_read_succeeds() -> Result<(), String> {
    let mut reader = reader()?;
    reader.handle_names(Ok(DbusResultValue::Names(vec![
        "com.victronenergy.pvinverter.test".to_owned(),
    ])))?;
    assert_eq!(reader.active_discovery_interval, Duration::from_secs(60));

    let operation = (0..2)
        .find_map(|_| reader.next_operation(ReadMode::Degraded))
        .ok_or_else(|| "PV read was not scheduled".to_owned())?;
    let DbusOperation::Read { service, path } = operation else {
        return Err("PV read had the wrong operation kind".to_owned());
    };
    reader.handle_read_member(
        &service,
        &path,
        Ok(DbusResultValue::Value(crate::dbus::BusValue::F64(500.0))),
    )?;
    reader.handle_names(Ok(DbusResultValue::Names(vec![service])))?;
    assert_eq!(reader.active_discovery_interval, Duration::from_secs(900));
    Ok(())
}

#[test]
fn failed_critical_reads_back_off_without_becoming_unbounded() -> Result<(), String> {
    let config = IniConfig::parse(
        "[DEFAULT]\nAutoGridL1Path=/Ac/Grid/L1/Power\nAutoGridL2Path=\nAutoGridL3Path=\nAutoUseDcPv=false\nAutoPvServicePrefix=\nAutoBatteryService=\nAutoBatteryServicePrefix=\n",
    )?;
    let mut reader = EnergyReader::from_config(&config);
    let now = std::time::Instant::now();
    reader.start_cycle(ReadKey::Grid, now, 1.0);
    reader.handle_read_member(
        "com.victronenergy.system",
        "/Ac/Grid/L1/Power",
        Err("NoReply".to_owned()),
    )?;
    let first =
        reader.next_due[&ReadKey::Grid].saturating_duration_since(std::time::Instant::now());
    assert!(first >= Duration::from_secs(29));
    assert!(first <= Duration::from_secs(30));

    reader.start_cycle(ReadKey::Grid, std::time::Instant::now(), 1.0);
    reader.handle_read_member(
        "com.victronenergy.system",
        "/Ac/Grid/L1/Power",
        Err("NoReply".to_owned()),
    )?;
    let second =
        reader.next_due[&ReadKey::Grid].saturating_duration_since(std::time::Instant::now());
    assert!(second >= Duration::from_secs(59));
    assert!(second <= Duration::from_secs(60));
    Ok(())
}

#[test]
fn oldest_due_read_wins_instead_of_fixed_group_priority() -> Result<(), String> {
    let mut reader = reader()?;
    let now = Instant::now();
    for key in [
        ReadKey::Grid,
        ReadKey::Pv,
        ReadKey::BatterySoc,
        ReadKey::BatteryPower,
        ReadKey::BatteryCapacityWh,
        ReadKey::BatteryCapacityAh,
        ReadKey::BatteryVoltage,
    ] {
        reader.next_due.insert(key, now + Duration::from_secs(60));
    }
    let one_second_ago = now
        .checked_sub(Duration::from_secs(1))
        .ok_or_else(|| "test clock cannot represent one second ago".to_owned())?;
    let two_seconds_ago = now
        .checked_sub(Duration::from_secs(2))
        .ok_or_else(|| "test clock cannot represent two seconds ago".to_owned())?;
    let three_seconds_ago = now
        .checked_sub(Duration::from_secs(3))
        .ok_or_else(|| "test clock cannot represent three seconds ago".to_owned())?;
    reader.next_due.insert(ReadKey::Grid, one_second_ago);
    reader.next_due.insert(ReadKey::Pv, two_seconds_ago);
    reader
        .next_due
        .insert(ReadKey::BatterySoc, three_seconds_ago);

    assert_eq!(reader.next_due_key(now, true), Some(ReadKey::BatterySoc));
    assert_eq!(reader.next_due_key(now, false), Some(ReadKey::BatterySoc));
    Ok(())
}

#[test]
fn explicit_pv_sleep_remains_bounded_evidence_after_service_disappears() -> Result<(), String> {
    let mut reader = reader()?;
    let service = "com.victronenergy.pvinverter.test";
    reader.handle_names(Ok(DbusResultValue::Names(vec![service.to_owned()])))?;
    reader.start_cycle(ReadKey::Pv, Instant::now(), 1.0);
    reader.handle_read_member(service, "/Ac/Power", Err("inverter asleep".to_owned()))?;

    let (evidence, reasons) = reader.pv_dormancy_health();
    assert_eq!(evidence.as_array().map(Vec::len), Some(1));
    assert_eq!(evidence[0]["reason"], "explicit-dormant-state");
    assert_eq!(
        reasons.values().next().map(String::as_str),
        Some("pv-sleep-confirmed")
    );

    reader.handle_names(Ok(DbusResultValue::Names(Vec::new())))?;
    let topology = reader.topology(100.0)?;
    let pv = topology
        .sources
        .iter()
        .find(|source| source.kind == "pv_ac")
        .ok_or_else(|| "validated sleeping PV source disappeared".to_owned())?;
    assert_eq!(pv.state, "offline");
    assert_eq!(
        reader.pv_dormancy_health().0.as_array().map(Vec::len),
        Some(1)
    );
    Ok(())
}

#[test]
fn transient_pv_failure_uses_the_bounded_last_good_window() -> Result<(), String> {
    let mut reader = reader()?;
    let service = "com.victronenergy.pvinverter.test";
    reader.handle_names(Ok(DbusResultValue::Names(vec![service.to_owned()])))?;
    reader.start_cycle(ReadKey::Pv, Instant::now(), 1.0);
    reader.handle_read_member(
        service,
        "/Ac/Power",
        Ok(DbusResultValue::Value(BusValue::F64(500.0))),
    )?;

    reader.start_cycle(ReadKey::Pv, Instant::now(), 1.0);
    reader.handle_read_member(service, "/Ac/Power", Err("NoReply".to_owned()))?;
    let snapshot = reader.snapshot(Clocks::now()?);
    assert_eq!(snapshot.pv_power_w.status, MeasurementStatus::Stale);
    let held = snapshot
        .pv_power_w
        .value
        .ok_or_else(|| "last-good PV value was not retained".to_owned())?;
    assert!(held > 390.0 && held <= 400.0);
    assert_eq!(snapshot.pv_power_w.reason_code, "transient-hold");
    Ok(())
}
