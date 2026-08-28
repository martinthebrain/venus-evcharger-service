// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded monotonic availability evidence for AC and DC PV sources.

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use serde::Serialize;

use crate::energy::Clocks;

const EVIDENCE_TTL: Duration = Duration::from_secs(18 * 60 * 60);
const OBSERVATION_RETENTION: Duration = Duration::from_secs(24 * 60 * 60);
const ERROR_BACKOFF: Duration = Duration::from_secs(300);
const MAX_OBSERVATIONS: usize = 64;

#[derive(Clone, Debug, Serialize)]
pub(super) struct PvDormancyEvidence {
    pub(super) source_id: String,
    pub(super) reason: &'static str,
    pub(super) observed_at: f64,
}

#[derive(Clone, Debug, Default)]
struct PvObservation {
    validated: bool,
    available: Option<bool>,
    failure_reason: Option<&'static str>,
    dormant: bool,
    dormant_observed_at: f64,
    dormant_observed_monotonic: f64,
    last_observed_monotonic: f64,
    next_probe_monotonic: f64,
}

#[derive(Default)]
pub(super) struct PvDormancyTracker {
    observations: HashMap<String, PvObservation>,
}

impl PvDormancyTracker {
    pub(super) fn record_value(
        &mut self,
        source_id: &str,
        clocks: Clocks,
        active_source_ids: &HashSet<String>,
    ) {
        let observation = self.observation(source_id, active_source_ids);
        observation.validated = true;
        observation.available = Some(true);
        observation.failure_reason = None;
        observation.dormant = false;
        observation.last_observed_monotonic = clocks.monotonic;
        observation.next_probe_monotonic = 0.0;
    }

    pub(super) fn record_error(
        &mut self,
        source_id: &str,
        error: &str,
        clocks: Clocks,
        active_source_ids: &HashSet<String>,
    ) {
        let observation = self.observation(source_id, active_source_ids);
        observation.available = Some(false);
        observation.last_observed_monotonic = clocks.monotonic;
        observation.next_probe_monotonic = clocks.monotonic + ERROR_BACKOFF.as_secs_f64();
        if explicit_dormancy_error(error) {
            observation.validated = true;
            observation.failure_reason = None;
            observation.dormant = true;
            observation.dormant_observed_at = clocks.epoch.max(0.0);
            observation.dormant_observed_monotonic = clocks.monotonic;
        } else if !observation.dormant {
            observation.failure_reason = Some("source-path-unreadable");
        }
    }

    pub(super) fn maintain(&mut self, active_source_ids: &HashSet<String>, monotonic_at: f64) {
        for observation in self.observations.values_mut() {
            if observation.dormant
                && monotonic_at - observation.dormant_observed_monotonic
                    >= EVIDENCE_TTL.as_secs_f64()
            {
                observation.dormant = false;
                observation.failure_reason = Some("source-path-unreadable");
            }
        }
        self.observations.retain(|source_id, observation| {
            active_source_ids.contains(source_id)
                || monotonic_at - observation.last_observed_monotonic
                    < OBSERVATION_RETENTION.as_secs_f64()
        });
    }

    pub(super) fn probe_allowed(&self, source_id: &str, monotonic_at: f64) -> bool {
        self.observations
            .get(source_id)
            .is_none_or(|observation| monotonic_at >= observation.next_probe_monotonic)
    }

    pub(super) fn validated(&self, source_id: &str) -> bool {
        self.observations
            .get(source_id)
            .is_some_and(|observation| observation.validated)
    }

    pub(super) fn validated_source_ids(&self) -> HashSet<String> {
        self.observations
            .iter()
            .filter(|(_source_id, observation)| observation.validated)
            .map(|(source_id, _observation)| source_id.clone())
            .collect()
    }

    pub(super) fn evidence(&self, source_ids: &HashSet<String>) -> Vec<PvDormancyEvidence> {
        let mut result = self
            .observations
            .iter()
            .filter(|(source_id, observation)| {
                source_ids.contains(*source_id)
                    && observation.validated
                    && observation.available == Some(false)
                    && observation.dormant
            })
            .map(|(source_id, observation)| PvDormancyEvidence {
                source_id: source_id.clone(),
                reason: "explicit-dormant-state",
                observed_at: observation.dormant_observed_at,
            })
            .collect::<Vec<_>>();
        result.sort_by(|left, right| left.source_id.cmp(&right.source_id));
        result
    }

