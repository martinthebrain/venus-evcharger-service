// SPDX-License-Identifier: GPL-3.0-or-later
//! Canonical cache observations for local publications and external reads.

use std::collections::{BTreeMap, HashMap};

use serde_json::{Value, json};

use crate::energy::{Clocks, Measurement, MeasurementStatus};
use crate::publication::PublicationCacheValue;

#[derive(Clone, Debug)]
struct CacheObservation {
    value: Value,
    source: String,
    changed_at: f64,
    confirmed_at: f64,
    confirmed_monotonic: f64,
    updated_at: f64,
    updated_monotonic: f64,
    status: String,
    last_error: String,
    confidence: f64,
    freshness_kind: String,
    source_state: String,
    reason_code: String,
    stale_after_seconds: Option<f64>,
}

#[derive(Default)]
pub struct RuntimeCache {
    observations: HashMap<String, CacheObservation>,
}

impl RuntimeCache {
    pub fn remember_publication(&mut self, publication: PublicationCacheValue) {
        self.observations.insert(
            publication.key,
            CacheObservation {
                value: publication.value,
                source: publication.source,
                changed_at: publication.changed_at,
                confirmed_at: publication.confirmed_at,
                confirmed_monotonic: publication.confirmed_monotonic,
                updated_at: publication.confirmed_at,
                updated_monotonic: publication.confirmed_monotonic,
                status: "fresh".to_owned(),
                last_error: String::new(),
                confidence: 1.0,
                freshness_kind: publication.freshness_kind,
                source_state: "active".to_owned(),
                reason_code: String::new(),
                stale_after_seconds: None,
            },
        );
    }

    pub fn remember_external_value(
        &mut self,
        key: &str,
        value: Value,
        source: &str,
        observed: Clocks,
    ) {
        let changed_at = self.changed_at(key, &value, observed.epoch);
        self.observations.insert(
            key.to_owned(),
            CacheObservation {
                value,
                source: source.to_owned(),
                changed_at,
                confirmed_at: observed.epoch,
                confirmed_monotonic: observed.monotonic,
                updated_at: observed.epoch,
                updated_monotonic: observed.monotonic,
                status: "fresh".to_owned(),
                last_error: String::new(),
                confidence: 1.0,
                freshness_kind: "external_read".to_owned(),
                source_state: "active".to_owned(),
                reason_code: String::new(),
                stale_after_seconds: None,
            },
        );
    }

    pub fn remember_measurement(
        &mut self,
        key: &str,
        measurement: &Measurement,
        captured: Clocks,
        stale_after_seconds: f64,
    ) {
        let value = measurement.value.map_or(Value::Null, Value::from);
        let changed_at = self.changed_at(key, &value, captured.epoch);
        let previous = self.observations.get(key);
        let confirmed_at = positive_or_previous(
            measurement.observed_at,
            previous.map_or(0.0, |item| item.confirmed_at),
        );
        let confirmed_monotonic = positive_or_previous(
            measurement.observed_monotonic,
            previous.map_or(0.0, |item| item.confirmed_monotonic),
        );
        let source_state = match measurement.status {
            MeasurementStatus::Fresh | MeasurementStatus::Stale => "active",
            MeasurementStatus::Error => "error",
            MeasurementStatus::Unavailable | MeasurementStatus::Unknown => "unavailable",
        };
        let last_error = matches!(
            measurement.status,
            MeasurementStatus::Error | MeasurementStatus::Unavailable
        )
        .then(|| measurement.reason_code.clone())
        .unwrap_or_default();
        let source = if measurement.source_ids.is_empty() {
            "gateway".to_owned()
        } else {
            measurement.source_ids.join(",")
        };
        self.observations.insert(
            key.to_owned(),
            CacheObservation {
                value,
                source,
                changed_at,
                confirmed_at,
                confirmed_monotonic,
                updated_at: captured.epoch,
                updated_monotonic: captured.monotonic,
                status: measurement.status.as_str().to_owned(),
                last_error,
                confidence: measurement.confidence,
                freshness_kind: "external_read".to_owned(),
                source_state: source_state.to_owned(),
                reason_code: measurement.reason_code.clone(),
                stale_after_seconds: Some(stale_after_seconds.max(0.0)),
            },
        );
    }

