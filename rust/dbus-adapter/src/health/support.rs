// SPDX-License-Identifier: GPL-3.0-or-later
//! Small bounded statistics and error-classification helpers.

use std::collections::VecDeque;

use super::OperationEvent;

pub(super) fn count(
    events: &VecDeque<OperationEvent>,
    predicate: impl Fn(&OperationEvent) -> bool,
) -> u64 {
    u64::try_from(events.iter().filter(|event| predicate(event)).count()).unwrap_or(u64::MAX)
}

pub(super) fn percentile(values: &[f64], numerator: usize, denominator: usize) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let rank = values
        .len()
        .saturating_mul(numerator)
        .div_ceil(denominator.max(1));
    let index = rank.saturating_sub(1);
    values[index.min(values.len() - 1)]
}

pub(super) fn looks_like_timeout(error: &str) -> bool {
    let error = error.to_ascii_lowercase();
    error.contains("timeout") || error.contains("timed out") || error.contains("noreply")
}

pub(super) fn bounded_text(value: &str, maximum: usize) -> String {
    value.chars().take(maximum).collect()
}