    pub(super) fn failure_reason(&self, source_id: &str) -> Option<&'static str> {
        self.observations
            .get(source_id)
            .and_then(|observation| observation.failure_reason)
    }

    fn observation<'a>(
        &'a mut self,
        source_id: &str,
        active_source_ids: &HashSet<String>,
    ) -> &'a mut PvObservation {
        if !self.observations.contains_key(source_id) {
            self.trim_to_capacity(active_source_ids, 1);
        }
        self.observations.entry(source_id.to_owned()).or_default()
    }

    fn trim_to_capacity(&mut self, active_source_ids: &HashSet<String>, reserve: usize) {
        let target = MAX_OBSERVATIONS.saturating_sub(reserve);
        while self.observations.len() > target {
            let candidate = self
                .observations
                .iter()
                .min_by(|left, right| {
                    (
                        active_source_ids.contains(left.0),
                        ordered_time(left.1.last_observed_monotonic),
                        left.0,
                    )
                        .cmp(&(
                            active_source_ids.contains(right.0),
                            ordered_time(right.1.last_observed_monotonic),
                            right.0,
                        ))
                })
                .map(|(source_id, _observation)| source_id.clone());
            let Some(source_id) = candidate else {
                break;
            };
            self.observations.remove(&source_id);
        }
    }
}

fn explicit_dormancy_error(error: &str) -> bool {
    let message = error.trim().to_ascii_lowercase();
    if message.is_empty()
        || [
            "not asleep",
            "not dormant",
            "not sleeping",
            "not in standby",
        ]
        .iter()
        .any(|phrase| message.contains(phrase))
    {
        return false;
    }
    message
        .split(|character: char| !character.is_ascii_alphabetic())
        .any(|word| matches!(word, "asleep" | "dormant" | "sleeping" | "standby"))
}

const fn ordered_time(value: f64) -> u64 {
    value.max(0.0).to_bits()
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::{PvDormancyTracker, explicit_dormancy_error};
    use crate::energy::Clocks;
    use crate::runtime_policy_contract;

    #[test]
    fn only_explicit_non_negated_sleep_messages_are_dormant() {
        assert!(explicit_dormancy_error("inverter sleeping"));
        assert!(explicit_dormancy_error("DC inverter standby"));
        assert!(!explicit_dormancy_error("not sleeping"));
        assert!(!explicit_dormancy_error(
            "org.freedesktop.DBus.Error.NoReply"
        ));
    }

    #[test]
    fn generated_python_dormancy_messages_match() -> Result<(), String> {
        for case in runtime_policy_contract::load()?.dormancy_messages {
            assert_eq!(explicit_dormancy_error(&case.message), case.explicit);
        }
        Ok(())
    }

    #[test]
    fn generic_failure_backs_off_without_creating_dormancy() {
        let active = HashSet::from(["pv-a".to_owned()]);
        let mut tracker = PvDormancyTracker::default();
        tracker.record_error(
            "pv-a",
            "NoReply",
            Clocks {
                epoch: 10.0,
                monotonic: 20.0,
            },
            &active,
        );
        assert!(!tracker.probe_allowed("pv-a", 319.9));
        assert!(tracker.probe_allowed("pv-a", 320.0));
        assert!(tracker.evidence(&active).is_empty());
    }

    #[test]
    fn explicit_sleep_is_validated_evidence_and_success_clears_it() {
        let active = HashSet::from(["pv-a".to_owned()]);
        let mut tracker = PvDormancyTracker::default();
        tracker.record_error(
            "pv-a",
            "inverter asleep",
            Clocks {
                epoch: 10.0,
                monotonic: 20.0,
            },
            &active,
        );
        assert!(tracker.validated("pv-a"));
        assert_eq!(tracker.evidence(&active).len(), 1);
        tracker.record_value(
            "pv-a",
            Clocks {
                epoch: 30.0,
                monotonic: 320.0,
            },
            &active,
        );
        assert!(tracker.evidence(&active).is_empty());
    }
}