    pub fn payload(
        &self,
        clocks: Clocks,
        default_stale_after_seconds: f64,
    ) -> BTreeMap<String, Value> {
        self.observations
            .iter()
            .map(|(key, observation)| {
                let stale_after = observation
                    .stale_after_seconds
                    .unwrap_or(default_stale_after_seconds);
                let age = observation_age(observation.confirmed_monotonic, clocks.monotonic);
                let status = projected_status(
                    &observation.status,
                    &observation.freshness_kind,
                    age,
                    stale_after,
                );
                let mut payload = json!({
                    "value": observation.value,
                    "source": observation.source,
                    "changed_at": observation.changed_at,
                    "confirmed_at": observation.confirmed_at,
                    "confirmed_monotonic": observation.confirmed_monotonic,
                    "updated_at": observation.updated_at,
                    "updated_monotonic": observation.updated_monotonic,
                    "age_s": age,
                    "change_age_s": epoch_age(observation.changed_at, clocks.epoch),
                    "status": status,
                    "last_error": observation.last_error,
                    "confidence": observation.confidence,
                    "freshness_kind": observation.freshness_kind,
                    "source_state": observation.source_state,
                    "stale_after_s": stale_after,
                });
                if !observation.reason_code.is_empty() {
                    payload["reason_code"] = Value::String(observation.reason_code.clone());
                }
                (key.clone(), payload)
            })
            .collect()
    }

    pub fn freshness_payload(&self, clocks: Clocks, default_stale_after_seconds: f64) -> Value {
        const CORE_KEYS: [&str; 3] = ["grid_power_w", "pv_power_w", "battery_soc"];
        let values = self.payload(clocks, default_stale_after_seconds);
        let mut all_status_counts = BTreeMap::<String, usize>::new();
        let mut external_status_counts = BTreeMap::<String, usize>::new();
        let mut local_status_counts = BTreeMap::<String, usize>::new();
        for value in values.values() {
            let status = value["status"].as_str().unwrap_or("unknown").to_owned();
            *all_status_counts.entry(status.clone()).or_default() += 1;
            match value["freshness_kind"].as_str().unwrap_or("external_read") {
                "external_read" => *external_status_counts.entry(status).or_default() += 1,
                "local_owned" | "static" => {
                    *local_status_counts.entry(status).or_default() += 1;
                }
                _ => {}
            }
        }
        let mut core_status_counts = BTreeMap::<String, usize>::new();
        let mut critical_stale_count = 0_usize;
        let mut critical_missing_count = 0_usize;
        let mut critical_nonfresh_count = 0_usize;
        let mut important = serde_json::Map::new();
        for key in CORE_KEYS {
            let status = values
                .get(key)
                .and_then(|value| value["status"].as_str())
                .unwrap_or("missing");
            let age = values
                .get(key)
                .and_then(|value| value["age_s"].as_f64())
                .unwrap_or(0.0);
            *core_status_counts.entry(status.to_owned()).or_default() += 1;
            critical_stale_count += usize::from(status == "stale");
            critical_missing_count += usize::from(status == "missing");
            critical_nonfresh_count += usize::from(!matches!(status, "fresh" | "missing"));
            important.insert(format!("{key}_age_s"), Value::from(age));
            important.insert(format!("{key}_status"), Value::from(status));
        }
        let optional = values.iter().filter(|(key, value)| {
            value["freshness_kind"] == "external_read" && !CORE_KEYS.contains(&key.as_str())
        });
        let optional_source_error_count = optional
            .clone()
            .filter(|(_key, value)| value["status"] == "error")
            .count();
        let optional_source_unavailable_count = optional
            .filter(|(_key, value)| value["status"] == "unavailable")
            .count();
        important.extend(serde_json::Map::from_iter([
            ("value_count".to_owned(), Value::from(values.len())),
            (
                "status_counts".to_owned(),
                serde_json::to_value(core_status_counts).unwrap_or(Value::Null),
            ),
            (
                "all_status_counts".to_owned(),
                serde_json::to_value(all_status_counts).unwrap_or(Value::Null),
            ),
            (
                "external_read_status_counts".to_owned(),
                serde_json::to_value(external_status_counts).unwrap_or(Value::Null),
            ),
            (
                "local_publish_status_counts".to_owned(),
                serde_json::to_value(local_status_counts).unwrap_or(Value::Null),
            ),
            (
                "critical_stale_count".to_owned(),
                Value::from(critical_stale_count),
            ),
            (
                "critical_missing_count".to_owned(),
                Value::from(critical_missing_count),
            ),
            (
                "critical_nonfresh_count".to_owned(),
                Value::from(critical_nonfresh_count),
            ),
            (
                "optional_source_error_count".to_owned(),
                Value::from(optional_source_error_count),
            ),
            (
                "optional_source_unavailable_count".to_owned(),
                Value::from(optional_source_unavailable_count),
            ),
        ]));
        Value::Object(important)
    }

