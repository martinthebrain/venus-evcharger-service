//! Bounded, case-insensitive INI parsing compatible with `ConfigParser`.

use std::collections::BTreeMap;
use std::fs::File;
use std::io::Read;
use std::path::Path;

use crate::error::{HelperError, Result};

/// Parsed INI defaults used by the helper configuration boundary.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct IniDefaults {
    values: BTreeMap<String, String>,
}

/// One parsed INI document with inherited `DEFAULT` values.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct IniDocument {
    defaults: IniDefaults,
    sections: BTreeMap<String, BTreeMap<String, String>>,
}

impl IniDefaults {
    /// Parse a document and retain its case-insensitive `DEFAULT` values.
    ///
    /// # Errors
    ///
    /// Returns an error for malformed assignments, sections, or duplicate keys.
    pub fn parse(text: &str) -> Result<Self> {
        IniDocument::parse(text).map(|document| document.defaults)
    }

    /// Return one default value using ConfigParser-compatible key matching.
    #[must_use]
    pub fn get(&self, key: &str) -> Option<&str> {
        self.values
            .get(&key.to_ascii_lowercase())
            .map(String::as_str)
    }
}

impl IniDocument {
    /// Parse defaults and named sections from one INI document.
    ///
    /// Option names and section lookups are case-insensitive. Duplicate keys
    /// within the same section fail closed, matching strict `ConfigParser`
    /// behavior used by the Python implementation.
    ///
    /// # Errors
    ///
    /// Returns an error for malformed assignments, sections, or duplicate
    /// section/key pairs.
    pub fn parse(text: &str) -> Result<Self> {
        let mut defaults = BTreeMap::new();
        let mut sections: BTreeMap<String, BTreeMap<String, String>> = BTreeMap::new();
        let mut current_section: Option<String> = None;
        for (index, raw_line) in text.lines().enumerate() {
            let line_number = index + 1;
            let line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
                continue;
            }
            if line.starts_with('[') {
                if !line.ends_with(']') || line.len() < 3 {
                    return Err(HelperError::Configuration(format!(
                        "invalid section header on line {line_number}"
                    )));
                }
                let section = line[1..line.len() - 1].trim();
                if section.is_empty() {
                    return Err(HelperError::Configuration(format!(
                        "empty section header on line {line_number}"
                    )));
                }
                let normalized = section.to_ascii_lowercase();
                if normalized == "default" {
                    current_section = None;
                    continue;
                }
                if sections.contains_key(&normalized) {
                    return Err(HelperError::Configuration(format!(
                        "duplicate section {section:?} on line {line_number}"
                    )));
                }
                sections.insert(normalized.clone(), BTreeMap::new());
                current_section = Some(normalized);
                continue;
            }
            let separator = line.find('=').or_else(|| line.find(':')).ok_or_else(|| {
                HelperError::Configuration(format!("invalid assignment on line {line_number}"))
            })?;
            let key = line[..separator].trim();
            if key.is_empty() {
                return Err(HelperError::Configuration(format!(
                    "empty key on line {line_number}"
                )));
            }
            let normalized = key.to_ascii_lowercase();
            let values = match current_section.as_ref() {
                Some(section) => sections.get_mut(section).ok_or_else(|| {
                    HelperError::Configuration(format!(
                        "unknown current section on line {line_number}"
                    ))
                })?,
                None => &mut defaults,
            };
            if values
                .insert(normalized, line[separator + 1..].trim().to_owned())
                .is_some()
            {
                return Err(HelperError::Configuration(format!(
                    "duplicate key {key:?} on line {line_number}"
                )));
            }
        }
        Ok(Self {
            defaults: IniDefaults { values: defaults },
            sections,
        })
    }

    /// Borrow the document defaults.
    #[must_use]
    pub const fn defaults(&self) -> &IniDefaults {
        &self.defaults
    }

    /// Return whether a named section exists.
    #[must_use]
    pub fn has_section(&self, section: &str) -> bool {
        self.sections.contains_key(&section.to_ascii_lowercase())
    }

    /// Return one section value, inheriting from `DEFAULT` when absent.
    #[must_use]
    pub fn get(&self, section: &str, key: &str) -> Option<&str> {
        let normalized_key = key.to_ascii_lowercase();
        self.sections
            .get(&section.to_ascii_lowercase())
            .and_then(|values| values.get(&normalized_key))
            .map(String::as_str)
            .or_else(|| self.defaults.get(key))
    }

    /// Return explicitly configured entries for one section.
    #[must_use]
    pub fn section_entries(&self, section: &str) -> Option<&BTreeMap<String, String>> {
        self.sections.get(&section.to_ascii_lowercase())
    }
}

/// Read one size-bounded UTF-8 file.
///
/// # Errors
///
/// Returns an error when the file is missing, oversized, unreadable, or invalid UTF-8.
pub fn read_bounded_text(path: &Path, max_bytes: u64, label: &str) -> Result<String> {
    let file = File::open(path).map_err(|error| HelperError::input(label, &error))?;
    let size = file
        .metadata()
        .map_err(|error| HelperError::input(label, &error))?
        .len();
    if size > max_bytes {
        return Err(HelperError::Input(format!(
            "{label} exceeds {max_bytes} bytes"
        )));
    }
    let mut bytes = Vec::with_capacity(usize::try_from(size).unwrap_or(0));
    file.take(max_bytes + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| HelperError::input(label, &error))?;
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > max_bytes {
        return Err(HelperError::Input(format!(
            "{label} exceeds {max_bytes} bytes"
        )));
    }
    String::from_utf8(bytes)
        .map_err(|error| HelperError::Input(format!("{label} is not UTF-8: {error}")))
}

#[cfg(test)]
mod tests {
    use super::{IniDefaults, IniDocument};

    #[test]
    fn parses_defaults_case_insensitively_and_ignores_sections() {
        let parsed = IniDefaults::parse(
            "[DEFAULT]\nAutoInputPollIntervalMs=2000\n[Wallbox]\nAutoInputPollIntervalMs=9\n",
        );
        assert!(parsed.is_ok());
        let defaults = parsed.unwrap_or_default();
        assert_eq!(defaults.get("autoinputpollintervalms"), Some("2000"));
        assert_eq!(defaults.get("AutoInputPollIntervalMs"), Some("2000"));
    }

    #[test]
    fn rejects_case_insensitive_duplicate_defaults() {
        assert!(IniDefaults::parse("[DEFAULT]\nKey=1\nkey=2\n").is_err());
    }

    #[test]
    fn parses_sections_and_inherits_defaults() {
        let parsed = IniDocument::parse(
            "[DEFAULT]\nRequestTimeoutSeconds=2\n[Adapter]\nBaseUrl=http://device\n",
        );
        assert!(parsed.is_ok());
        let document = parsed.unwrap_or_default();
        assert!(document.has_section("adapter"));
        assert_eq!(document.get("Adapter", "BaseUrl"), Some("http://device"));
        assert_eq!(document.get("Adapter", "RequestTimeoutSeconds"), Some("2"));
    }

    #[test]
    fn rejects_duplicate_sections_and_section_keys() {
        assert!(IniDocument::parse("[A]\nKey=1\nkey=2\n").is_err());
        assert!(IniDocument::parse("[A]\nKey=1\n[a]\nOther=2\n").is_err());
    }
}
