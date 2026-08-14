//! Template HTTP/JSON external energy connector.

use crate::connectors::EnergyConnector;
use crate::connectors::common::{
    AuthSettings, JsonHttpClient, finite, load_connector_document, optional_bool, optional_number,
    optional_string, optional_text, resolved_url, section_text,
};
use crate::energy::{EnergySourceDefinition, EnergySourceSnapshot};
use crate::error::{HelperError, Result};

const RESPONSE_PATH_KEYS: [&str; 9] = [
    "SocPath",
    "UsableCapacityWhPath",
    "BatteryPowerPath",
    "AcPowerPath",
    "PvInputPowerPath",
    "GridInteractionPath",
    "OperatingModePath",
    "OnlinePath",
    "ConfidencePath",
];

#[derive(Clone, Debug)]
struct ResponsePaths {
    soc: Option<String>,
    usable_capacity_wh: Option<String>,
    battery_power: Option<String>,
    ac_power: Option<String>,
    pv_input_power: Option<String>,
    grid_interaction: Option<String>,
    operating_mode: Option<String>,
    online: Option<String>,
    confidence: Option<String>,
}

impl ResponsePaths {
    fn load(document: &crate::ini::IniDocument, section: &str) -> Self {
        let paths: Vec<Option<String>> = RESPONSE_PATH_KEYS
            .iter()
            .map(|key| optional_text(document.get(section, key)))
            .collect();
        Self {
            soc: paths[0].clone(),
            usable_capacity_wh: paths[1].clone(),
            battery_power: paths[2].clone(),
            ac_power: paths[3].clone(),
            pv_input_power: paths[4].clone(),
            grid_interaction: paths[5].clone(),
            operating_mode: paths[6].clone(),
            online: paths[7].clone(),
            confidence: paths[8].clone(),
        }
    }

    fn has_readable_value(&self) -> bool {
        [
            &self.soc,
            &self.usable_capacity_wh,
            &self.battery_power,
            &self.ac_power,
            &self.pv_input_power,
            &self.grid_interaction,
        ]
        .into_iter()
        .any(Option::is_some)
    }
}

/// One cached template connector with bounded HTTP transport.
pub struct TemplateHttpConnector {
    client: JsonHttpClient,
    timeout_seconds: f64,
    method: String,
    url: String,
    base_url: String,
    paths: ResponsePaths,
}

impl TemplateHttpConnector {
    pub fn load(source: &EnergySourceDefinition, default_timeout: f64) -> Result<Self> {
        let document = load_connector_document(&source.config_path)?;
        let base_url = section_text(&document, "Adapter", "BaseUrl", "");
        let configured_timeout = finite(document.get("Adapter", "RequestTimeoutSeconds"));
        let timeout_seconds = configured_timeout
            .filter(|value| *value > 0.0)
            .unwrap_or(default_timeout);
        let configured_method =
            section_text(&document, "EnergyRequest", "Method", "GET").to_ascii_uppercase();
        let method = if matches!(configured_method.as_str(), "GET" | "POST" | "PUT" | "PATCH") {
            configured_method
        } else {
            "GET".to_owned()
        };
        let url = resolved_url(
            &base_url,
            &section_text(&document, "EnergyRequest", "Url", ""),
        )?;
        if url.is_empty() {
            return Err(HelperError::Configuration(format!(
                "energy source {:?} requires EnergyRequest.Url",
                source.source_id
            )));
        }
        let paths = ResponsePaths::load(&document, "EnergyResponse");
        if !paths.has_readable_value() && source.usable_capacity_wh.is_none() {
            return Err(HelperError::Configuration(format!(
                "energy source {:?} requires a readable response path or usable capacity",
                source.source_id
            )));
        }
        Ok(Self {
            client: JsonHttpClient::new(AuthSettings::load(&document)?),
            timeout_seconds,
            method,
            url,
            base_url,
            paths,
        })
    }
}

