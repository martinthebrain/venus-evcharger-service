//! Bounded, in-memory Linux process and CPU activity sampling.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::Serialize;

const PROC_ROOT: &str = "/proc";
const TOP_PROCESS_LIMIT: usize = 12;
const PROCESS_NAME_LIMIT: usize = 64;
const PERCENT_DECIMAL_SCALE: u128 = 1_000;

/// CPU activity attributed to one process over the preceding sample window.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ProcessActivity {
    /// Process identifier at the end of the window.
    pub pid: u32,
    /// Kernel process name, deliberately excluding command-line arguments.
    pub name: String,
    /// Percentage of one CPU core consumed during the window.
    pub cpu_percent: f64,
    /// Process state observed at the end of the window.
    pub state: String,
}

/// Bounded resource evidence covering the interval before an observation.
#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "status", rename_all = "kebab-case")]
pub enum SystemActivity {
    /// Both interval endpoints were available and internally consistent.
    Available {
        /// Monotonic duration represented by the counters.
        window_seconds: f64,
        /// One-minute Linux load average at the end of the window.
        load_1m: f64,
        /// Five-minute Linux load average at the end of the window.
        load_5m: f64,
        /// Fifteen-minute Linux load average at the end of the window.
        load_15m: f64,
        /// Aggregate non-idle CPU percentage across all cores.
        system_cpu_percent: f64,
        /// Aggregate CPU time reported as I/O wait.
        system_iowait_percent: f64,
        /// Runnable tasks reported by `/proc/loadavg`.
        runnable_processes: u32,
        /// Total tasks reported by `/proc/loadavg`.
        total_processes: u32,
        /// Processes in uninterruptible sleep at the final endpoint.
        blocked_processes: u32,
        /// Highest process CPU consumers, sorted descending and bounded.
        top_processes: Vec<ProcessActivity>,
    },
    /// A bounded reason explains why interval evidence is unavailable.
    Unavailable {
        /// Stable machine-readable reason.
        reason_code: String,
        /// Bounded human-readable detail.
        error: String,
    },
}

impl SystemActivity {
    fn unavailable(reason_code: &str, error: impl Into<String>) -> Self {
        Self::Unavailable {
            reason_code: reason_code.to_owned(),
            error: bound_text(&error.into(), 240),
        }
    }
}

/// Stateful sampler retaining only one previous set of cumulative counters.
pub struct SystemActivitySampler {
    proc_root: PathBuf,
    previous: Result<ProcSample, String>,
}

impl SystemActivitySampler {
    /// Capture the first endpoint without producing persistent output.
    #[must_use]
    pub fn start() -> Self {
        Self::from_root(PathBuf::from(PROC_ROOT))
    }

    fn from_root(proc_root: PathBuf) -> Self {
        let previous = read_sample(&proc_root);
        Self {
            proc_root,
            previous,
        }
    }

    /// Capture a new endpoint and derive bounded interval evidence.
    #[must_use]
    pub fn observe(&mut self) -> SystemActivity {
        let current = read_sample(&self.proc_root);
        let activity = match (&self.previous, &current) {
            (Ok(previous), Ok(current)) => activity_between(previous, current),
            (Err(error), _) => {
                SystemActivity::unavailable("previous-proc-sample-unavailable", error)
            }
            (_, Err(error)) => SystemActivity::unavailable("proc-sample-unavailable", error),
        };
        self.previous = current;
        activity
    }
}

