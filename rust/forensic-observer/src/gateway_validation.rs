//! Shared strict-schema primitives for gateway diagnostics.

use std::collections::BTreeSet;

use serde_json::{Map, Value};

use crate::error::{ObserverError, Result};

pub fn exact_object<'a>(
    value: &'a Value,
    label: &str,
    fields: &[&str],
) -> Result<&'a Map<String, Value>> {
    let object = value
        .as_object()
        .ok_or_else(|| invalid(&format!("{label} must be an object")))?;
    if key_set(object) != string_set(fields) {
        return Err(invalid(&format!("{label} fields do not match the schema")));
    }
    Ok(object)
}

pub fn key_set(object: &Map<String, Value>) -> BTreeSet<String> {
    object.keys().cloned().collect()
}

pub fn string_set(values: &[&str]) -> BTreeSet<String> {
    values.iter().map(|value| (*value).to_owned()).collect()
}

pub fn required<'a>(object: &'a Map<String, Value>, key: &str) -> Result<&'a Value> {
    object
        .get(key)
        .ok_or_else(|| invalid(&format!("missing field {key}")))
}

pub fn required_text(object: &Map<String, Value>, key: &str, allow_empty: bool) -> Result<String> {
    let value = required(object, key)?
        .as_str()
        .ok_or_else(|| invalid(&format!("{key} must be text")))?;
    if !allow_empty && value.trim().is_empty() {
        return Err(invalid(&format!("{key} must be non-empty text")));
    }
    Ok(value.to_owned())
}

pub fn required_bool(object: &Map<String, Value>, key: &str) -> Result<bool> {
    required(object, key)?
        .as_bool()
        .ok_or_else(|| invalid(&format!("{key} must be boolean")))
}

pub fn required_u64(object: &Map<String, Value>, key: &str, label: &str) -> Result<u64> {
    required(object, key)?
        .as_u64()
        .ok_or_else(|| invalid(&format!("{label} must be a non-negative integer")))
}

pub fn positive_number(object: &Map<String, Value>, key: &str, label: &str) -> Result<f64> {
    let value = finite_number(object, key, label)?;
    if value <= 0.0 {
        return Err(invalid(&format!("{label} must be positive")));
    }
    Ok(value)
}

pub fn nonnegative_number(object: &Map<String, Value>, key: &str, label: &str) -> Result<f64> {
    let value = finite_number(object, key, label)?;
    if value < 0.0 {
        return Err(invalid(&format!("{label} must be non-negative")));
    }
    Ok(value)
}

pub fn invalid(message: &str) -> ObserverError {
    ObserverError::Input(message.to_owned())
}

fn finite_number(object: &Map<String, Value>, key: &str, label: &str) -> Result<f64> {
    let value = required(object, key)?
        .as_f64()
        .ok_or_else(|| invalid(&format!("{label} must be numeric")))?;
    if !value.is_finite() {
        return Err(invalid(&format!("{label} must be finite")));
    }
    Ok(value)
}