impl EnergyConnector for TemplateHttpConnector {
    fn read_step(
        &mut self,
        source: &EnergySourceDefinition,
        observed_at: f64,
        timeout_seconds: f64,
    ) -> Result<Option<EnergySourceSnapshot>> {
        let payload = self.client.request_json(
            &self.method,
            &self.url,
            self.timeout_seconds.min(timeout_seconds),
        )?;
        let soc = optional_number(&payload, self.paths.soc.as_deref())?
            .filter(|value| (0.0..=100.0).contains(value));
        let configured_capacity =
            optional_number(&payload, self.paths.usable_capacity_wh.as_deref())?;
        let usable_capacity_wh = match configured_capacity {
            Some(value) if value > 0.0 => Some(value),
            Some(_) => None,
            None => source.usable_capacity_wh,
        };
        let confidence = optional_number(&payload, self.paths.confidence.as_deref())?
            .map_or(1.0, |value| value.clamp(0.0, 1.0));
        let online = optional_bool(&payload, self.paths.online.as_deref())?.unwrap_or(true);
        Ok(Some(EnergySourceSnapshot {
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
            soc,
            usable_capacity_wh,
            usable_capacity_source: String::new(),
            installed_capacity_ah: None,
            capacity_voltage_v: None,
            capacity_nominal_voltage_v: None,
            capacity_cell_count: None,
            battery_chemistry: source.battery_chemistry.clone(),
            net_battery_power_w: optional_number(&payload, self.paths.battery_power.as_deref())?,
            charge_limit_power_w: None,
            discharge_limit_power_w: None,
            ac_power_w: optional_number(&payload, self.paths.ac_power.as_deref())?,
            pv_input_power_w: optional_number(&payload, self.paths.pv_input_power.as_deref())?,
            grid_interaction_w: optional_number(&payload, self.paths.grid_interaction.as_deref())?,
            ac_power_scope_key: String::new(),
            pv_input_power_scope_key: String::new(),
            grid_interaction_scope_key: String::new(),
            operating_mode: optional_string(&payload, self.paths.operating_mode.as_deref())?
                .unwrap_or_default(),
            online,
            confidence,
            captured_at: Some(observed_at),
            physical_id: source.physical_id.clone(),
            physical_priority: source.physical_priority,
        }))
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;
    use std::time::Duration;

    use tempfile::tempdir;

    use super::TemplateHttpConnector;
    use crate::connectors::EnergyConnector;
    use crate::energy::{ConnectorType, EnergyRole, EnergySourceDefinition};

    fn source(config_path: String) -> EnergySourceDefinition {
        EnergySourceDefinition {
            source_id: "hybrid".to_owned(),
            profile_name: "template-http-hybrid".to_owned(),
            role: EnergyRole::HybridInverter,
            connector_type: Some(ConnectorType::TemplateHttp),
            config_path,
            service_name: String::new(),
            usable_capacity_wh: Some(5_000.0),
            battery_chemistry: "lfp".to_owned(),
            capacity_auto_estimate: false,
            capacity_estimate_min_soc: 95.0,
            capacity_startup_recheck_seconds: 300.0,
            estimated_capacity_wh: None,
            estimated_capacity_ah: None,
            estimated_capacity_nominal_voltage_v: None,
            estimated_capacity_cell_count: None,
            physical_id: "bank-a".to_owned(),
            physical_priority: 4,
        }
    }

    #[test]
    fn template_http_maps_nested_values_and_normalizes_invalid_method_to_get()
    -> Result<(), Box<dyn std::error::Error>> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let address = listener.local_addr()?;
        let server = thread::spawn(move || -> std::io::Result<String> {
            let (mut stream, _) = listener.accept()?;
            stream.set_read_timeout(Some(Duration::from_secs(2)))?;
            let mut request = [0_u8; 4096];
            let count = stream.read(&mut request)?;
            let body = r#"{"battery":{"soc":80.0,"capacity_wh":10000.0,"power_w":-1200.0},"solar":{"pv_w":3200.0},"meter":{"grid_w":-450.0},"online":1.5,"confidence":0.8}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )?;
            Ok(String::from_utf8_lossy(&request[..count]).into_owned())
        });
        let directory = tempdir()?;
        let config_path = directory.path().join("connector.ini");
        fs::write(
            &config_path,
            format!(
                "[Adapter]\nBaseUrl=http://{address}\nRequestTimeoutSeconds=0.5\n[EnergyRequest]\nMethod=DELETE\nUrl=/energy\n[EnergyResponse]\nSocPath=battery.soc\nUsableCapacityWhPath=battery.capacity_wh\nBatteryPowerPath=battery.power_w\nPvInputPowerPath=solar.pv_w\nGridInteractionPath=meter.grid_w\nOnlinePath=online\nConfidencePath=confidence\n"
            ),
        )?;
        let definition = source(config_path.to_string_lossy().into_owned());
        let mut connector = TemplateHttpConnector::load(&definition, 1.0)?;
        let snapshot = connector
            .read_step(&definition, 100.0, 1.0)?
            .ok_or("template connector did not complete")?;
        let request = server.join().map_err(|_| "HTTP test server failed")??;

        assert!(request.starts_with("GET /energy HTTP/1.1\r\n"));
        assert_eq!(snapshot.soc, Some(80.0));
        assert_eq!(snapshot.usable_capacity_wh, Some(10_000.0));
        assert_eq!(snapshot.net_battery_power_w, Some(-1_200.0));
        assert_eq!(snapshot.pv_input_power_w, Some(3_200.0));
        assert_eq!(snapshot.grid_interaction_w, Some(-450.0));
        assert!(snapshot.online);
        assert!((snapshot.confidence - 0.8).abs() < f64::EPSILON);
        assert_eq!(snapshot.captured_at, Some(100.0));
        assert_eq!(snapshot.physical_id, "bank-a");
        assert_eq!(snapshot.physical_priority, 4);
        Ok(())
    }

    #[test]
    fn relative_template_url_without_base_is_rejected_during_load()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempdir()?;
        let config_path = directory.path().join("connector.ini");
        fs::write(
            &config_path,
            "[EnergyRequest]\nUrl=/energy\n[EnergyResponse]\nSocPath=soc\n",
        )?;
        let definition = source(config_path.to_string_lossy().into_owned());
        assert!(TemplateHttpConnector::load(&definition, 1.0).is_err());
        Ok(())
    }
}
