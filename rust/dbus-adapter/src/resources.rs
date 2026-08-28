// SPDX-License-Identifier: GPL-3.0-or-later
//! Low-rate procfs resource sampling with the established GX hysteresis.

use std::fs;
use std::time::{Duration, Instant};

use serde::Serialize;

const BUSY_LOAD_PER_CPU: f64 = 1.0;
const BUSY_CPU_PERCENT: f64 = 80.0;
const BUSY_MEM_AVAILABLE_KB: f64 = 65_536.0;
const CONSTRAINED_LOAD_PER_CPU: f64 = 1.5;
const CONSTRAINED_CPU_PERCENT: f64 = 90.0;
const CONSTRAINED_MEM_AVAILABLE_KB: f64 = 32_768.0;
const BUSY_EXIT_LOAD_PER_CPU: f64 = 0.85;
const BUSY_EXIT_CPU_PERCENT: f64 = 75.0;
const BUSY_EXIT_MEM_AVAILABLE_KB: f64 = 73_728.0;
const CONSTRAINED_EXIT_LOAD_PER_CPU: f64 = 1.25;
const CONSTRAINED_EXIT_CPU_PERCENT: f64 = 85.0;
const CONSTRAINED_EXIT_MEM_AVAILABLE_KB: f64 = 40_960.0;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ResourceState {
    Ok,
    Busy,
    Constrained,
}

