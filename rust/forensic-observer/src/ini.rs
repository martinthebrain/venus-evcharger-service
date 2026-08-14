//! Bounded, case-sensitive INI parsing for observer-owned configuration.

use std::collections::BTreeMap;
use std::fs::File;
use std::io::Read;
use std::path::Path;

use crate::error::{ObserverError, Result};

/// Parsed case-sensitive INI document with Python-style `DEFAULT` inheritance.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct IniDocument {
    defaults: BTreeMap<String, String>,
    sections: BTreeMap<String, BTreeMap<String, String>>,
}

impl IniDocument {
    /// Parse one INI document.
    ///
    /// # Errors
    ///
    /// Returns an error for malformed sections, assignments, or duplicate keys.
    pub fn parse(text: &str) -> Result<Self> {
        let mut document = Self::default();
        let mut section = String::from("DEFAULT");
        for (index, raw_line) in text.lines().enumerate() {
            let line_number = index + 1;
            let line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
                continue;
            }
            if line.starts_with('[') {
                section = parse_section(line, line_number)?;
                document.sections.entry(section.clone()).or_default();
                continue;
            }
            let (key, value) = parse_assignment(line, line_number)?;
            let target = if section == "DEFAULT" {
                &mut document.defaults
            } else {
                document.sections.entry(section.clone()).or_default()
            };
            if target.insert(key.to_owned(), value.to_owned()).is_some() {
                return Err(ObserverError::Configuration(format!(
                    "duplicate key {key:?} on line {line_number}"
                )));
            }
        }
        Ok(document)
    }

    /// Return whether an explicitly named section exists.
    #[must_use]
    pub fn has_section(&self, name: &str) -> bool {
        self.sections.contains_key(name)
    }

    /// Read one key using `DEFAULT` inheritance.
    #[must_use]
    pub fn get(&self, section: &str, key: &str) -> Option<&str> {
        self.sections
            .get(section)
            .and_then(|values| values.get(key))
            .or_else(|| self.defaults.get(key))
            .map(String::as_str)
    }

    /// Read one `DEFAULT` key.
    #[must_use]
    pub fn default_value(&self, key: &str) -> Option<&str> {
        self.defaults.get(key).map(String::as_str)
    }

    /// Read one key case-insensitively for legacy-compatible adapter files.
    #[must_use]
    pub fn get_case_insensitive(&self, section: &str, key: &str) -> Option<&str> {
        self.sections
            .get(section)
            .and_then(|values| case_insensitive_value(values, key))
            .or_else(|| case_insensitive_value(&self.defaults, key))
    }
}

/// Read one size-bounded UTF-8 file.
///
/// # Errors
///
/// Returns an error when the file cannot be read, exceeds `max_bytes`, or is
/// not valid UTF-8.
pub(crate) fn read_bounded_text(path: &Path, max_bytes: u64, label: &str) -> Result<String> {
    let file = File::open(path).map_err(|error| ObserverError::input(label, &error))?;
    let size = file
        .metadata()
        .map_err(|error| ObserverError::input(label, &error))?
        .len();
    if size > max_bytes {
        return Err(ObserverError::Input(format!(
            "{label} exceeds {max_bytes} bytes"
        )));
    }
    let mut bytes = Vec::with_capacity(usize::try_from(size).unwrap_or(0));
    file.take(max_bytes + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| ObserverError::input(label, &error))?;
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > max_bytes {
        return Err(ObserverError::Input(format!(
            "{label} exceeds {max_bytes} bytes"
        )));
    }
    String::from_utf8(bytes)
        .map_err(|error| ObserverError::Input(format!("{label} is not UTF-8: {error}")))
}

fn parse_section(line: &str, line_number: usize) -> Result<String> {
    if !line.ends_with(']') || line.len() < 3 {
        return Err(ObserverError::Configuration(format!(
            "invalid section header on line {line_number}"
        )));
    }
    let name = line[1..line.len() - 1].trim();
    if name.is_empty() {
        return Err(ObserverError::Configuration(format!(
            "empty section name on line {line_number}"
        )));
    }
    Ok(name.to_owned())
}

fn parse_assignment(line: &str, line_number: usize) -> Result<(&str, &str)> {
    let separator = line.find('=').or_else(|| line.find(':')).ok_or_else(|| {
        ObserverError::Configuration(format!("invalid assignment on line {line_number}"))
    })?;
    let key = line[..separator].trim();
    if key.is_empty() {
        return Err(ObserverError::Configuration(format!(
            "empty key on line {line_number}"
        )));
    }
    Ok((key, line[separator + 1..].trim()))
}

fn case_insensitive_value<'a>(values: &'a BTreeMap<String, String>, key: &str) -> Option<&'a str> {
    values
        .iter()
        .find(|(candidate, _value)| candidate.eq_ignore_ascii_case(key))
        .map(|(_key, value)| value.as_str())
}

#[cfg(test)]
mod tests {
    use super::IniDocument;

    #[test]
    fn parser_preserves_case_and_default_inheritance() {
        let parsed = IniDocument::parse("[DEFAULT]\nHost=upper\nhost=lower\n[Adapter]\nType=x\n");
        assert!(parsed.is_ok());
        let document = parsed.unwrap_or_default();
        assert_eq!(document.get("Adapter", "Host"), Some("upper"));
        assert_eq!(document.get("Adapter", "host"), Some("lower"));
        assert_eq!(document.get_case_insensitive("Adapter", "type"), Some("x"));
    }

    #[test]
    fn malformed_and_duplicate_inputs_fail_closed() {
        assert!(IniDocument::parse("[broken\nkey=value\n").is_err());
        assert!(IniDocument::parse("[DEFAULT]\nkey=one\nkey=two\n").is_err());
        assert!(IniDocument::parse("=value\n").is_err());
    }
}