#[derive(Clone, Debug)]
struct ProcSample {
    captured_at: Instant,
    cpu: CpuCounters,
    load: LoadCounters,
    processes: HashMap<u32, ProcessCounters>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct CpuCounters {
    total: u64,
    idle: u64,
    iowait: u64,
    cpu_count: u32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct LoadCounters {
    one: f64,
    five: f64,
    fifteen: f64,
    runnable: u32,
    total: u32,
}

#[derive(Clone, Debug, PartialEq)]
struct ProcessCounters {
    name: String,
    state: char,
    cpu_ticks: u64,
    started_at_ticks: u64,
}

fn read_sample(proc_root: &Path) -> Result<ProcSample, String> {
    let stat = fs::read_to_string(proc_root.join("stat"))
        .map_err(|error| format!("read proc stat: {error}"))?;
    let loadavg = fs::read_to_string(proc_root.join("loadavg"))
        .map_err(|error| format!("read proc loadavg: {error}"))?;
    Ok(ProcSample {
        captured_at: Instant::now(),
        cpu: parse_cpu_counters(&stat)?,
        load: parse_load_counters(&loadavg)?,
        processes: read_processes(proc_root),
    })
}

fn parse_cpu_counters(text: &str) -> Result<CpuCounters, String> {
    let mut lines = text.lines();
    let aggregate = lines
        .next()
        .ok_or_else(|| "proc stat is empty".to_owned())?;
    let mut fields = aggregate.split_whitespace();
    if fields.next() != Some("cpu") {
        return Err("proc stat lacks aggregate CPU counters".to_owned());
    }
    let values = fields
        .map(str::parse::<u64>)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("invalid aggregate CPU counter: {error}"))?;
    if values.len() < 5 {
        return Err("proc stat has too few aggregate CPU counters".to_owned());
    }
    let total = values.iter().try_fold(0_u64, |sum, value| {
        sum.checked_add(*value)
            .ok_or_else(|| "aggregate CPU counters overflow".to_owned())
    })?;
    let cpu_count = lines
        .filter_map(|line| line.split_whitespace().next())
        .filter(|name| name.strip_prefix("cpu").is_some_and(is_ascii_digits))
        .count();
    let cpu_count = u32::try_from(cpu_count)
        .map_err(|error| format!("CPU count is not representable: {error}"))?;
    if cpu_count == 0 {
        return Err("proc stat has no per-CPU counters".to_owned());
    }
    Ok(CpuCounters {
        total,
        idle: values[3],
        iowait: values[4],
        cpu_count,
    })
}

fn parse_load_counters(text: &str) -> Result<LoadCounters, String> {
    let mut fields = text.split_whitespace();
    let one = parse_finite(fields.next(), "one-minute load")?;
    let five = parse_finite(fields.next(), "five-minute load")?;
    let fifteen = parse_finite(fields.next(), "fifteen-minute load")?;
    let tasks = fields
        .next()
        .ok_or_else(|| "proc loadavg lacks task counts".to_owned())?;
    let (runnable, total) = tasks
        .split_once('/')
        .ok_or_else(|| "proc loadavg task counts are invalid".to_owned())?;
    Ok(LoadCounters {
        one,
        five,
        fifteen,
        runnable: parse_u32(runnable, "runnable task count")?,
        total: parse_u32(total, "total task count")?,
    })
}

fn read_processes(proc_root: &Path) -> HashMap<u32, ProcessCounters> {
    let Ok(entries) = fs::read_dir(proc_root) else {
        return HashMap::new();
    };
    entries
        .flatten()
        .filter_map(|entry| {
            let pid = entry.file_name().to_string_lossy().parse::<u32>().ok()?;
            let stat = fs::read_to_string(entry.path().join("stat")).ok()?;
            parse_process_counters(&stat)
                .ok()
                .map(|process| (pid, process))
        })
        .collect()
}

fn parse_process_counters(text: &str) -> Result<ProcessCounters, String> {
    let name_start = text
        .find('(')
        .ok_or_else(|| "process stat lacks name start".to_owned())?;
    let name_end = text
        .rfind(')')
        .filter(|end| *end > name_start)
        .ok_or_else(|| "process stat lacks name end".to_owned())?;
    let name = bound_text(&text[name_start + 1..name_end], PROCESS_NAME_LIMIT);
    let fields = text[name_end + 1..].split_whitespace().collect::<Vec<_>>();
    if fields.len() <= 19 {
        return Err("process stat has too few fields".to_owned());
    }
    let state = fields[0]
        .chars()
        .next()
        .ok_or_else(|| "process stat has no state".to_owned())?;
    let user_ticks = parse_u64(fields[11], "process user ticks")?;
    let system_ticks = parse_u64(fields[12], "process system ticks")?;
    Ok(ProcessCounters {
        name,
        state,
        cpu_ticks: user_ticks
            .checked_add(system_ticks)
            .ok_or_else(|| "process CPU ticks overflow".to_owned())?,
        started_at_ticks: parse_u64(fields[19], "process start ticks")?,
    })
}

fn activity_between(previous: &ProcSample, current: &ProcSample) -> SystemActivity {
    let Some(total_delta) = current.cpu.total.checked_sub(previous.cpu.total) else {
        return SystemActivity::unavailable(
            "cpu-counters-regressed",
            "total CPU counter regressed",
        );
    };
    if total_delta == 0 || current.cpu.cpu_count != previous.cpu.cpu_count {
        return SystemActivity::unavailable(
            "cpu-counters-invalid",
            "CPU interval is empty or CPU count changed",
        );
    }
    let idle_delta = current.cpu.idle.saturating_sub(previous.cpu.idle);
    let iowait_delta = current.cpu.iowait.saturating_sub(previous.cpu.iowait);
    let window_seconds = current
        .captured_at
        .checked_duration_since(previous.captured_at)
        .map_or(0.0, |duration| duration.as_secs_f64());
    if window_seconds <= 0.0 {
        return SystemActivity::unavailable("sample-window-invalid", "sample window is empty");
    }
    let mut top_processes = process_activity(previous, current, total_delta);
    top_processes.truncate(TOP_PROCESS_LIMIT);
    SystemActivity::Available {
        window_seconds,
        load_1m: current.load.one,
        load_5m: current.load.five,
        load_15m: current.load.fifteen,
        system_cpu_percent: percentage(total_delta.saturating_sub(idle_delta), total_delta, 100),
        system_iowait_percent: percentage(iowait_delta, total_delta, 100),
        runnable_processes: current.load.runnable,
        total_processes: current.load.total,
        blocked_processes: u32::try_from(
            current
                .processes
                .values()
                .filter(|process| process.state == 'D')
                .count(),
        )
        .unwrap_or(u32::MAX),
        top_processes,
    }
}

fn process_activity(
    previous: &ProcSample,
    current: &ProcSample,
    total_delta: u64,
) -> Vec<ProcessActivity> {
    let scale = current.cpu.cpu_count.saturating_mul(100);
    let mut values = current
        .processes
        .iter()
        .filter_map(|(pid, process)| {
            let old = previous.processes.get(pid)?;
            if process.started_at_ticks != old.started_at_ticks {
                return None;
            }
            let delta = process.cpu_ticks.checked_sub(old.cpu_ticks)?;
            Some(ProcessActivity {
                pid: *pid,
                name: process.name.clone(),
                cpu_percent: percentage(delta, total_delta, scale),
                state: process.state.to_string(),
            })
        })
        .collect::<Vec<_>>();
    values.sort_by(|left, right| {
        right
            .cpu_percent
            .total_cmp(&left.cpu_percent)
            .then_with(|| left.pid.cmp(&right.pid))
    });
    values
}

fn percentage(part: u64, total: u64, scale: u32) -> f64 {
    if total == 0 {
        return 0.0;
    }
    let maximum = u128::from(scale) * PERCENT_DECIMAL_SCALE;
    let scaled = (u128::from(part) * maximum / u128::from(total)).min(maximum);
    let bounded = u32::try_from(scaled).unwrap_or(u32::MAX);
    f64::from(bounded) / 1_000.0
}

fn parse_finite(value: Option<&str>, label: &str) -> Result<f64, String> {
    let parsed = value
        .ok_or_else(|| format!("proc data lacks {label}"))?
        .parse::<f64>()
        .map_err(|error| format!("invalid {label}: {error}"))?;
    if parsed.is_finite() && parsed >= 0.0 {
        Ok(parsed)
    } else {
        Err(format!("{label} must be finite and nonnegative"))
    }
}

fn parse_u32(value: &str, label: &str) -> Result<u32, String> {
    value
        .parse::<u32>()
        .map_err(|error| format!("invalid {label}: {error}"))
}

fn parse_u64(value: &str, label: &str) -> Result<u64, String> {
    value
        .parse::<u64>()
        .map_err(|error| format!("invalid {label}: {error}"))
}

fn is_ascii_digits(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit())
}