impl ResourceState {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Busy => "busy",
            Self::Constrained => "constrained",
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct ResourceSnapshot {
    pub state: ResourceState,
    pub loadavg_1m: Option<f64>,
    pub loadavg_5m: Option<f64>,
    pub loadavg_15m: Option<f64>,
    pub load_per_cpu_1m: Option<f64>,
    pub system_cpu_pct: Option<f64>,
    pub mem_total_kb: Option<f64>,
    pub mem_available_kb: Option<f64>,
    pub process_rss_kb: Option<f64>,
    pub process_threads: Option<u64>,
    pub cpu_count: usize,
    pub pressure_evidence: Option<ResourcePressureEvidence>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ResourcePressureEvidence {
    pub active: bool,
    pub triggered_at: f64,
    pub causes: Vec<String>,
    pub load_per_cpu_1m: Option<f64>,
    pub system_cpu_pct: Option<f64>,
    pub mem_available_kb: Option<f64>,
}

impl ResourceSnapshot {
    const fn unknown(cpu_count: usize) -> Self {
        Self {
            state: ResourceState::Busy,
            loadavg_1m: None,
            loadavg_5m: None,
            loadavg_15m: None,
            load_per_cpu_1m: None,
            system_cpu_pct: None,
            mem_total_kb: None,
            mem_available_kb: None,
            process_rss_kb: None,
            process_threads: None,
            cpu_count,
            pressure_evidence: None,
        }
    }
}

pub struct ResourceMonitor {
    sample_interval: Duration,
    recovery_hold: Duration,
    next_sample: Instant,
    state: ResourceState,
    recovery: Option<(ResourceState, Instant)>,
    previous_cpu: Option<(u64, u64)>,
    snapshot: ResourceSnapshot,
    constrained_evidence: Option<ResourcePressureEvidence>,
}

impl ResourceMonitor {
    pub fn new(sample_interval: Duration, recovery_hold: Duration) -> Self {
        let cpu_count = std::thread::available_parallelism().map_or(1, usize::from);
        Self {
            sample_interval: sample_interval.max(Duration::from_millis(200)),
            recovery_hold,
            next_sample: Instant::now(),
            state: ResourceState::Ok,
            recovery: None,
            previous_cpu: None,
            snapshot: ResourceSnapshot::unknown(cpu_count),
            constrained_evidence: None,
        }
    }

    pub fn sample_if_due(&mut self) -> &ResourceSnapshot {
        let now = Instant::now();
        if now < self.next_sample {
            return &self.snapshot;
        }
        self.next_sample = now + self.sample_interval;
        let mut snapshot = sample(self.snapshot.cpu_count, &mut self.previous_cpu);
        let candidate = classify(
            snapshot.load_per_cpu_1m,
            snapshot.system_cpu_pct,
            snapshot.mem_available_kb,
        );
        let previous = self.state;
        self.update_state(candidate, &snapshot, now);
        self.capture_pressure_evidence(previous, &snapshot);
        snapshot.state = self.state;
        snapshot.pressure_evidence = self.constrained_evidence.clone().map(|mut evidence| {
            evidence.active = self.state == ResourceState::Constrained;
            evidence
        });
        self.snapshot = snapshot;
        &self.snapshot
    }

    pub const fn snapshot(&self) -> &ResourceSnapshot {
        &self.snapshot
    }

    fn update_state(
        &mut self,
        candidate: ResourceState,
        snapshot: &ResourceSnapshot,
        now: Instant,
    ) {
        let candidate = match self.state {
            ResourceState::Constrained if !constrained_exit_ready(snapshot) => {
                ResourceState::Constrained
            }
            ResourceState::Busy if candidate == ResourceState::Ok && !busy_exit_ready(snapshot) => {
                ResourceState::Busy
            }
            _ => candidate,
        };
        if candidate == self.state {
            self.recovery = None;
            return;
        }
        if severity(candidate) > severity(self.state) {
            self.state = candidate;
            self.recovery = None;
            return;
        }
        let started = match self.recovery {
            Some((target, started)) if target == candidate => started,
            _ => now,
        };
        self.recovery = Some((candidate, started));
        if now.duration_since(started) >= self.recovery_hold {
            self.state = candidate;
            self.recovery = None;
        }
    }

    fn capture_pressure_evidence(&mut self, previous: ResourceState, snapshot: &ResourceSnapshot) {
        if self.state != ResourceState::Constrained {
            return;
        }
        let causes = constrained_causes(snapshot);
        if causes.is_empty() {
            return;
        }
        let prior_is_critical = self
            .constrained_evidence
            .as_ref()
            .is_some_and(|evidence| has_critical_cause(&evidence.causes));
        if previous == ResourceState::Constrained && prior_is_critical {
            return;
        }
        if previous == ResourceState::Constrained && !has_critical_cause(&causes) {
            return;
        }
        self.constrained_evidence = Some(ResourcePressureEvidence {
            active: true,
            triggered_at: epoch_seconds(),
            causes,
            load_per_cpu_1m: snapshot.load_per_cpu_1m,
            system_cpu_pct: snapshot.system_cpu_pct,
            mem_available_kb: snapshot.mem_available_kb,
        });
    }
}

fn sample(cpu_count: usize, previous_cpu: &mut Option<(u64, u64)>) -> ResourceSnapshot {
    let load = load_average();
    let current_cpu = system_cpu();
    let system_cpu_pct = cpu_percentage(*previous_cpu, current_cpu);
    *previous_cpu = current_cpu;
    let (mem_total_kb, mem_available_kb) = memory_values();
    let (process_rss_kb, process_threads) = process_values();
    ResourceSnapshot {
        state: ResourceState::Busy,
        loadavg_1m: load.map(|value| value.0),
        loadavg_5m: load.map(|value| value.1),
        loadavg_15m: load.map(|value| value.2),
        load_per_cpu_1m: load.map(|value| {
            let count = u32::try_from(cpu_count.max(1)).unwrap_or(u32::MAX);
            value.0 / f64::from(count)
        }),
        system_cpu_pct,
        mem_total_kb,
        mem_available_kb,
        process_rss_kb,
        process_threads,
        cpu_count,
        pressure_evidence: None,
    }
}

fn constrained_causes(snapshot: &ResourceSnapshot) -> Vec<String> {
    let mut causes = Vec::new();
    if at_least(snapshot.load_per_cpu_1m, CONSTRAINED_LOAD_PER_CPU) {
        causes.push("load".to_owned());
    }
    if at_least(snapshot.system_cpu_pct, CONSTRAINED_CPU_PERCENT) {
        causes.push("cpu".to_owned());
    }
    if below(snapshot.mem_available_kb, CONSTRAINED_MEM_AVAILABLE_KB) {
        causes.push("memory".to_owned());
    }
    causes
}

fn has_critical_cause(causes: &[String]) -> bool {
    causes
        .iter()
        .any(|cause| cause == "cpu" || cause == "memory")
}

fn epoch_seconds() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |duration| duration.as_secs_f64())
}

fn load_average() -> Option<(f64, f64, f64)> {
    let raw = fs::read_to_string("/proc/loadavg").ok()?;
    let mut fields = raw.split_whitespace();
    Some((
        finite_nonnegative(fields.next()?.parse().ok()?)?,
        finite_nonnegative(fields.next()?.parse().ok()?)?,
        finite_nonnegative(fields.next()?.parse().ok()?)?,
    ))
}

fn system_cpu() -> Option<(u64, u64)> {
    let raw = fs::read_to_string("/proc/stat").ok()?;
    let values = raw
        .lines()
        .next()?
        .split_whitespace()
        .skip(1)
        .map(str::parse::<u64>)
        .collect::<Result<Vec<_>, _>>()
        .ok()?;
    let total = values.iter().copied().sum();
    let idle = values.get(3).copied().unwrap_or(0) + values.get(4).copied().unwrap_or(0);
    (total > 0 && idle <= total).then_some((total, idle))
}

