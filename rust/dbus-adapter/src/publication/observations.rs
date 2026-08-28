// SPDX-License-Identifier: GPL-3.0-or-later
//! Publication freshness and cache projections kept separate from D-Bus writes.

use super::{
    GUI_CONTROL_FIELDS, GUI_MEASUREMENT_FIELDS, GUI_SESSION_FIELDS, GuiFreshness,
    PublicationCacheValue, PublicationFieldObservation, PublicationRegistry,
    SESSION_ACTIVE_CURRENT_AMPS, SESSION_ACTIVE_POWER_WATTS, identity_path_count,
};

impl PublicationRegistry {
    pub fn registered_path_count(&self) -> usize {
        self.services
            .values()
            .map(|record| {
                self.specs(&record.kind).map_or(0, |specs| {
                    specs.len() + identity_path_count(&record.kind) + 1
                })
            })
            .sum()
    }

    pub fn evcs_registered(&self) -> bool {
        self.services.contains_key("evcs")
    }

    pub fn evcs_field_observation(&self, field: &str) -> Option<&PublicationFieldObservation> {
        self.evcs_observations.get(field)
    }

    pub const fn evcs_heartbeat(&self) -> (f64, f64) {
        (self.evcs_heartbeat_at, self.evcs_heartbeat_monotonic)
    }

    pub fn gui_freshness(&self, monotonic_at: f64, effective_max_age_seconds: f64) -> GuiFreshness {
        let measurement_max_age_seconds =
            self.maximum_field_age(&GUI_MEASUREMENT_FIELDS, monotonic_at, false);
        let control_max_age_seconds =
            self.maximum_field_age(&GUI_CONTROL_FIELDS, monotonic_at, true);
        let session_active =
            self.fresh_field_number("ac_power_w", monotonic_at, effective_max_age_seconds)
                >= SESSION_ACTIVE_POWER_WATTS
                || self.fresh_field_number("ac_current_a", monotonic_at, effective_max_age_seconds)
                    >= SESSION_ACTIVE_CURRENT_AMPS;
        let session_fields: &[&str] = if session_active {
            &GUI_SESSION_FIELDS
        } else {
            &[]
        };
        let session_max_age_seconds = self.maximum_field_age(session_fields, monotonic_at, false);
        let measurement_missing_count = self.missing_field_count(&GUI_MEASUREMENT_FIELDS);
        let control_missing_count = self.missing_field_count(&GUI_CONTROL_FIELDS);
        let session_missing_count = self.missing_field_count(session_fields);
        GuiFreshness {
            maximum_age_seconds: measurement_max_age_seconds
                .max(control_max_age_seconds)
                .max(session_max_age_seconds),
            measurement_max_age_seconds,
            control_max_age_seconds,
            session_max_age_seconds,
            missing_count: measurement_missing_count
                .saturating_add(control_missing_count)
                .saturating_add(session_missing_count),
            measurement_missing_count,
            control_missing_count,
            session_missing_count,
        }
    }

    pub(crate) fn cache_values(&self) -> Vec<PublicationCacheValue> {
        self.services
            .values()
            .flat_map(|record| {
                let service_name = record.service.service_name().to_owned();
                record
                    .observations
                    .iter()
                    .map(move |(path, observation)| PublicationCacheValue {
                        key: format!("path:{service_name}{path}"),
                        value: observation.value.to_json(),
                        source: format!("{service_name}{path}"),
                        changed_at: observation.changed_at,
                        confirmed_at: observation.confirmed_at,
                        confirmed_monotonic: observation.confirmed_monotonic,
                        freshness_kind: observation.freshness_kind.clone(),
                    })
            })
            .collect()
    }

    fn maximum_field_age(
        &self,
        fields: &[&str],
        monotonic_at: f64,
        use_service_heartbeat: bool,
    ) -> f64 {
        fields
            .iter()
            .map(|field| self.field_age(field, monotonic_at, use_service_heartbeat))
            .fold(0.0, f64::max)
    }

    fn field_age(&self, field: &str, monotonic_at: f64, use_service_heartbeat: bool) -> f64 {
        let Some(observation) = self.evcs_observations.get(field) else {
            return 0.0;
        };
        let observed = if use_service_heartbeat {
            observation
                .confirmed_monotonic
                .max(self.evcs_heartbeat_monotonic)
        } else {
            observation.confirmed_monotonic
        };
        if observed > 0.0 && monotonic_at >= observed {
            monotonic_at - observed
        } else {
            0.0
        }
    }

    fn missing_field_count(&self, fields: &[&str]) -> usize {
        fields
            .iter()
            .filter(|field| !self.evcs_observations.contains_key(**field))
            .count()
    }

    fn fresh_field_number(&self, field: &str, monotonic_at: f64, maximum_age: f64) -> f64 {
        let Some(observation) = self.evcs_observations.get(field) else {
            return 0.0;
        };
        if self.field_age(field, monotonic_at, false) > maximum_age {
            return 0.0;
        }
        observation
            .value
            .as_f64()
            .filter(|value| value.is_finite())
            .unwrap_or(0.0)
    }
}
