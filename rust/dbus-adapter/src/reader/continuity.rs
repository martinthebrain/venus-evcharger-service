// SPDX-License-Identifier: GPL-3.0-or-later
//! Short bounded PV continuity and semantic battery normalization.

use std::collections::HashMap;
use std::time::Instant;

use super::{ActiveRead, ReadMember};
use crate::energy::{Measurement, opaque_source_id};

const PV_HOLD_FACTOR: f64 = 0.8;
const PV_HOLD_SECONDS: f64 = 5.0;

#[derive(Clone, Debug)]
pub(super) struct LastGoodPv {
    pub(super) value: f64,
    pub(super) hold_started: Option<Instant>,
}

pub(super) fn apply_pv_hold(
    active: &mut ActiveRead,
    samples: &mut HashMap<(String, String), LastGoodPv>,
    service: &str,
    path: &str,
    error: &str,
) {
    let key = (service.to_owned(), path.to_owned());
    if !transient_error(error) {
        samples.remove(&key);
        active.confidence = active.confidence.min(0.2);
        active.errors.push(format!("{service}{path}: {error}"));
        return;
    }
    let Some(sample) = samples.get_mut(&key) else {
        active.confidence = active.confidence.min(0.2);
        active.errors.push(format!("{service}{path}: {error}"));
        return;
    };
    let started = *sample.hold_started.get_or_insert_with(Instant::now);
    let age = started.elapsed().as_secs_f64();
    let confidence = PV_HOLD_FACTOR * (1.0 - age / PV_HOLD_SECONDS).max(0.0);
    if confidence > 0.0 {
        active.total += sample.value * confidence;
        active.confidence = active.confidence.min(confidence);
        active.held_estimate = true;
        active
            .successful_sources
            .insert(opaque_source_id("pv-ac", service));
    } else {
        active.confidence = active.confidence.min(0.2);
        active.errors.push(format!("{service}{path}: {error}"));
    }
}

pub(super) fn held_pv_aggregate(
    samples: &HashMap<(String, String), LastGoodPv>,
    members: &[ReadMember],
) -> Option<(f64, f64)> {
    let mut total = 0.0;
    let mut confidence = 1.0_f64;
    let mut held = false;
    for member in members {
        let Some(sample) = samples.get(&(member.service.clone(), member.path.clone())) else {
            continue;
        };
        let Some(started) = sample.hold_started else {
            continue;
        };
        let factor =
            PV_HOLD_FACTOR * (1.0 - started.elapsed().as_secs_f64() / PV_HOLD_SECONDS).max(0.0);
        if factor > 0.0 {
            total += sample.value * factor;
            confidence = confidence.min(factor);
            held = true;
        }
    }
    held.then_some((total, confidence))
}

fn transient_error(error: &str) -> bool {
    let normalized = error.to_ascii_lowercase();
    normalized.contains("timeout")
        || normalized.contains("timed out")
        || normalized.contains("noreply")
}

pub(super) fn semantic_battery_power(measurement: &Measurement) -> Measurement {
    let mut result = measurement.clone();
    result.value = result.value.map(|value| -value);
    result
}

pub(super) fn positive_metadata(measurement: &Measurement) -> Measurement {
    if measurement.value.is_none_or(|value| value > 0.0) {
        return measurement.clone();
    }
    Measurement::unavailable(measurement.source_ids.clone(), "invalid-non-positive")
}

#[cfg(test)]
mod tests {
    use super::transient_error;

    #[test]
    fn only_timeout_class_errors_keep_last_good_pv() {
        assert!(transient_error("org.freedesktop.DBus.Error.NoReply"));
        assert!(transient_error("operation timed out"));
        assert!(!transient_error("UnknownObject"));
    }
}
