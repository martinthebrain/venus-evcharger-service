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
        system_cpu_pct: Some(92.0),
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
fn load_pressure_degrades_but_cpu_pressure_is_protective() {
    let mut load_health = GatewayHealthMonitor::new(clocks(100.0));
    let load = load_health.snapshot(&resources(&["load"]), clocks(101.0));
    assert_eq!(load.performance_state, "degraded");
    assert_eq!(load.state, "degraded");
    assert_eq!(load.protective_cause, "");

    let mut cpu_health = GatewayHealthMonitor::new(clocks(100.0));
    let cpu = cpu_health.snapshot(&resources(&["load", "cpu"]), clocks(101.0));
    assert_eq!(cpu.performance_state, "protective");
    assert_eq!(cpu.state, "protective");
    assert_eq!(cpu.protective_cause, "resource-cpu");
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
