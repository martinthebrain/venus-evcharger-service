// SPDX-License-Identifier: GPL-3.0-or-later

use std::time::Duration;

use serde_json::Value;

use super::{GatewayHealthMonitor, looks_like_timeout, percentile};
use crate::broker::{DbusOperation, DbusResult};
use crate::energy::Clocks;
use crate::resources::{ResourcePressureEvidence, ResourceSnapshot, ResourceState};

fn clocks(epoch: f64) -> Clocks {
    Clocks {
        epoch,
        monotonic: epoch,
    }
}

fn failed_read(message: &str) -> DbusResult {
    DbusResult {
        operation: DbusOperation::Read {
            service: "com.victronenergy.system".to_owned(),
            path: "/Ac/Grid/L1/Power".to_owned(),
        },
        result: Err(message.to_owned()),
        duration: Duration::from_millis(1_000),
    }
}

fn resources(causes: &[&str]) -> ResourceSnapshot {
    ResourceSnapshot {
        state: if causes.is_empty() {
            ResourceState::Ok
        } else {
            ResourceState::Constrained
        },
        loadavg_1m: Some(3.2),
        loadavg_5m: Some(2.0),
        loadavg_15m: Some(1.0),
        load_per_cpu_1m: Some(1.6),
        system_cpu_pct: Some(if causes.contains(&"cpu") { 92.0 } else { 60.0 }),
        mem_total_kb: Some(512_000.0),
        mem_available_kb: Some(120_000.0),
        process_rss_kb: Some(8_000.0),
        process_threads: Some(2),
        cpu_count: 2,
        pressure_evidence: (!causes.is_empty()).then(|| ResourcePressureEvidence {
            active: true,
            triggered_at: 99.0,
            causes: causes.iter().map(|cause| (*cause).to_owned()).collect(),
            load_per_cpu_1m: Some(1.6),
            system_cpu_pct: Some(92.0),
            mem_available_kb: Some(120_000.0),
        }),
    }
}

#[test]
fn timeout_and_percentile_contracts_are_explicit() {
    assert!(looks_like_timeout(
        "org.freedesktop.DBus.Error.NoReply: timeout"
    ));
    assert!(!looks_like_timeout("unknown object"));
    assert!((percentile(&[1.0, 2.0, 3.0, 4.0], 95, 100) - 4.0).abs() < f64::EPSILON);
}

#[test]
fn third_timeout_degrades_and_sixth_timeout_protects() {
    let mut health = GatewayHealthMonitor::new(clocks(100.0));
    for index in 0..2 {
        health.record_operation(
            &failed_read("NoReply"),
            false,
            clocks(101.0 + f64::from(index)),
        );
    }
    assert_eq!(health.operational_state(), "ok");

    health.record_operation(&failed_read("NoReply"), false, clocks(103.0));
    assert_eq!(health.operational_state(), "degraded");

    for index in 0..3 {
        health.record_operation(
            &failed_read("NoReply"),
            false,
            clocks(104.0 + f64::from(index)),
        );
    }
    assert_eq!(health.operational_state(), "protective");
    let snapshot = health.snapshot(&resources(&[]), clocks(107.0));
    assert_eq!(snapshot.timeouts_60s, 6);
    assert_eq!(snapshot.active_protective_trigger["timeout_count_60s"], 6);
}

#[test]
fn optional_pv_timeouts_never_trip_the_circuit() {
    let mut health = GatewayHealthMonitor::new(clocks(100.0));
    for index in 0..10 {
        health.record_operation(
            &failed_read("NoReply"),
            true,
            clocks(101.0 + f64::from(index)),
        );
    }
    assert_eq!(health.operational_state(), "ok");
    let snapshot = health.snapshot(&resources(&[]), clocks(112.0));
    assert_eq!(snapshot.timeouts_60s, 0);
    assert_eq!(snapshot.errors_60s, 0);
    assert_eq!(snapshot.active_protective_trigger, Value::Null);
    assert_eq!(snapshot.operations["optional_read"]["samples_60s"], 10);
}

#[test]
fn load_pressure_throttles_without_degrading_but_cpu_pressure_is_protective() {
    let mut load_health = GatewayHealthMonitor::new(clocks(100.0));
    let load = load_health.snapshot(&resources(&["load"]), clocks(101.0));
    assert_eq!(load.performance_state, "ok");
    assert_eq!(load.state, "ok");
    assert_eq!(load.protective_cause, "");

    let mut cpu_health = GatewayHealthMonitor::new(clocks(100.0));
    let cpu = cpu_health.snapshot(&resources(&["load", "cpu"]), clocks(101.0));
    assert_eq!(cpu.performance_state, "protective");
    assert_eq!(cpu.state, "protective");
    assert_eq!(cpu.protective_cause, "resource-cpu");
}

#[test]
fn historical_cpu_peak_does_not_hold_protection_when_only_load_remains_high() {
    let mut sample = resources(&["load", "cpu"]);
    let mut health = GatewayHealthMonitor::new(clocks(100.0));
    assert_eq!(
        health.snapshot(&sample, clocks(101.0)).performance_state,
        "protective"
    );
    sample.system_cpu_pct = Some(60.0);
    let recovering = health.snapshot(&sample, clocks(102.0));
    assert_eq!(recovering.performance_state, "ok");
    assert!(recovering.state_recovery_pending);
    assert_eq!(recovering.protective_cause, "recovery-hold");
    assert_eq!(
        sample.pressure_evidence.as_ref().map(|e| e.causes.len()),
        Some(2)
    );
    sample.system_cpu_pct = None;
    assert_eq!(
        health.snapshot(&sample, clocks(103.0)).performance_state,
        "protective"
    );
    sample.system_cpu_pct = Some(85.0);
    assert_eq!(
        health.snapshot(&sample, clocks(104.0)).performance_state,
        "protective"
    );
}

#[test]
fn memory_protection_holds_until_its_own_exit_threshold() {
    let mut sample = resources(&["load", "cpu", "memory"]);
    sample.system_cpu_pct = Some(60.0);
    sample.mem_available_kb = Some(40_959.0);
    let mut health = GatewayHealthMonitor::new(clocks(100.0));
    assert_eq!(
        health.snapshot(&sample, clocks(101.0)).protective_cause,
        "resource-memory"
    );
    sample.mem_available_kb = Some(40_960.0);
    assert_eq!(
        health.snapshot(&sample, clocks(102.0)).performance_state,
        "ok"
    );
}

#[test]
fn new_critical_pressure_is_not_hidden_by_an_older_different_trigger() {
    let mut sample = resources(&["load", "memory"]);
    sample.system_cpu_pct = Some(95.0);
    sample.mem_available_kb = Some(120_000.0);
    let mut health = GatewayHealthMonitor::new(clocks(100.0));
    assert_eq!(
        health.snapshot(&sample, clocks(101.0)).protective_cause,
        "resource-cpu"
    );
}

#[test]
fn queue_age_escalates_from_congested_to_slow_at_twice_the_slo() {
    let mut health = GatewayHealthMonitor::new(clocks(100.0));
    health.observe_slo(true, 10.0, 10.0);
    assert_eq!(health.slo_pressure_state(), "congested");
    assert_eq!(
        health
            .snapshot(&resources(&[]), clocks(101.0))
            .backpressure_state,
        "congested"
    );

    health.observe_slo(true, 20.01, 10.0);
    assert_eq!(health.slo_pressure_state(), "slow");
    assert_eq!(
        health
            .snapshot(&resources(&[]), clocks(102.0))
            .backpressure_state,
        "slow"
    );
}
