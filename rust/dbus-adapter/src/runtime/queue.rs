// SPDX-License-Identifier: GPL-3.0-or-later
//! Semantic queue ordering, pressure policy, and bounded scheduler accounting.

use std::collections::{BTreeMap, VecDeque};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde_json::{Map, Value, json};

use super::command::{command_kind, is_publication};
use crate::config::IniConfig;

const AGED_REFRESH_SECONDS: f64 = 15.0;
const WINDOW: Duration = Duration::from_secs(1);
const PROCESSED_WINDOW: Duration = Duration::from_secs(60);

const QUEUE_CLASSES: [(&str, &str, usize); 9] = [
    (
        "startup/register",
        "DbusGatewayQueueBudgetStartupRegister",
        100,
    ),
    (
        "gui-critical-publish",
        "DbusGatewayQueueBudgetGuiCriticalPublish",
        50,
    ),
    ("local-publish", "DbusGatewayQueueBudgetLocalPublish", 30),
    ("remote-write", "DbusGatewayQueueBudgetRemoteWrite", 2),
    ("read-fast", "DbusGatewayQueueBudgetReadFast", 4),
    ("read-slow", "DbusGatewayQueueBudgetReadSlow", 2),
    ("discovery", "DbusGatewayQueueBudgetDiscovery", 1),
    ("introspection", "DbusGatewayQueueBudgetIntrospection", 1),
    ("diagnostic", "DbusGatewayQueueBudgetDiagnostic", 1),
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum PressureState {
    Ok,
    Congested,
    Slow,
    Protective,
}

#[derive(Clone, Copy, Debug, Default)]
pub(super) struct QueueDemand {
    pub(super) critical_operations: usize,
    pub(super) oldest_slo_age_seconds: f64,
}

#[derive(Clone, Debug, Default)]
struct QueueClassObservation {
    pending: usize,
    oldest_age_seconds: f64,
}

pub(super) struct QueueScheduler {
    local_publish_burst_limit: usize,
    local_publish_tick_budget: Duration,
    base_budgets: BTreeMap<String, usize>,
    effective_budgets: BTreeMap<String, usize>,
    budget_events: VecDeque<(Instant, String)>,
    processed_events: VecDeque<Instant>,
    lifecycle_events: VecDeque<(Instant, String, String)>,
    lifecycle_counts: BTreeMap<String, usize>,
    class_observations: BTreeMap<String, QueueClassObservation>,
    demand: QueueDemand,
    oldest_command_age_seconds: f64,
    last_processed_at: f64,
    dynamic_local_publish_burst_limit: usize,
}

impl QueueScheduler {
    pub(super) fn from_config(config: &IniConfig) -> Self {
        let local_publish_burst_limit =
            positive_size(config, "DbusGatewayLocalPublishBurstLimit", 20);
        let local_publish_tick_budget = Duration::from_secs_f64(
            (config.f64("DbusGatewayLocalPublishTickBudgetMs", 75.0) / 1_000.0).max(0.001),
        );
        let base_budgets = QUEUE_CLASSES
            .into_iter()
            .map(|(class, key, fallback)| {
                let configured = config.i64(key, i64::try_from(fallback).unwrap_or(i64::MAX));
                let minimum = usize::from(matches!(
                    class,
                    "startup/register"
                        | "gui-critical-publish"
                        | "local-publish"
                        | "remote-write"
                        | "read-fast"
                ));
                (
                    class.to_owned(),
                    usize::try_from(configured.max(i64::try_from(minimum).unwrap_or(0)))
                        .unwrap_or(fallback),
                )
            })
            .collect::<BTreeMap<_, _>>();
        Self {
            local_publish_burst_limit,
            local_publish_tick_budget,
            effective_budgets: base_budgets.clone(),
            base_budgets,
            budget_events: VecDeque::new(),
            processed_events: VecDeque::new(),
            lifecycle_events: VecDeque::new(),
            lifecycle_counts: BTreeMap::new(),
            class_observations: BTreeMap::new(),
            demand: QueueDemand::default(),
            oldest_command_age_seconds: 0.0,
            last_processed_at: 0.0,
            dynamic_local_publish_burst_limit: local_publish_burst_limit,
        }
    }

    pub(super) fn prioritize(
        mut commands: Vec<(PathBuf, Map<String, Value>)>,
        captured_at: f64,
    ) -> Vec<(PathBuf, Map<String, Value>)> {
        commands.sort_by(|left, right| {
            command_order(&left.1, captured_at).cmp(&command_order(&right.1, captured_at))
        });
        commands
    }

    pub(super) fn observe_pending(
        &mut self,
        commands: &[(PathBuf, Map<String, Value>)],
        captured_at: f64,
    ) {
        self.class_observations.clear();
        self.demand = QueueDemand::default();
        self.oldest_command_age_seconds = 0.0;
        for (_path, command) in commands {
            let class = command_queue_class(command);
            let age = command_age(command, captured_at);
            let observation = self.class_observations.entry(class.clone()).or_default();
            observation.pending = observation.pending.saturating_add(1);
            observation.oldest_age_seconds = observation.oldest_age_seconds.max(age);
            self.oldest_command_age_seconds = self.oldest_command_age_seconds.max(age);
            if is_tick_critical(&class) {
                self.demand.critical_operations = self.demand.critical_operations.saturating_add(1);
                self.demand.oldest_slo_age_seconds = self.demand.oldest_slo_age_seconds.max(age);
            }
        }
    }

    pub(super) const fn demand(&self) -> QueueDemand {
        self.demand
    }

    pub(super) fn update_pressure(
        &mut self,
        pressure: PressureState,
        queue_slo_seconds: f64,
        mainloop_gap_ms: f64,
        mainloop_slo_ms: f64,
    ) {
        self.prune(Instant::now());
        self.effective_budgets.clone_from(&self.base_budgets);
        let mut burst = self.local_publish_burst_limit;
        if self.demand.oldest_slo_age_seconds > queue_slo_seconds {
            burst = burst.saturating_mul(3).max(burst.saturating_add(4)).min(50);
        }
        if mainloop_gap_ms > mainloop_slo_ms {
            burst = burst.min((self.local_publish_burst_limit / 2).max(1));
        }
        match pressure {
            PressureState::Ok => {}
            PressureState::Congested => {
                self.cap(
                    "gui-critical-publish",
                    (self.local_publish_burst_limit / 2).max(1),
                );
                self.cap("local-publish", (self.local_publish_burst_limit / 4).max(1));
                self.suspend_advisory();
                burst = burst.min((self.local_publish_burst_limit / 2).max(1));
            }
            PressureState::Slow => {
                self.cap(
                    "gui-critical-publish",
                    (self.local_publish_burst_limit / 4).max(1),
                );
                self.cap("local-publish", 1);
                self.suspend_advisory();
                burst = burst.min((self.local_publish_burst_limit / 4).max(1));
            }
            PressureState::Protective => {
                self.cap("gui-critical-publish", 1);
                self.cap("local-publish", 1);
                self.suspend_advisory();
                burst = 1;
            }
        }
        self.dynamic_local_publish_burst_limit = burst.max(1);
    }

    pub(super) fn command_allowed(command: &Map<String, Value>, pressure: PressureState) -> bool {
        let class = command_queue_class(command);
        if class == "startup/register" {
            return true;
        }
        let priority = command_priority(command);
        match pressure {
            PressureState::Ok => true,
            PressureState::Congested => {
                !matches!(priority, "optional" | "diagnostic") && class != "diagnostic"
            }
            PressureState::Slow => {
                class == "gui-critical-publish" || matches!(priority, "safety" | "user")
            }
            PressureState::Protective => {
                matches!(class.as_str(), "gui-critical-publish" | "remote-write")
                    && matches!(priority, "safety" | "user")
            }
        }
    }

    pub(super) fn budget_available(&mut self, command: &Map<String, Value>) -> bool {
        let now = Instant::now();
        self.prune(now);
        let class = command_queue_class(command);
        let limit = self.effective_budgets.get(&class).copied().unwrap_or(1);
        self.budget_events
            .iter()
            .filter(|(_at, event_class)| event_class == &class)
            .count()
            < limit
    }

    pub(super) fn record_attempt(&mut self, command: &Map<String, Value>) {
        let now = Instant::now();
        self.prune(now);
        self.budget_events
            .push_back((now, command_queue_class(command)));
    }

    pub(super) fn record_processed(&mut self, captured_at: f64) {
        let now = Instant::now();
        self.prune(now);
        self.processed_events.push_back(now);
        self.last_processed_at = captured_at.max(0.0);
    }

    pub(super) fn record_lifecycle(&mut self, command: &Map<String, Value>, state: &str) {
        let now = Instant::now();
        self.prune(now);
        let normalized = if state.is_empty() { "unknown" } else { state };
        *self
            .lifecycle_counts
            .entry(normalized.to_owned())
            .or_default() += 1;
        self.lifecycle_events
            .push_back((now, normalized.to_owned(), command_queue_class(command)));
    }

    pub(super) const fn dynamic_local_publish_burst_limit(&self) -> usize {
        self.dynamic_local_publish_burst_limit
    }

    pub(super) const fn local_publish_tick_budget(&self) -> Duration {
        self.local_publish_tick_budget
    }

    pub(super) fn payload(&mut self) -> Value {
        self.prune(Instant::now());
        let classes = self
            .class_observations
            .iter()
            .map(|(class, observation)| {
                (
                    class.clone(),
                    json!({
                        "pending": observation.pending,
                        "oldest_age_s": observation.oldest_age_seconds,
                    }),
                )
            })
            .collect::<BTreeMap<_, _>>();
        let usage = self.budget_events.iter().fold(
            BTreeMap::<String, usize>::new(),
            |mut counts, (_at, class)| {
                *counts.entry(class.clone()).or_default() += 1;
                counts
            },
        );
        let lifecycle_60s = self.lifecycle_events.iter().fold(
            BTreeMap::<String, usize>::new(),
            |mut counts, (_at, state, _class)| {
                *counts.entry(state.clone()).or_default() += 1;
                counts
            },
        );
        json!({
            "processed_commands_60s": self.processed_events.len(),
            "last_processed_at": self.last_processed_at,
            "local_publish_burst_limit": self.local_publish_burst_limit,
            "dynamic_local_publish_burst_limit": self.dynamic_local_publish_burst_limit,
            "local_publish_tick_budget_ms": self.local_publish_tick_budget.as_secs_f64() * 1_000.0,
            "queue_class_budgets": self.effective_budgets,
            "queue_class_usage_1s": usage,
            "lifecycle_counts": self.lifecycle_counts,
            "lifecycle_counts_60s": lifecycle_60s,
            "queue_classes": classes,
            "oldest_command_age_s": self.oldest_command_age_seconds,
            "oldest_slo_command_age_s": self.demand.oldest_slo_age_seconds,
        })
    }

    fn cap(&mut self, class: &str, maximum: usize) {
        if let Some(limit) = self.effective_budgets.get_mut(class) {
            *limit = (*limit).min(maximum);
        }
    }

    fn suspend_advisory(&mut self) {
        for class in ["diagnostic", "discovery", "introspection"] {
            self.effective_budgets.insert(class.to_owned(), 0);
        }
    }

    fn prune(&mut self, now: Instant) {
        while self
            .budget_events
            .front()
            .is_some_and(|(at, _class)| now.saturating_duration_since(*at) > WINDOW)
        {
            self.budget_events.pop_front();
        }
        while self
            .processed_events
            .front()
            .is_some_and(|at| now.saturating_duration_since(*at) > PROCESSED_WINDOW)
        {
            self.processed_events.pop_front();
        }
        while self
            .lifecycle_events
            .front()
            .is_some_and(|(at, _state, _class)| {
                now.saturating_duration_since(*at) > PROCESSED_WINDOW
            })
        {
            self.lifecycle_events.pop_front();
        }
    }
}

pub(super) fn command_queue_class(command: &Map<String, Value>) -> String {
    if let Some(class) = command.get("queue_class").and_then(Value::as_str)
        && !class.is_empty()
    {
        return class.to_owned();
    }
    let kind = command_kind(command);
    if matches!(kind, "register_evcs" | "register_companion") {
        return "startup/register".to_owned();
    }
    if is_publication(kind) {
        return if command.get("publication_priority").and_then(Value::as_str) == Some("critical") {
            "gui-critical-publish"
        } else {
            "local-publish"
        }
        .to_owned();
    }
    match kind {
        "refresh_energy_inputs" => {
            if command.get("scope").and_then(Value::as_str) == Some("topology") {
                "discovery"
            } else {
                "read-fast"
            }
        }
        "introspect" => "introspection",
        "gx_relay_refresh" => "read-fast",
        "gx_relay_set_enabled" | "ess_grid_setpoint" => "remote-write",
        "disable_matching_generic_shelly_once" => "configuration",
        _ => "diagnostic",
    }
    .to_owned()
}

fn command_order(command: &Map<String, Value>, captured_at: f64) -> (u8, u8, u64) {
    let priority = if aged_refresh(command, captured_at) {
        3
    } else {
        priority_rank(command_priority(command)).saturating_mul(2)
    };
    let created = command
        .get("created_at")
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && *value > 0.0)
        .unwrap_or(0.0);
    (
        priority,
        queue_rank(&command_queue_class(command)),
        created.to_bits(),
    )
}

