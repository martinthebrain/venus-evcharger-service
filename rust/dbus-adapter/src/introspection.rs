// SPDX-License-Identifier: GPL-3.0-or-later
//! Coalesced low-rate introspection of only the D-Bus fields used by the gateway.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use quick_xml::Reader;
use quick_xml::events::Event;
use serde::Serialize;
use serde_json::json;

use crate::broker::{DbusOperation, DbusResult, DbusResultValue};
use crate::config::IniConfig;
use crate::mailbox::atomic_json;
use crate::reader::EnergyReader;
use crate::resources::ResourceState;

const MAX_XML_BYTES: usize = 256 * 1024;
const MAX_XML_EVENTS: usize = 8_192;
const MIN_FULL_SCAN_SECONDS: f64 = 60.0;

#[derive(Clone, Debug, Serialize)]
struct Finding {
    status: String,
    confidence: f64,
    interfaces: Vec<String>,
    children: Vec<String>,
    source: String,
    reason: String,
    last_success_at: Option<f64>,
    last_error: String,
    retry_after: f64,
}

pub struct IntrospectionManager {
    enabled: bool,
    full_scan_interval: Duration,
    queue: VecDeque<Target>,
    queued: BTreeSet<(String, String)>,
    active: Option<Target>,
    findings: BTreeMap<String, BTreeMap<String, Finding>>,
    next_full_scan: Instant,
    last_full_scan_at: f64,
}

#[derive(Clone, Debug)]
struct Target {
    service: String,
    path: String,
    owner: Option<PathBuf>,
}

impl IntrospectionManager {
    pub fn from_config(config: &IniConfig) -> Self {
        let interval = config
            .f64("DbusIntrospectionFullScanIntervalSeconds", 21_600.0)
            .max(MIN_FULL_SCAN_SECONDS);
        Self {
            enabled: config.bool("DbusIntrospectionEnabled", true),
            full_scan_interval: Duration::from_secs_f64(interval),
            queue: VecDeque::new(),
            queued: BTreeSet::new(),
            active: None,
            findings: BTreeMap::new(),
            next_full_scan: Instant::now(),
            last_full_scan_at: 0.0,
        }
    }

    pub fn schedule_if_due(
        &mut self,
        reader: &EnergyReader,
        topology_changed: bool,
        resource_state: ResourceState,
        captured_at: f64,
    ) {
        if !self.enabled || resource_state == ResourceState::Constrained {
            return;
        }
        let now = Instant::now();
        if !topology_changed && now < self.next_full_scan {
            return;
        }
        for target in reader.introspection_targets() {
            if self.active.as_ref().is_some_and(|active| {
                (active.service.as_str(), active.path.as_str())
                    == (target.0.as_str(), target.1.as_str())
            }) || !self.queued.insert(target.clone())
            {
                continue;
            }
            self.queue.push_back(Target {
                service: target.0,
                path: target.1,
                owner: None,
            });
        }
        self.next_full_scan = now + self.full_scan_interval;
        self.last_full_scan_at = captured_at;
    }

    pub fn next_operation(&mut self) -> Option<DbusOperation> {
        if self.active.is_some() {
            return None;
        }
        let target = self.queue.pop_front()?;
        self.queued
            .remove(&(target.service.clone(), target.path.clone()));
        self.active = Some(target.clone());
        Some(DbusOperation::Introspect {
            service: target.service,
            path: target.path,
        })
    }

    pub fn schedule_command(&mut self, owner: PathBuf, service: &str, path: &str) -> bool {
        if !self.enabled || service.trim().is_empty() || !path.starts_with('/') {
            return false;
        }
        let key = (service.to_owned(), path.to_owned());
        if self.active.as_ref().is_some_and(|active| {
            (active.service.as_str(), active.path.as_str()) == (service, path)
        }) || !self.queued.insert(key)
        {
            return false;
        }
        self.queue.push_back(Target {
            service: service.to_owned(),
            path: path.to_owned(),
            owner: Some(owner),
        });
        true
    }

    pub fn operation_submission_failed(&mut self) {
        let Some(target) = self.active.take() else {
            return;
        };
        if self
            .queued
            .insert((target.service.clone(), target.path.clone()))
        {
            self.queue.push_front(target);
        }
    }

