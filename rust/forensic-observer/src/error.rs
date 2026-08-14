//! Error vocabulary for bounded observer operations.

use std::fmt::{Display, Formatter};
use std::io;

/// Result type used throughout the observer.
pub type Result<T> = std::result::Result<T, ObserverError>;

/// One classified observer failure with a non-sensitive diagnostic message.
#[derive(Debug, Eq, PartialEq)]
pub enum ObserverError {
    /// Configuration is missing or violates its contract.
    Configuration(String),
    /// A bounded input could not be read or decoded.
    Input(String),
    /// A subprocess could not be managed.
    Process(String),
    /// An incident artifact could not be persisted.
    Storage(String),
}

impl ObserverError {
    /// Build an input error from one IO failure and stable context label.
    #[must_use]
    pub fn input(context: &str, error: &io::Error) -> Self {
        Self::Input(format!("{context}: {error}"))
    }

    /// Build a storage error from one IO failure and stable context label.
    #[must_use]
    pub fn storage(context: &str, error: &io::Error) -> Self {
        Self::Storage(format!("{context}: {error}"))
    }
}

impl Display for ObserverError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Configuration(message) => write!(formatter, "configuration error: {message}"),
            Self::Input(message) => write!(formatter, "input error: {message}"),
            Self::Process(message) => write!(formatter, "process error: {message}"),
            Self::Storage(message) => write!(formatter, "storage error: {message}"),
        }
    }
}

impl std::error::Error for ObserverError {}

impl From<serde_json::Error> for ObserverError {
    fn from(error: serde_json::Error) -> Self {
        Self::Input(format!("invalid JSON: {error}"))
    }
}