fn cpu_percentage(previous: Option<(u64, u64)>, current: Option<(u64, u64)>) -> Option<f64> {
    let ((old_total, old_idle), (total, idle)) = (previous?, current?);
    let total_delta = total.checked_sub(old_total)?;
    let idle_delta = idle.checked_sub(old_idle)?;
    if total_delta == 0 {
        return Some(0.0);
    }
    let busy = integer_as_f64(total_delta.saturating_sub(idle_delta.min(total_delta)))?;
    let total = integer_as_f64(total_delta)?;
    Some(busy / total * 100.0)
}

fn memory_values() -> (Option<f64>, Option<f64>) {
    let Some(values) = numeric_mapping("/proc/meminfo") else {
        return (None, None);
    };
    (
        value_for(&values, "MemTotal").and_then(integer_as_f64),
        value_for(&values, "MemAvailable").and_then(integer_as_f64),
    )
}

fn process_values() -> (Option<f64>, Option<u64>) {
    let Some(values) = numeric_mapping("/proc/self/status") else {
        return (None, None);
    };
    (
        value_for(&values, "VmRSS").and_then(integer_as_f64),
        value_for(&values, "Threads"),
    )
}

fn numeric_mapping(path: &str) -> Option<Vec<(String, u64)>> {
    let raw = fs::read_to_string(path).ok()?;
    let mut result = Vec::new();
    for line in raw.lines() {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let Some(token) = value.split_whitespace().next() else {
            continue;
        };
        let Ok(number) = token.parse::<u64>() else {
            continue;
        };
        result.push((name.to_owned(), number));
    }
    (!result.is_empty()).then_some(result)
}

fn value_for(values: &[(String, u64)], name: &str) -> Option<u64> {
    values
        .iter()
        .find_map(|(key, value)| (key == name).then_some(*value))
}

fn integer_as_f64(value: u64) -> Option<f64> {
    value.to_string().parse().ok()
}

fn finite_nonnegative(value: f64) -> Option<f64> {
    (value.is_finite() && value >= 0.0).then_some(value)
}

fn classify(load: Option<f64>, cpu: Option<f64>, memory: Option<f64>) -> ResourceState {
    if [load, cpu, memory].iter().all(Option::is_none) {
        return ResourceState::Busy;
    }
    if at_least(load, CONSTRAINED_LOAD_PER_CPU)
        || at_least(cpu, CONSTRAINED_CPU_PERCENT)
        || below(memory, CONSTRAINED_MEM_AVAILABLE_KB)
    {
        ResourceState::Constrained
    } else if at_least(load, BUSY_LOAD_PER_CPU)
        || at_least(cpu, BUSY_CPU_PERCENT)
        || below(memory, BUSY_MEM_AVAILABLE_KB)
    {
        ResourceState::Busy
    } else {
        ResourceState::Ok
    }
}

fn constrained_exit_ready(snapshot: &ResourceSnapshot) -> bool {
    below(snapshot.load_per_cpu_1m, CONSTRAINED_EXIT_LOAD_PER_CPU)
        && below(snapshot.system_cpu_pct, CONSTRAINED_EXIT_CPU_PERCENT)
        && at_least(snapshot.mem_available_kb, CONSTRAINED_EXIT_MEM_AVAILABLE_KB)
}

fn busy_exit_ready(snapshot: &ResourceSnapshot) -> bool {
    below(snapshot.load_per_cpu_1m, BUSY_EXIT_LOAD_PER_CPU)
        && below(snapshot.system_cpu_pct, BUSY_EXIT_CPU_PERCENT)
        && at_least(snapshot.mem_available_kb, BUSY_EXIT_MEM_AVAILABLE_KB)
}

fn at_least(value: Option<f64>, threshold: f64) -> bool {
    value.is_some_and(|item| item >= threshold)
}

fn below(value: Option<f64>, threshold: f64) -> bool {
    value.is_some_and(|item| item < threshold)
}

const fn severity(state: ResourceState) -> u8 {
    match state {
        ResourceState::Ok => 0,
        ResourceState::Busy => 1,
        ResourceState::Constrained => 2,
    }
}

#[cfg(test)]
mod tests {
    use super::{ResourceState, classify};

    #[test]
    fn pressure_thresholds_preserve_the_python_contract() {
        assert_eq!(
            classify(Some(0.2), Some(10.0), Some(100_000.0)),
            ResourceState::Ok
        );
        assert_eq!(
            classify(Some(1.0), Some(10.0), Some(100_000.0)),
            ResourceState::Busy
        );
        assert_eq!(
            classify(Some(1.5), Some(10.0), Some(100_000.0)),
            ResourceState::Constrained
        );
        assert_eq!(classify(None, None, None), ResourceState::Busy);
    }
}
