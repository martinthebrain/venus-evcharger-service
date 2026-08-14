//! Classified failures at the helper's configuration and IPC boundaries.

use std::fmt::{Display, Formatter};
use std::io;

/// Result type used by the Auto input helper.
pub type Result<T> = std::result::Result<T, HelperError>;

/// One bounded and non-sensitive helper failure.
#[derive(Debug, Eq, PartialEq)]
pub enum HelperError {
    /// Configuration is malformed or violates a required invariant.
    Configuration(String),
    /// A bounded runtime input could not be read or decoded.
    Input(String),
    /// A snapshot or command could not be persisted atomically.
    Storage(String),
    /// Runtime identity or clock state is invalid.
    Runtime(String),
}

impl HelperError {
    /// Convert one IO error without exposing file contents.
    #[must_use]
    pub fn input(context: &str, error: &io::Error) -> Self {
        Self::Input(format!("{context}: {error}"))
    }

    /// Convert one storage error without exposing payload contents.
    #[must_use]
    pub fn storage(context: &str, error: &io::Error) -> Self {
        Self::Storage(format!("{context}: {error}"))
    }
}

impl Display for HelperError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Configuration(message) => write!(formatter, "configuration error: {message}"),
            Self::Input(message) => write!(formatter, "input error: {message}"),
            Self::Storage(message) => write!(formatter, "storage error: {message}"),
            Self::Runtime(message) => write!(formatter, "runtime error: {message}"),
        }
    }
}

impl std::error::Error for HelperError {}

impl From<serde_json::Error> for HelperError {
    fn from(error: serde_json::Error) -> Self {
        Self::Input(format!("invalid JSON: {error}"))
    }
}