fn aged_refresh(command: &Map<String, Value>, captured_at: f64) -> bool {
    let class = command_queue_class(command);
    matches!(
        class.as_str(),
        "read-fast" | "read-slow" | "discovery" | "introspection"
    ) && command_age(command, captured_at) >= AGED_REFRESH_SECONDS
}

fn command_age(command: &Map<String, Value>, captured_at: f64) -> f64 {
    let activity = command
        .get("updated_at")
        .or_else(|| command.get("created_at"))
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && *value > 0.0)
        .unwrap_or(captured_at);
    (captured_at - activity).max(0.0)
}

fn command_priority(command: &Map<String, Value>) -> &str {
    command
        .get("priority")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("diagnostic")
}

const fn priority_rank(priority: &str) -> u8 {
    match priority.as_bytes() {
        b"safety" => 0,
        b"user" => 1,
        b"publish" => 2,
        b"read" => 3,
        b"normal" => 4,
        b"optional" | b"discovery" => 5,
        _ => 6,
    }
}

fn queue_rank(class: &str) -> u8 {
    match class {
        "startup/register" => 0,
        "gui-critical-publish" => 1,
        "remote-write" => 2,
        "local-publish" => 3,
        "read-fast" => 4,
        "read-slow" => 5,
        "discovery" => 6,
        "introspection" => 7,
        "diagnostic" => 8,
        _ => 9,
    }
}

fn is_tick_critical(class: &str) -> bool {
    matches!(
        class,
        "startup/register"
            | "gui-critical-publish"
            | "local-publish"
            | "remote-write"
            | "read-fast"
    )
}

pub(super) fn is_advisory(command: &Map<String, Value>) -> bool {
    matches!(
        command_queue_class(command).as_str(),
        "diagnostic" | "discovery" | "introspection"
    )
}

fn positive_size(config: &IniConfig, key: &str, fallback: usize) -> usize {
    usize::try_from(
        config
            .i64(key, i64::try_from(fallback).unwrap_or(i64::MAX))
            .max(1),
    )
    .unwrap_or(fallback)
}

#[cfg(test)]
mod tests;