    pub fn handle_result(
        &mut self,
        response: DbusResult,
        captured_at: f64,
    ) -> Result<Option<PathBuf>, String> {
        let Some(target) = self.active.take() else {
            return Err("introspection result arrived without an active target".to_owned());
        };
        let finding = match response.result {
            Ok(DbusResultValue::Xml(xml)) => {
                let (interfaces, children) = parse_names(&xml)?;
                Finding {
                    status: "fresh".to_owned(),
                    confidence: 0.8,
                    interfaces,
                    children,
                    source: "gateway".to_owned(),
                    reason: "gateway-introspection".to_owned(),
                    last_success_at: Some(captured_at),
                    last_error: String::new(),
                    retry_after: captured_at,
                }
            }
            Ok(_) => failed_finding(captured_at, "wrong D-Bus response type"),
            Err(error) => failed_finding(captured_at, &error),
        };
        self.findings
            .entry(target.service)
            .or_default()
            .insert(target.path, finding);
        Ok(target.owner)
    }

    pub fn write_snapshot(&self, path: &Path, captured_at: f64) -> Result<(), String> {
        if !self.enabled {
            return Ok(());
        }
        let services = self
            .findings
            .iter()
            .map(|(service, paths)| {
                let last_updated_at = paths
                    .values()
                    .filter_map(|finding| finding.last_success_at)
                    .fold(captured_at, f64::max);
                (
                    service.clone(),
                    json!({"paths": paths, "last_updated_at": last_updated_at}),
                )
            })
            .collect::<BTreeMap<_, _>>();
        atomic_json(
            path,
            &json!({
                "schema_version": 1,
                "captured_at": captured_at,
                "heartbeat_at": captured_at,
                "worker_state": "gateway",
                "writer_pid": std::process::id(),
                "queue_depth": self.queue.len() + usize::from(self.active.is_some()),
                "last_full_scan_at": self.last_full_scan_at,
                "services": services,
            }),
        )
    }

    pub fn queue_depth(&self) -> usize {
        self.queue.len() + usize::from(self.active.is_some())
    }
}

fn failed_finding(captured_at: f64, error: &str) -> Finding {
    Finding {
        status: "unresponsive-backoff".to_owned(),
        confidence: 0.55,
        interfaces: Vec::new(),
        children: Vec::new(),
        source: "gateway".to_owned(),
        reason: "gateway-introspection".to_owned(),
        last_success_at: None,
        last_error: error.to_owned(),
        retry_after: captured_at + 900.0,
    }
}

fn parse_names(xml: &str) -> Result<(Vec<String>, Vec<String>), String> {
    if xml.len() > MAX_XML_BYTES {
        return Err("D-Bus introspection XML is too large".to_owned());
    }
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);
    let mut interfaces = BTreeSet::new();
    let mut children = BTreeSet::new();
    let mut events = 0_usize;
    loop {
        events += 1;
        if events > MAX_XML_EVENTS {
            return Err("D-Bus introspection XML is too complex".to_owned());
        }
        match reader.read_event().map_err(|error| error.to_string())? {
            Event::Start(element) | Event::Empty(element) => {
                let target = if element.name().as_ref() == b"interface" {
                    Some(&mut interfaces)
                } else if element.name().as_ref() == b"node" {
                    Some(&mut children)
                } else {
                    None
                };
                if let Some(target) = target {
                    for attribute in element.attributes().with_checks(false) {
                        let attribute = attribute.map_err(|error| error.to_string())?;
                        if attribute.key.as_ref() == b"name" {
                            target.insert(
                                attribute
                                    .unescape_value()
                                    .map_err(|error| error.to_string())?
                                    .into_owned(),
                            );
                            break;
                        }
                    }
                }
            }
            Event::Eof => break,
            _ => {}
        }
    }
    Ok((
        interfaces.into_iter().collect(),
        children.into_iter().collect(),
    ))
}

#[cfg(test)]
mod tests {
    use super::parse_names;

    #[test]
    fn bounded_parser_extracts_only_interfaces_and_children() -> Result<(), String> {
        let (interfaces, children) = parse_names(
            r#"<node><interface name="com.victronenergy.BusItem"/><node name="Ac"/></node>"#,
        )?;
        assert_eq!(interfaces, ["com.victronenergy.BusItem"]);
        assert_eq!(children, ["Ac"]);
        Ok(())
    }
}
