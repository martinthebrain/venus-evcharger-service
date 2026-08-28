// SPDX-License-Identifier: GPL-3.0-or-later

use std::path::PathBuf;

use serde_json::{Map, Value, json};

use super::{PressureState, QueueScheduler, command_queue_class};
use crate::config::IniConfig;
use crate::runtime_policy_contract;

fn object(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

#[test]
fn queue_classes_match_the_python_gateway_policy() {
    assert_eq!(
        command_queue_class(&object(&json!({"kind": "register_evcs"}))),
        "startup/register"
    );
    assert_eq!(
        command_queue_class(&object(&json!({
            "kind": "publish_evcs_fields",
            "publication_priority": "critical"
        }))),
        "gui-critical-publish"
    );
    assert_eq!(
        command_queue_class(&object(&json!({
            "kind": "refresh_energy_inputs",
            "scope": "topology"
        }))),
        "discovery"
    );
}

#[test]
fn generated_python_queue_and_backpressure_cases_match() -> Result<(), String> {
    let contract = runtime_policy_contract::load()?;
    for case in contract.commands {
        assert_eq!(command_queue_class(&case.command), case.queue_class);
        for (state, expected) in case.allowed {
            let pressure = match state.as_str() {
                "ok" => PressureState::Ok,
                "congested" => PressureState::Congested,
                "slow" => PressureState::Slow,
                "protective" => PressureState::Protective,
                _ => return Err(format!("unknown contract pressure state: {state}")),
            };
            assert_eq!(
                QueueScheduler::command_allowed(&case.command, pressure),
                expected
            );
        }
    }
    Ok(())
}

#[test]
fn configured_budgets_and_pressure_caps_are_enforced() -> Result<(), String> {
    let config = IniConfig::parse(
        "[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=20\nDbusGatewayQueueBudgetRemoteWrite=1\n",
    )?;
    let mut scheduler = QueueScheduler::from_config(&config);
    let remote = object(&json!({"kind": "gx_relay_set_enabled", "priority": "user"}));
    assert!(scheduler.budget_available(&remote));
    scheduler.record_attempt(&remote);
    assert!(!scheduler.budget_available(&remote));
    scheduler.update_pressure(PressureState::Protective, 10.0, 0.0, 500.0);
    assert!(QueueScheduler::command_allowed(
        &remote,
        PressureState::Protective
    ));
    let diagnostic = object(&json!({"kind": "unknown"}));
    assert!(!QueueScheduler::command_allowed(
        &diagnostic,
        PressureState::Protective
    ));
    Ok(())
}

#[test]
fn every_python_queue_budget_key_is_configurable() -> Result<(), String> {
    let config = IniConfig::parse(
        "[DEFAULT]\n\
DbusGatewayLocalPublishBurstLimit=17\n\
DbusGatewayLocalPublishTickBudgetMs=12.5\n\
DbusGatewayQueueBudgetStartupRegister=11\n\
DbusGatewayQueueBudgetGuiCriticalPublish=12\n\
DbusGatewayQueueBudgetLocalPublish=13\n\
DbusGatewayQueueBudgetRemoteWrite=14\n\
DbusGatewayQueueBudgetReadFast=15\n\
DbusGatewayQueueBudgetReadSlow=16\n\
DbusGatewayQueueBudgetDiscovery=17\n\
DbusGatewayQueueBudgetIntrospection=18\n\
DbusGatewayQueueBudgetDiagnostic=19\n",
    )?;
    let mut scheduler = QueueScheduler::from_config(&config);
    let payload = scheduler.payload();
    assert_eq!(payload["local_publish_burst_limit"], 17);
    let tick_budget = payload["local_publish_tick_budget_ms"]
        .as_f64()
        .ok_or_else(|| "missing local publish tick budget".to_owned())?;
    assert!((tick_budget - 12.5).abs() < f64::EPSILON);
    let budgets = &payload["queue_class_budgets"];
    for (class, expected) in [
        ("startup/register", 11),
        ("gui-critical-publish", 12),
        ("local-publish", 13),
        ("remote-write", 14),
        ("read-fast", 15),
        ("read-slow", 16),
        ("discovery", 17),
        ("introspection", 18),
        ("diagnostic", 19),
    ] {
        assert_eq!(budgets[class], expected);
    }
    Ok(())
}

#[test]
fn aged_refresh_overtakes_normal_work_without_overtaking_user_work() {
    let commands = vec![
        (
            PathBuf::from("normal"),
            object(&json!({"kind": "unknown", "priority": "normal", "created_at": 99.0})),
        ),
        (
            PathBuf::from("aged"),
            object(
                &json!({"kind": "refresh_energy_inputs", "priority": "read", "created_at": 80.0}),
            ),
        ),
        (
            PathBuf::from("user"),
            object(
                &json!({"kind": "gx_relay_set_enabled", "priority": "user", "created_at": 99.0}),
            ),
        ),
    ];
    let ordered = QueueScheduler::prioritize(commands, 100.0);
    assert_eq!(ordered[0].0, PathBuf::from("user"));
    assert_eq!(ordered[1].0, PathBuf::from("aged"));
}
