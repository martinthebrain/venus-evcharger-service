//! Native non-DBus energy connector registry.

mod command;
mod common;
mod http;
mod modbus;
mod opendtu;

use crate::energy::{ConnectorType, EnergySourceDefinition, EnergySourceSnapshot};
use crate::error::{HelperError, Result};

/// One connector step; multi-request connectors return `None` until complete.
pub trait EnergyConnector: Send {
    /// Execute at most one external I/O operation.
    ///
    /// # Errors
    ///
    /// Returns a classified, non-sensitive transport or payload error.
    fn read_step(
        &mut self,
        source: &EnergySourceDefinition,
        observed_at: f64,
        timeout_seconds: f64,
    ) -> Result<Option<EnergySourceSnapshot>>;
}

/// Build one validated connector from its dedicated configuration file.
///
/// # Errors
///
/// Returns an error for missing files or malformed connector settings.
pub fn build_connector(
    source: &EnergySourceDefinition,
    default_timeout_seconds: f64,
) -> Result<Box<dyn EnergyConnector>> {
    match source.connector_type {
        Some(ConnectorType::TemplateHttp) => {
            http::TemplateHttpConnector::load(source, default_timeout_seconds)
                .map(|connector| Box::new(connector) as Box<dyn EnergyConnector>)
        }
        Some(ConnectorType::CommandJson) => {
            command::CommandJsonConnector::load(source, default_timeout_seconds)
                .map(|connector| Box::new(connector) as Box<dyn EnergyConnector>)
        }
        Some(ConnectorType::OpenDtuHttp) => {
            opendtu::OpenDtuConnector::load(source, default_timeout_seconds)
                .map(|connector| Box::new(connector) as Box<dyn EnergyConnector>)
        }
        Some(ConnectorType::Modbus) => {
            modbus::ModbusConnector::load(source, default_timeout_seconds)
                .map(|connector| Box::new(connector) as Box<dyn EnergyConnector>)
        }
        None => Err(HelperError::Configuration(format!(
            "energy source {:?} has no connector",
            source.source_id
        ))),
    }
}
