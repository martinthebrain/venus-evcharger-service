// SPDX-License-Identifier: GPL-3.0-or-later
//! Read priority, completion cadence, and bounded retry scheduling.

use std::collections::BTreeSet;
use std::time::{Duration, Instant};

use super::{ActiveRead, EnergyReader, ReadKey, ReadMode, held_pv_aggregate, measurement_index};
use crate::energy::{Clocks, Measurement, MeasurementStatus};

impl ReadMode {
    pub(super) const fn allows_optional(self) -> bool {
        !matches!(self, Self::Protective)
    }

    pub(super) const fn allows_discovery(self) -> bool {
        matches!(self, Self::Normal)
    }

    pub(super) const fn interval_factor(self) -> f64 {
        match self {
            Self::Normal => 1.0,
            Self::Degraded => 3.0,
            Self::Protective => 5.0,
        }
    }
}

impl EnergyReader {
    pub fn tick_demand(&self, now: Instant, monotonic_at: f64) -> (usize, f64) {
        const CORE_KEYS: [ReadKey; 3] = [ReadKey::Grid, ReadKey::Pv, ReadKey::BatterySoc];
        let due = CORE_KEYS
            .into_iter()
            .filter(|key| {
                self.policy.intervals.contains_key(key)
                    && self
                        .next_due
                        .get(key)
                        .is_none_or(|deadline| *deadline <= now)
            })
            .count();
        let active = usize::from(
            self.active
                .as_ref()
                .is_some_and(|read| CORE_KEYS.contains(&read.key)),
        );
        let maximum_age = CORE_KEYS
            .into_iter()
            .filter_map(|key| {
                let observed = self.measurements[measurement_index(key)].observed_monotonic;
                (observed > 0.0 && monotonic_at >= observed).then_some(monotonic_at - observed)
            })
            .fold(0.0, f64::max);
        (due.saturating_add(active), maximum_age)
    }

    pub(super) fn next_due_key(&self, now: Instant, allow_optional: bool) -> Option<ReadKey> {
        const ORDER: [ReadKey; 7] = [
            ReadKey::Grid,
            ReadKey::Pv,
            ReadKey::BatterySoc,
            ReadKey::BatteryPower,
            ReadKey::BatteryCapacityWh,
            ReadKey::BatteryCapacityAh,
            ReadKey::BatteryVoltage,
        ];
        ORDER
            .into_iter()
            .enumerate()
            .filter(|(_, key)| {
                (allow_optional || *key != ReadKey::Pv)
                    && self.policy.intervals.contains_key(key)
                    && self.next_due.get(key).is_none_or(|due| *due <= now)
            })
            .min_by_key(|(priority, key)| (self.next_due.get(key).copied(), *priority))
            .map(|(_, key)| key)
    }

    pub(super) fn schedule_completed_cycle(&mut self, active: &ActiveRead) {
        if active.key == ReadKey::Pv || active.errors.is_empty() {
            self.failure_counts.insert(active.key, 0);
            self.next_due.insert(
                active.key,
                Instant::now()
                    + self.policy.success_delay(
                        active.key,
                        active.interval_factor,
                        active.operation_count,
                    ),
            );
            return;
        }
        let failures = self
            .failure_counts
            .get(&active.key)
            .copied()
            .unwrap_or(0)
            .saturating_add(1)
            .min(6);
        self.failure_counts.insert(active.key, failures);
        let interval = self.policy.intervals[&active.key].as_secs_f64();
        let multiplier = 2_u32.pow(u32::from(failures.saturating_sub(1)));
        let delay = (interval * 10.0).max(30.0) * f64::from(multiplier);
        self.next_due.insert(
            active.key,
            Instant::now() + Duration::from_secs_f64(delay.min(300.0)),
        );
    }

    pub(super) fn start_cycle(&mut self, key: ReadKey, now: Instant, interval_factor: f64) {
        if key == ReadKey::Pv {
            self.maintain_pv();
        }
        let members = self.members(key);
        self.next_due.insert(
            key,
            now + self
                .policy
                .success_delay(key, interval_factor, members.len()),
        );
        if members.is_empty() {
            let sources = self.source_ids(key);
            self.measurements[measurement_index(key)] = if key == ReadKey::Pv {
                let clocks = Clocks::now().ok();
                let candidates = self.pv_candidate_members();
                if let Some((value, confidence)) = held_pv_aggregate(&self.last_pv, &candidates) {
                    Measurement {
                        value: Some(value),
                        observed_at: clocks.map_or(0.0, |value| value.epoch),
                        observed_monotonic: clocks.map_or(0.0, |value| value.monotonic),
                        status: MeasurementStatus::Stale,
                        confidence,
                        source_ids: sources,
                        reason_code: "transient-hold".to_owned(),
                    }
                } else {
                    Measurement {
                        value: Some(0.0),
                        observed_at: clocks.map_or(0.0, |value| value.epoch),
                        observed_monotonic: clocks.map_or(0.0, |value| value.monotonic),
                        status: MeasurementStatus::Fresh,
                        confidence: 0.2,
                        source_ids: sources,
                        reason_code: "optional-source-unavailable".to_owned(),
                    }
                }
            } else {
                Measurement::unavailable(sources, "source-unavailable")
            };
            return;
        }
        self.active = Some(ActiveRead {
            key,
            members,
            index: 0,
            total: 0.0,
            successful_sources: BTreeSet::new(),
            confidence: 1.0,
            held_estimate: false,
            errors: Vec::new(),
            interval_factor,
            operation_count: 0,
        });
    }
}
