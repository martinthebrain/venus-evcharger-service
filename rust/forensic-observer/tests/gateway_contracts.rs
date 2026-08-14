use std::fs;

use proptest::prelude::*;
use serde_json::{Value, json};
use tempfile::tempdir;
use venus_evcharger_forensic_observer::gateway::GatewayDiagnostics;

fn sample(name: &str, value: &Value) -> Value {
    json!({
        "name": name,
        "value": value,
        "status": "fresh",
        "changed_at": 98.0,
        "confirmed_at": 99.0,
        "confidence": 1.0,
        "applicability": "applicable",
        "reason_code": ""
    })
}

fn document() -> Value {
    json!({
        "schema_version": 3,
        "sequence": 7,
        "captured_at": 100.0,
        "captured_monotonic": 50.0,
        "health": {
            "state": "ok", "stale": false, "timeouts_60s": 0,
            "average_latency_ms": 4.0, "maximum_latency_ms": 8.0,
            "pending_gateway_commands": 1, "pending_core_commands": 2,
            "maximum_event_loop_gap_ms_60s": 20.0, "last_success_at": 99.5,
            "last_error_code": ""
        },
        "discovery": {
            "enabled": true, "state": "idle", "pending_work": 0,
            "discovered_source_count": 0, "unusable_source_count": 0,
            "dormant_source_count": 0, "sources": []
        },
        "publication": {"registered": true, "heartbeat_at": 99.5, "stale": false},
        "ev_charger": [
            sample("operating_mode", &json!(2)),
            sample("charging_enabled", &json!(true)),
            sample("auto_start_enabled", &json!(true)),
            sample("ac_power_w", &json!(1234.5)),
            sample("charger_state_code", &json!(2)),
            sample("decision_reason", &json!("scheduled-window")),
            sample("decision_state", &json!("charging")),
            sample("last_health_reason", &json!("healthy")),
            sample("runtime_overrides_active", &json!(false)),
            sample("runtime_overrides_source", &json!("none"))
        ]
    })
}

#[test]
fn valid_schema_is_accepted_and_roundtrips() {
    let input = document();
    let parsed = GatewayDiagnostics::from_value(&input);
    assert!(parsed.is_ok());
    let serialized = serde_json::to_value(parsed.ok()).unwrap_or(Value::Null);
    assert_eq!(serialized["schema_version"], json!(3));
}

#[test]
fn missing_and_wrong_semantic_fields_are_rejected() {
    let mut missing = document();
    if let Some(samples) = missing["ev_charger"].as_array_mut() {
        let _removed = samples.pop();
    }
    assert!(GatewayDiagnostics::from_value(&missing).is_err());

    let mut wrong = document();
    wrong["ev_charger"][0]["value"] = json!(true);
    assert!(GatewayDiagnostics::from_value(&wrong).is_err());

    let mut unavailable = document();
    unavailable["ev_charger"][1]["value"] = Value::Null;
    unavailable["ev_charger"][1]["status"] = json!("unavailable");
    unavailable["ev_charger"][1]["changed_at"] = json!(0.0);
    unavailable["ev_charger"][1]["confirmed_at"] = json!(0.0);
    unavailable["ev_charger"][1]["reason_code"] = json!("missing");
    let parsed = GatewayDiagnostics::from_value(&unavailable).ok();
    assert_eq!(
        parsed.map(|value| value.critical_unavailable_fields()),
        Some(vec!["charging_enabled"])
    );
}

#[test]
fn protective_health_is_preserved_semantically() {
    let mut protective = document();
    protective["health"]["state"] = json!("protective");
    let parsed = GatewayDiagnostics::from_value(&protective).ok();
    assert_eq!(
        parsed.map(|value| value.health.state),
        Some("protective".to_owned())
    );
}

#[test]
fn diagnostics_file_size_is_bounded_before_json_decoding() {
    let directory = tempdir();
    assert!(directory.is_ok());
    let Some(directory) = directory.ok() else {
        return;
    };
    let path = directory.path().join("oversized.json");
    assert!(fs::write(&path, vec![b' '; 1_048_577]).is_ok());
    assert!(GatewayDiagnostics::read(&path).is_err());
}

proptest! {
    #[test]
    fn operating_mode_accepts_exactly_the_public_domain(mode in 0_u64..10) {
        let mut input = document();
        input["ev_charger"][0]["value"] = json!(mode);
        prop_assert_eq!(GatewayDiagnostics::from_value(&input).is_ok(), mode <= 2);
    }

    #[test]
    fn fresh_sample_timestamps_preserve_causal_order(changed in 0_u32..120, confirmed in 0_u32..120) {
        let mut input = document();
        input["ev_charger"][3]["changed_at"] = json!(f64::from(changed));
        input["ev_charger"][3]["confirmed_at"] = json!(f64::from(confirmed));
        let expected = changed > 0 && confirmed > 0 && changed <= confirmed;
        prop_assert_eq!(GatewayDiagnostics::from_value(&input).is_ok(), expected);
    }
}