fn bound_text(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

#[cfg(test)]
mod tests {
    use super::{
        CpuCounters, LoadCounters, ProcSample, ProcessCounters, SystemActivity, activity_between,
        parse_cpu_counters, parse_load_counters, parse_process_counters,
    };
    use std::collections::HashMap;
    use std::time::{Duration, Instant};

    #[test]
    fn proc_parsers_accept_kernel_shapes_and_names_with_parentheses() {
        let cpu = parse_cpu_counters(
            "cpu  10 2 3 80 5 0 0 0 0 0\ncpu0 1 0 0 1 0\ncpu1 1 0 0 1 0\nintr 0\n",
        );
        assert_eq!(
            cpu,
            Ok(CpuCounters {
                total: 100,
                idle: 80,
                iowait: 5,
                cpu_count: 2
            })
        );
        assert_eq!(
            parse_load_counters("1.25 2.50 3.75 4/321 99\n"),
            Ok(LoadCounters {
                one: 1.25,
                five: 2.5,
                fifteen: 3.75,
                runnable: 4,
                total: 321
            })
        );
        let process = parse_process_counters(
            "42 (worker (dbus)) R 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21",
        );
        assert!(process.is_ok());
        let Some(process) = process.ok() else {
            return;
        };
        assert_eq!(process.name, "worker (dbus)");
        assert_eq!(process.state, 'R');
        assert_eq!(process.cpu_ticks, 23);
        assert_eq!(process.started_at_ticks, 19);
    }

    #[test]
    fn interval_reports_cpu_iowait_blocking_and_sorted_processes() {
        let start = Instant::now();
        let old_processes = HashMap::from([
            (7, process("adapter", 'S', 100, 1)),
            (8, process("reused", 'S', 100, 1)),
        ]);
        let new_processes = HashMap::from([
            (7, process("adapter", 'D', 140, 1)),
            (8, process("reused", 'R', 900, 2)),
            (9, process("new", 'R', 50, 3)),
        ]);
        let previous = sample(start, 1_000, 700, 20, old_processes);
        let current = sample(
            start + Duration::from_secs(30),
            1_400,
            900,
            40,
            new_processes,
        );
        let activity = activity_between(&previous, &current);
        let SystemActivity::Available {
            window_seconds,
            system_cpu_percent,
            system_iowait_percent,
            blocked_processes,
            top_processes,
            ..
        } = activity
        else {
            return;
        };
        assert!((window_seconds - 30.0).abs() < f64::EPSILON);
        assert!((system_cpu_percent - 50.0).abs() < f64::EPSILON);
        assert!((system_iowait_percent - 5.0).abs() < f64::EPSILON);
        assert_eq!(blocked_processes, 1);
        assert_eq!(top_processes.len(), 1);
        assert_eq!(top_processes[0].pid, 7);
        assert!((top_processes[0].cpu_percent - 20.0).abs() < f64::EPSILON);
        assert_eq!(top_processes[0].state, "D");
    }

    fn process(name: &str, state: char, cpu_ticks: u64, started_at_ticks: u64) -> ProcessCounters {
        ProcessCounters {
            name: name.to_owned(),
            state,
            cpu_ticks,
            started_at_ticks,
        }
    }

    fn sample(
        captured_at: Instant,
        total: u64,
        idle: u64,
        iowait: u64,
        processes: HashMap<u32, ProcessCounters>,
    ) -> ProcSample {
        ProcSample {
            captured_at,
            cpu: CpuCounters {
                total,
                idle,
                iowait,
                cpu_count: 2,
            },
            load: LoadCounters {
                one: 6.0,
                five: 3.0,
                fifteen: 2.0,
                runnable: 4,
                total: 300,
            },
            processes,
        }
    }
}
