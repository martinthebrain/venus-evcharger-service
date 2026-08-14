//! `OpenDTU` HTTP connector with one-request-per-step progress.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Map, Value};

use crate::connectors::EnergyConnector;
use crate::connectors::common::{
    AuthSettings, JsonHttpClient, boolean, finite, load_connector_document, resolved_url,
    section_text,
};
use crate::energy::{EnergyRole, EnergySourceDefinition, EnergySourceSnapshot};
use crate::error::{HelperError, Result};

struct OpenDtuProgress {
    payload: Map<String, Value>,
    inverters: BTreeMap<String, Map<String, Value>>,
    detail_serials: Vec<String>,
    next_detail_index: usize,
}

/// Cached `OpenDTU` configuration and multi-step request state.
pub struct OpenDtuConnector {
    client: JsonHttpClient,
    base_url: String,
    status_url: String,
    inverter_status_url: String,
    serial_filter: BTreeSet<String>,
    max_data_age_seconds: f64,
    timeout_seconds: f64,
    progress: Option<OpenDtuProgress>,
}

impl OpenDtuConnector {
    pub fn load(source: &EnergySourceDefinition, default_timeout: f64) -> Result<Self> {
        let document = load_connector_document(&source.config_path)?;
        let base_url = section_text(&document, "Adapter", "BaseUrl", "");
        let status_url = resolved_url(
            &base_url,
            &section_text(&document, "OpenDTU", "StatusUrl", "/api/livedata/status"),
        )?;
        if status_url.is_empty() {
            return Err(HelperError::Configuration(format!(
                "energy source {:?} requires OpenDTU.StatusUrl or Adapter.BaseUrl",
                source.source_id
            )));
        }
        let configured_timeout = finite(document.get("Adapter", "RequestTimeoutSeconds"));
        let max_data_age_seconds = finite(document.get("OpenDTU", "MaxDataAgeSeconds"))
            .filter(|value| *value >= 0.0)
            .unwrap_or(600.0);
        let serial_filter = document
            .get("OpenDTU", "InverterSerials")
            .unwrap_or("")
            .split(',')
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .collect();
        Ok(Self {
            client: JsonHttpClient::new(AuthSettings::load(&document)?),
            base_url: base_url.clone(),
            status_url,
            inverter_status_url: resolved_url(
                &base_url,
                &section_text(
                    &document,
                    "OpenDTU",
                    "InverterStatusUrl",
                    "/api/livedata/status?inv=${serial}",
                ),
            )?,
            serial_filter,
            max_data_age_seconds,
            timeout_seconds: configured_timeout
                .filter(|value| *value > 0.0)
                .unwrap_or(default_timeout),
            progress: None,
        })
    }

    fn start(&self, timeout_seconds: f64) -> Result<OpenDtuProgress> {
        let payload = self
            .client
            .request_json("GET", &self.status_url, timeout_seconds)?;
        let raw = unique_inverters(&payload, &self.serial_filter);
        let mut inverters = BTreeMap::new();
        let mut detail_serials = Vec::new();
        for inverter in raw {
            let serial = serial(&inverter);
            if has_measurements(&inverter) {
                inverters.insert(serial, inverter);
            } else {
                detail_serials.push(serial);
            }
        }
        Ok(OpenDtuProgress {
            payload,
            inverters,
            detail_serials,
            next_detail_index: 0,
        })
    }

    fn continue_read(&self, progress: &mut OpenDtuProgress, timeout_seconds: f64) -> Result<()> {
        let serial = progress
            .detail_serials
            .get(progress.next_detail_index)
            .ok_or_else(|| HelperError::Runtime("OpenDTU progress is complete".to_owned()))?;
        let serial_token = ["$", "{", "serial", "}"].concat();
        let url = self.inverter_status_url.replace(&serial_token, serial);
        let payload = self.client.request_json("GET", &url, timeout_seconds)?;
        let inverter = detail_inverter(&payload, serial)?;
        progress.inverters.insert(serial.clone(), inverter);
        progress.next_detail_index = progress.next_detail_index.saturating_add(1);
        Ok(())
    }

