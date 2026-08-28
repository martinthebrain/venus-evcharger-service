// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded private D-Bus connection used by scheduled gateway operations.

use std::time::Duration;

use zbus::blocking::connection::Builder;
use zbus::blocking::fdo::DBusProxy;
use zbus::blocking::{Connection, Proxy};
use zbus::zvariant::OwnedValue;

use super::BusValue;

const BUS_ITEM_INTERFACE: &str = "com.victronenergy.BusItem";

pub struct DbusClient {
    connection: Option<Connection>,
    timeout: Duration,
}

impl DbusClient {
    pub fn new(timeout: Duration) -> Result<Self, String> {
        if timeout.is_zero() {
            return Err("D-Bus timeout must be positive".to_owned());
        }
        Ok(Self {
            connection: None,
            timeout,
        })
    }

    pub fn reset(&mut self) {
        self.connection = None;
    }

    pub fn list_names(&mut self) -> Result<Vec<String>, String> {
        let proxy = DBusProxy::new(self.connection()?).map_err(|error| error.to_string())?;
        let names = proxy.list_names().map_err(|error| error.to_string())?;
        let mut result = names
            .into_iter()
            .map(|name| name.to_string())
            .collect::<Vec<_>>();
        result.sort();
        result.dedup();
        Ok(result)
    }

    pub fn read(&mut self, service: &str, path: &str) -> Result<BusValue, String> {
        validate_target(service, path)?;
        let proxy = Proxy::new(self.connection()?, service, path, BUS_ITEM_INTERFACE)
            .map_err(|error| error.to_string())?;
        let value: OwnedValue = proxy
            .call("GetValue", &())
            .map_err(|error| error.to_string())?;
        BusValue::from_owned(&value)
    }

    pub fn write(&mut self, service: &str, path: &str, value: &BusValue) -> Result<i32, String> {
        validate_target(service, path)?;
        let proxy = Proxy::new(self.connection()?, service, path, BUS_ITEM_INTERFACE)
            .map_err(|error| error.to_string())?;
        proxy
            .call("SetValue", &(value.to_owned()?,))
            .map_err(|error| error.to_string())
    }

    pub fn introspect(&mut self, service: &str, path: &str) -> Result<String, String> {
        validate_target(service, path)?;
        let proxy = Proxy::new(
            self.connection()?,
            service,
            path,
            "org.freedesktop.DBus.Introspectable",
        )
        .map_err(|error| error.to_string())?;
        proxy
            .call("Introspect", &())
            .map_err(|error| error.to_string())
    }

    fn connection(&mut self) -> Result<&Connection, String> {
        if self.connection.is_none() {
            self.connection = Some(
                Builder::system()
                    .map_err(|error| error.to_string())?
                    .method_timeout(self.timeout)
                    .build()
                    .map_err(|error| error.to_string())?,
            );
        }
        self.connection
            .as_ref()
            .ok_or_else(|| "D-Bus connection unavailable".to_owned())
    }
}

fn validate_target(service: &str, path: &str) -> Result<(), String> {
    if service.trim().is_empty() {
        return Err("D-Bus service must not be empty".to_owned());
    }
    if !path.starts_with('/') {
        return Err(format!("D-Bus object path must be absolute: {path}"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{DbusClient, validate_target};

    #[test]
    fn timeout_and_target_contracts_are_bounded() {
        assert!(DbusClient::new(Duration::ZERO).is_err());
        assert!(DbusClient::new(Duration::from_secs(1)).is_ok());
        assert!(validate_target("", "/Ac/Power").is_err());
        assert!(validate_target("com.victronenergy.system", "Ac/Power").is_err());
        assert!(validate_target("com.victronenergy.system", "/Ac/Power").is_ok());
    }
}
