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
    let canonical = command.get("deadline_s");
    let legacy = command.get("max_age_seconds");
    if canonical.is_none() && legacy.is_none() {
        return false;
    }
    let canonical_deadline = canonical.and_then(Value::as_f64);
    let legacy_deadline = legacy.and_then(Value::as_f64);
    if canonical.is_some() && canonical_deadline.is_none()
        || legacy.is_some() && legacy_deadline.is_none()
        || canonical_deadline
            .zip(legacy_deadline)
            .is_some_and(|(left, right)| {
                !matches!(left.total_cmp(&right), std::cmp::Ordering::Equal)
            })
    {
        return true;
    }
    let Some(deadline) = canonical_deadline.or(legacy_deadline) else {
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
    fn command_deadlines_accept_the_legacy_alias_only_for_rolling_upgrades() {
        let valid_legacy: Map<String, Value> = Map::from_iter([
            ("max_age_seconds".to_owned(), json!(10.0)),
            ("created_at".to_owned(), json!(95.0)),
        ]);
        assert!(!command_deadline_expired(&valid_legacy, 100.0));

        let expired_legacy: Map<String, Value> = Map::from_iter([
            ("max_age_seconds".to_owned(), json!(2.0)),
            ("created_at".to_owned(), json!(95.0)),
        ]);
        assert!(command_deadline_expired(&expired_legacy, 100.0));

        let matching_aliases: Map<String, Value> = Map::from_iter([
            ("deadline_s".to_owned(), json!(10.0)),
            ("max_age_seconds".to_owned(), json!(10.0)),
            ("created_at".to_owned(), json!(95.0)),
        ]);
        assert!(!command_deadline_expired(&matching_aliases, 100.0));

        for malformed in [
            Map::from_iter([
                ("deadline_s".to_owned(), json!(10.0)),
                ("max_age_seconds".to_owned(), json!(9.0)),
                ("created_at".to_owned(), json!(95.0)),
            ]),
            Map::from_iter([
                ("max_age_seconds".to_owned(), json!("ten")),
                ("created_at".to_owned(), json!(95.0)),
            ]),
        ] {
            assert!(command_deadline_expired(&malformed, 100.0));
        }
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