    fn completed_snapshot(
        &self,
        source: &EnergySourceDefinition,
        progress: &OpenDtuProgress,
        observed_at: f64,
    ) -> EnergySourceSnapshot {
        let inverters: Vec<&Map<String, Value>> = progress.inverters.values().collect();
        let online: Vec<&Map<String, Value>> = inverters
            .iter()
            .copied()
            .filter(|inverter| inverter_online(inverter, self.max_data_age_seconds))
            .collect();
        let mut ac_power_w = sum_optional(online.iter().map(|item| ac_power(item)));
        let pv_input_power_w = sum_optional(online.iter().map(|item| dc_power(item)));
        let plausible_idle = allows_unreachable_idle(source)
            && !inverters.is_empty()
            && !inverters.iter().any(|item| producing(item))
            && online.is_empty()
            && zeroish(ac_power_w)
            && zeroish(pv_input_power_w)
            && !radio_problem(&progress.payload)
            && inverters.iter().all(|item| unreachable_idle(item));
        if plausible_idle {
            ac_power_w = Some(0.0);
        }
        let online_state = !inverters.is_empty() && (!online.is_empty() || plausible_idle);
        let confidence = if inverters.is_empty() {
            0.0
        } else if plausible_idle {
            1.0
        } else {
            count_as_f64(online.len()) / count_as_f64(inverters.len())
        };
        EnergySourceSnapshot {
            source_id: source.source_id.clone(),
            role: source.role,
            service_name: if source.service_name.is_empty() {
                if self.base_url.is_empty() {
                    source.config_path.clone()
                } else {
                    self.base_url.clone()
                }
            } else {
                source.service_name.clone()
            },
            soc: None,
            usable_capacity_wh: source.usable_capacity_wh,
            usable_capacity_source: String::new(),
            installed_capacity_ah: None,
            capacity_voltage_v: None,
            capacity_nominal_voltage_v: None,
            capacity_cell_count: None,
            battery_chemistry: source.battery_chemistry.clone(),
            net_battery_power_w: None,
            charge_limit_power_w: None,
            discharge_limit_power_w: None,
            ac_power_w,
            pv_input_power_w,
            grid_interaction_w: None,
            ac_power_scope_key: String::new(),
            pv_input_power_scope_key: String::new(),
            grid_interaction_scope_key: String::new(),
            operating_mode: if online.iter().any(|item| producing(item)) {
                "producing".to_owned()
            } else {
                "idle".to_owned()
            },
            online: online_state,
            confidence,
            captured_at: Some(observed_at),
            physical_id: source.physical_id.clone(),
            physical_priority: source.physical_priority,
        }
    }
}

fn count_as_f64(count: usize) -> f64 {
    f64::from(u32::try_from(count).unwrap_or(u32::MAX))
}

impl EnergyConnector for OpenDtuConnector {
    fn read_step(
        &mut self,
        source: &EnergySourceDefinition,
        observed_at: f64,
        timeout_seconds: f64,
    ) -> Result<Option<EnergySourceSnapshot>> {
        let bounded = self.timeout_seconds.min(timeout_seconds);
        let progress = match self.progress.take() {
            Some(mut progress) => {
                self.continue_read(&mut progress, bounded)?;
                progress
            }
            None => self.start(bounded)?,
        };
        if progress.next_detail_index < progress.detail_serials.len() {
            self.progress = Some(progress);
            return Ok(None);
        }
        Ok(Some(self.completed_snapshot(
            source,
            &progress,
            observed_at,
        )))
    }
}

fn unique_inverters(
    payload: &Map<String, Value>,
    serial_filter: &BTreeSet<String>,
) -> Vec<Map<String, Value>> {
    let Some(Value::Array(values)) = payload.get("inverters") else {
        return Vec::new();
    };
    let filtered: Vec<Map<String, Value>> = values
        .iter()
        .filter_map(Value::as_object)
        .filter(|item| {
            let value = serial(item);
            !value.is_empty() && (serial_filter.is_empty() || serial_filter.contains(&value))
        })
        .cloned()
        .collect();
    let mut counts = BTreeMap::new();
    for inverter in &filtered {
        let value = serial(inverter);
        counts
            .entry(value)
            .and_modify(|count: &mut usize| *count = count.saturating_add(1))
            .or_insert(1);
    }
    filtered
        .into_iter()
        .filter(|item| counts.get(&serial(item)) == Some(&1))
        .collect()
}

