// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded latency classification for optional D-Bus sources.

use std::collections::{HashMap, VecDeque};
use std::time::{Duration, Instant};

const WINDOW: Duration = Duration::from_secs(60);
const MAX_SOURCES: usize = 16;
const MAX_SAMPLES_PER_SOURCE: usize = 64;
const MIN_SAMPLES: usize = 3;
const SLOW_P95_MS: f64 = 250.0;
const VERY_SLOW_P99_MS: f64 = 750.0;

#[derive(Default)]
pub(super) struct OptionalSourceLatencies {
    samples: HashMap<String, VecDeque<(Instant, f64)>>,
}

impl OptionalSourceLatencies {
    pub(super) fn record(&mut self, source: &str, duration: Duration) -> f64 {
        let now = Instant::now();
        if !self.samples.contains_key(source) && self.samples.len() >= MAX_SOURCES {
            return 1.0;
        }
        let samples = self.samples.entry(source.to_owned()).or_default();
        samples.push_back((now, duration.as_secs_f64() * 1_000.0));
        while samples
            .front()
            .is_some_and(|(at, _)| now.saturating_duration_since(*at) > WINDOW)
        {
            samples.pop_front();
        }
        while samples.len() > MAX_SAMPLES_PER_SOURCE {
            samples.pop_front();
        }
        interval_factor(samples)
    }
}

fn interval_factor(samples: &VecDeque<(Instant, f64)>) -> f64 {
    if samples.len() < MIN_SAMPLES {
        return 1.0;
    }
    let mut values = samples.iter().map(|(_, value)| *value).collect::<Vec<_>>();
    values.sort_by(f64::total_cmp);
    if percentile(&values, 99) >= VERY_SLOW_P99_MS {
        5.0
    } else if percentile(&values, 95) >= SLOW_P95_MS {
        3.0
    } else {
        1.0
    }
}

fn percentile(values: &[f64], percent: usize) -> f64 {
    let rank = values.len().saturating_mul(percent).div_ceil(100);
    values[rank.saturating_sub(1).min(values.len() - 1)]
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::OptionalSourceLatencies;

    fn assert_factor(actual: f64, expected: f64) {
        assert!((actual - expected).abs() < f64::EPSILON);
    }

    #[test]
    fn optional_source_requires_three_samples_before_backing_off() {
        let mut latencies = OptionalSourceLatencies::default();
        assert_factor(latencies.record("source", Duration::from_millis(800)), 1.0);
        assert_factor(latencies.record("source", Duration::from_millis(800)), 1.0);
        assert_factor(latencies.record("source", Duration::from_millis(800)), 5.0);
    }

    #[test]
    fn p95_threshold_uses_the_python_interval_factor() {
        let mut latencies = OptionalSourceLatencies::default();
        assert_factor(latencies.record("source", Duration::from_millis(300)), 1.0);
        assert_factor(latencies.record("source", Duration::from_millis(300)), 1.0);
        assert_factor(latencies.record("source", Duration::from_millis(300)), 3.0);
    }
}
