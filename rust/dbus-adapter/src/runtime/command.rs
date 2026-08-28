// SPDX-License-Identifier: GPL-3.0-or-later
//! Runtime command classification and fail-closed deadline checks.

use serde_json::{Map, Value};

const MAX_FUTURE_DEADLINE_SKEW: f64 = 5.0;

pub(super) fn is_publication(kind: &str) -> bool {
    matches!(
        kind,
        "register_evcs" | "publish_evcs_fields" | "register_companion" | "publish_companion_fields"
    )
}

pub(super) fn command_kind(command: &Map<String, Value>) -> &str {
    command.get("kind").and_then(Value::as_str).unwrap_or("")
}

pub(super) fn command_deadline_expired(command: &Map<String, Value>, now: f64) -> bool {
    if !command.contains_key("deadline_s") {
        return false;
    }
    let Some(deadline) = command.get("deadline_s").and_then(Value::as_f64) else {
        return true;
    };
    if !deadline.is_finite() {
        return true;
    }
    if deadline <= 0.0 {
        return false;
    }
    let Some(created_at) = command.get("created_at").and_then(Value::as_f64) else {
        return true;
    };
    !created_at.is_finite()
        || created_at <= 0.0
        || created_at > now + MAX_FUTURE_DEADLINE_SKEW
        || now > created_at + deadline
}

#[cfg(test)]
mod tests {
    use serde_json::{Map, Value, json};

    use super::{command_deadline_expired, command_kind};

    #[test]
    fn command_deadlines_fail_closed_without_changing_non_transient_commands() {
        assert!(!command_deadline_expired(&Map::new(), 100.0));
        let valid: Map<String, Value> = Map::from_iter([
            ("deadline_s".to_owned(), json!(10.0)),
            ("created_at".to_owned(), json!(95.0)),
        ]);
        assert!(!command_deadline_expired(&valid, 100.0));
        let expired: Map<String, Value> = Map::from_iter([
            ("deadline_s".to_owned(), json!(2.0)),
            ("created_at".to_owned(), json!(95.0)),
        ]);
        assert!(command_deadline_expired(&expired, 100.0));
        let invalid: Map<String, Value> = Map::from_iter([("deadline_s".to_owned(), json!(2.0))]);
        assert!(command_deadline_expired(&invalid, 100.0));
    }

    #[test]
    fn command_kind_is_required_and_type_is_not_an_alias() {
        let type_only = Map::from_iter([("type".to_owned(), json!("refresh_energy_inputs"))]);
        let modern = Map::from_iter([
            ("kind".to_owned(), json!("refresh_energy_inputs")),
            ("type".to_owned(), json!("ignored")),
        ]);
        assert_eq!(command_kind(&type_only), "");
        assert_eq!(command_kind(&modern), "refresh_energy_inputs");
    }
}