    fn changed_at(&self, key: &str, value: &Value, captured_at: f64) -> f64 {
        self.observations.get(key).map_or(captured_at, |previous| {
            if previous.value == *value {
                previous.changed_at
            } else {
                captured_at
            }
        })
    }
}

fn positive_or_previous(value: f64, previous: f64) -> f64 {
    if value.is_finite() && value > 0.0 {
        value
    } else {
        previous
    }
}

fn observation_age(observed_monotonic: f64, now_monotonic: f64) -> f64 {
    if observed_monotonic > 0.0 && now_monotonic >= observed_monotonic {
        now_monotonic - observed_monotonic
    } else {
        0.0
    }
}

fn epoch_age(changed_at: f64, now: f64) -> f64 {
    if changed_at > 0.0 && now >= changed_at {
        now - changed_at
    } else {
        0.0
    }
}

fn projected_status<'a>(
    status: &'a str,
    freshness_kind: &str,
    age: f64,
    stale_after_seconds: f64,
) -> &'a str {
    if status == "fresh"
        && freshness_kind == "external_read"
        && stale_after_seconds > 0.0
        && age > stale_after_seconds
    {
        "stale"
    } else {
        status
    }
}

#[cfg(test)]
mod tests {
    use super::{RuntimeCache, projected_status};
    use crate::energy::{Clocks, Measurement, MeasurementStatus};

    #[test]
    fn stale_measurement_keeps_observation_age_and_external_freshness() {
        let mut cache = RuntimeCache::default();
        cache.remember_measurement(
            "pv_power_w",
            &Measurement {
                value: Some(800.0),
                observed_at: 90.0,
                observed_monotonic: 40.0,
                status: MeasurementStatus::Stale,
                confidence: 0.8,
                source_ids: vec!["pv-a".to_owned()],
                reason_code: "pv-transient-hold".to_owned(),
            },
            Clocks {
                epoch: 100.0,
                monotonic: 45.0,
            },
            10.0,
        );

        let values = cache.payload(
            Clocks {
                epoch: 102.0,
                monotonic: 47.0,
            },
            10.0,
        );
        let value = &values["pv_power_w"];
        assert_eq!(value["status"], "stale");
        assert_eq!(value["freshness_kind"], "external_read");
        assert_eq!(value["confirmed_at"], 90.0);
        assert_eq!(value["age_s"], 7.0);
        assert_eq!(value["confidence"], 0.8);
    }

    #[test]
    fn only_external_fresh_values_age_into_stale() {
        assert_eq!(
            projected_status("fresh", "external_read", 10.1, 10.0),
            "stale"
        );
        assert_eq!(
            projected_status("fresh", "external_read", 10.0, 10.0),
            "fresh"
        );
        assert_eq!(
            projected_status("fresh", "local_owned", 20.0, 10.0),
            "fresh"
        );
        assert_eq!(
            projected_status("error", "external_read", 20.0, 10.0),
            "error"
        );
    }
}