fn detail_inverter(payload: &Map<String, Value>, expected: &str) -> Result<Map<String, Value>> {
    let matches: Vec<Map<String, Value>> = payload
        .get("inverters")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter(|item| serial(item) == expected)
        .cloned()
        .collect();
    if matches.len() != 1 {
        return Err(HelperError::Input(format!(
            "OpenDTU detail response does not uniquely identify inverter {expected}"
        )));
    }
    matches.into_iter().next().ok_or_else(|| {
        HelperError::Input(format!(
            "OpenDTU detail response is missing inverter {expected}"
        ))
    })
}

fn serial(inverter: &Map<String, Value>) -> String {
    inverter
        .get("serial")
        .map(|value| match value {
            Value::String(text) => text.clone(),
            other => other.to_string(),
        })
        .unwrap_or_default()
        .trim()
        .to_owned()
}

fn has_measurements(inverter: &Map<String, Value>) -> bool {
    inverter.contains_key("AC") || unreachable_idle(inverter)
}

fn inverter_online(inverter: &Map<String, Value>, maximum_age: f64) -> bool {
    if !truthy(inverter.get("reachable")) {
        return false;
    }
    numeric(inverter.get("data_age")).is_some_and(|age| (0.0..=maximum_age).contains(&age))
}

fn producing(inverter: &Map<String, Value>) -> bool {
    truthy(inverter.get("producing"))
}

fn ac_power(inverter: &Map<String, Value>) -> Option<f64> {
    metric(inverter, &["AC", "0", "Power", "v"])
}

fn dc_power(inverter: &Map<String, Value>) -> Option<f64> {
    let channels = inverter.get("DC")?.as_object()?;
    sum_optional(
        channels
            .values()
            .filter_map(Value::as_object)
            .map(|channel| metric(channel, &["Power", "v"])),
    )
}

fn metric(container: &Map<String, Value>, path: &[&str]) -> Option<f64> {
    let mut current = container.get(*path.first()?)?;
    for token in &path[1..] {
        current = current.as_object()?.get(*token)?;
    }
    numeric(Some(current))
}

fn numeric(value: Option<&Value>) -> Option<f64> {
    value
        .and_then(|item| match item {
            Value::Number(number) => number.as_f64(),
            Value::String(text) => text.trim().parse::<f64>().ok(),
            _ => None,
        })
        .filter(|number| number.is_finite())
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(flag)) => *flag,
        Some(Value::Number(number)) => number.as_i64().is_some_and(|item| item != 0),
        Some(Value::String(text)) => boolean(Some(text)),
        _ => false,
    }
}

fn unreachable_idle(inverter: &Map<String, Value>) -> bool {
    !truthy(inverter.get("reachable")) && !producing(inverter)
}

fn radio_problem(payload: &Map<String, Value>) -> bool {
    payload
        .get("hints")
        .and_then(Value::as_object)
        .is_some_and(|hints| truthy(hints.get("radio_problem")))
}

fn zeroish(value: Option<f64>) -> bool {
    value.is_none_or(|item| item.abs() <= 0.5)
}

fn sum_optional(values: impl Iterator<Item = Option<f64>>) -> Option<f64> {
    let values: Vec<f64> = values.flatten().collect();
    (!values.is_empty()).then(|| values.iter().sum())
}

fn allows_unreachable_idle(source: &EnergySourceDefinition) -> bool {
    source.profile_name == "opendtu-pvinverter" || source.role == EnergyRole::Inverter
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{ac_power, dc_power, unique_inverters};

    #[test]
    fn opendtu_values_are_selected_and_duplicate_serials_fail_closed() {
        let inverter = json!({
            "serial": "1",
            "AC": {"0": {"Power": {"v": 123.0}}},
            "DC": {"0": {"Power": {"v": 70.0}}, "1": {"Power": {"v": 50.0}}}
        });
        let object = inverter.as_object().cloned().unwrap_or_default();
        assert_eq!(ac_power(&object), Some(123.0));
        assert_eq!(dc_power(&object), Some(120.0));
        let payload = json!({"inverters": [inverter.clone(), inverter]});
        assert!(
            unique_inverters(
                &payload.as_object().cloned().unwrap_or_default(),
                &std::collections::BTreeSet::new()
            )
            .is_empty()
        );
    }
}
