// SPDX-License-Identifier: GPL-3.0-or-later
//! Generated semantic publication surface and path policies.

use std::collections::BTreeMap;

use serde::Deserialize;
use serde_json::{Map, Value};

use crate::dbus::TextFormat;

const CONTRACT_JSON: &str = include_str!("../../contracts/publication.json");

#[derive(Clone, Debug, Deserialize)]
pub(super) struct PublicationContract {
    pub(super) schema_version: u8,
    pub(super) evcs: BTreeMap<String, PathSpec>,
    pub(super) companion: BTreeMap<String, BTreeMap<String, PathSpec>>,
}

impl PublicationContract {
    pub(super) fn load() -> Result<Self, String> {
        let contract: Self =
            serde_json::from_str(CONTRACT_JSON).map_err(|error| error.to_string())?;
        if contract.schema_version != 1 {
            return Err("unsupported native publication contract".to_owned());
        }
        Ok(contract)
    }
}

#[derive(Clone, Debug, Deserialize)]
pub(super) struct PathSpec {
    pub(super) path: String,
    pub(super) default: Value,
    pub(super) writeable: bool,
    pub(super) formatter: Option<String>,
    pub(super) route: Option<ControlRoute>,
    pub(super) freshness_kind: String,
}

#[derive(Clone, Debug, Deserialize)]
pub(super) struct ControlRoute {
    pub(super) name: String,
    pub(super) target: String,
}

pub(super) fn validate_fields(
    fields: &Map<String, Value>,
    specs: &BTreeMap<String, PathSpec>,
) -> Result<(), String> {
    if let Some(field) = fields.keys().find(|field| !specs.contains_key(*field)) {
        return Err(format!("unknown semantic publication field: {field}"));
    }
    Ok(())
}

pub(super) fn object<'a>(
    payload: &'a Map<String, Value>,
    field: &str,
) -> Option<&'a Map<String, Value>> {
    payload.get(field).and_then(Value::as_object)
}

pub(super) fn text<'a>(payload: &'a Map<String, Value>, field: &str) -> Option<&'a str> {
    payload.get(field).and_then(Value::as_str)
}

pub(super) fn positive_u64(value: Option<&Value>) -> Option<u64> {
    match value {
        Some(Value::Number(number)) => number.as_u64().filter(|value| *value > 0),
        Some(Value::String(text)) => text.parse::<u64>().ok().filter(|value| *value > 0),
        _ => None,
    }
}

pub(super) fn path_freshness(kind: &str, path: &str, specs: &BTreeMap<String, PathSpec>) -> String {
    if kind != "evcs" {
        return "local_owned".to_owned();
    }
    specs.values().find(|spec| spec.path == path).map_or_else(
        || {
            if path == "/UpdateIndex" {
                "local_owned".to_owned()
            } else {
                "static".to_owned()
            }
        },
        |spec| spec.freshness_kind.clone(),
    )
}

pub(super) fn text_format(value: Option<&str>) -> Result<TextFormat, String> {
    match value {
        None => Ok(TextFormat::Default),
        Some("kwh") => Ok(TextFormat::Kwh),
        Some("amps") => Ok(TextFormat::Amps),
        Some("watts") => Ok(TextFormat::Watts),
        Some("volts") => Ok(TextFormat::Volts),
        Some("status") => Ok(TextFormat::Status),
        Some(value) => Err(format!("unknown D-Bus text formatter: {value}")),
    }
}

#[cfg(test)]
mod tests {
    use super::PublicationContract;

    #[test]
    fn generated_contract_is_complete_and_parseable() -> Result<(), String> {
        let contract = PublicationContract::load()?;
        assert_eq!(contract.evcs.len(), 189);
        assert_eq!(contract.companion["battery"].len(), 4);
        assert_eq!(
            contract.evcs["auto_backend_mode"].freshness_kind,
            "diagnostic"
        );
        assert_eq!(contract.evcs["ac_current_a"].freshness_kind, "local_owned");
        Ok(())
    }
}
