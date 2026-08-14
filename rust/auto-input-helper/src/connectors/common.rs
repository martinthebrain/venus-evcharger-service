//! Shared bounded configuration, JSON, and HTTP helpers.

use std::io::Read;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value};

use crate::error::{HelperError, Result};
use crate::ini::{IniDocument, read_bounded_text};

const MAX_CONNECTOR_CONFIG_BYTES: u64 = 1_048_576;
const MAX_RESPONSE_BYTES: u64 = 262_144;

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct AuthSettings {
    pub username: String,
    pub password: String,
    pub digest: bool,
    pub header_name: Option<String>,
    pub header_value: Option<String>,
}

impl AuthSettings {
    pub fn load(document: &IniDocument) -> Result<Self> {
        let username = section_text(document, "Adapter", "Username", "");
        let password = document.get("Adapter", "Password").unwrap_or("").to_owned();
        let digest = boolean(document.get("Adapter", "DigestAuth"));
        let header_name = optional_text(document.get("Adapter", "AuthHeaderName"));
        let header_value = optional_text(document.get("Adapter", "AuthHeaderValue"));
        if digest && username.is_empty() {
            return Err(HelperError::Configuration(
                "DigestAuth requires Adapter.Username".to_owned(),
            ));
        }
        if header_name.is_some() != header_value.is_some() {
            return Err(HelperError::Configuration(
                "custom authentication requires both header name and value".to_owned(),
            ));
        }
        Ok(Self {
            username,
            password,
            digest,
            header_name,
            header_value,
        })
    }
}

pub struct JsonHttpClient {
    agent: ureq::Agent,
    auth: AuthSettings,
}

impl JsonHttpClient {
    pub fn new(auth: AuthSettings) -> Self {
        Self {
            agent: ureq::AgentBuilder::new().build(),
            auth,
        }
    }

    pub fn request_json(
        &self,
        method: &str,
        url: &str,
        timeout_seconds: f64,
    ) -> Result<Map<String, Value>> {
        let timeout = Duration::from_secs_f64(timeout_seconds.max(0.001));
        let mut request = self.agent.request(method, url).timeout(timeout);
        if let (Some(name), Some(value)) = (&self.auth.header_name, &self.auth.header_value) {
            request = request.set(name, value);
        }
        if !self.auth.digest && !self.auth.username.is_empty() {
            request = request.set(
                "Authorization",
                &format!(
                    "Basic {}",
                    base64(&format!("{}:{}", self.auth.username, self.auth.password))
                ),
            );
        }
        let response = match request.call() {
            Ok(response) => response,
            Err(ureq::Error::Status(401, response)) if self.auth.digest => {
                self.digest_retry(method, url, timeout, &response)?
            }
            Err(error) => return Err(http_error(error)),
        };
        decode_json_response(response)
    }

    fn digest_retry(
        &self,
        method: &str,
        url: &str,
        timeout: Duration,
        challenge_response: &ureq::Response,
    ) -> Result<ureq::Response> {
        let challenge = challenge_response
            .header("WWW-Authenticate")
            .ok_or_else(|| HelperError::Input("HTTP digest challenge is missing".to_owned()))?;
        let authorization = digest_authorization(
            challenge,
            method,
            url,
            &self.auth.username,
            &self.auth.password,
        )?;
        let mut request = self
            .agent
            .request(method, url)
            .timeout(timeout)
            .set("Authorization", &authorization);
        if let (Some(name), Some(value)) = (&self.auth.header_name, &self.auth.header_value) {
            request = request.set(name, value);
        }
        request.call().map_err(http_error)
    }
}

pub fn load_connector_document(path: &str) -> Result<IniDocument> {
    if path.trim().is_empty() {
        return Err(HelperError::Configuration(
            "external energy source requires ConfigPath".to_owned(),
        ));
    }
    let text = read_bounded_text(
        Path::new(path),
        MAX_CONNECTOR_CONFIG_BYTES,
        "energy connector configuration",
    )?;
    IniDocument::parse(&text)
}

pub fn section_text(document: &IniDocument, section: &str, key: &str, fallback: &str) -> String {
    document
        .get(section, key)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(fallback)
        .to_owned()
}

pub fn optional_text(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

pub fn finite(value: Option<&str>) -> Option<f64> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|value| value.is_finite())
}

pub fn boolean(value: Option<&str>) -> bool {
    value.is_some_and(|item| {
        matches!(
            item.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "on" | "enabled"
        )
    })
}

pub fn resolved_url(base_url: &str, path: &str) -> Result<String> {
    let path = path.trim();
    if path.is_empty() {
        return Ok(String::new());
    }
    if path.contains("://") {
        return Ok(path.to_owned());
    }
    if base_url.trim().is_empty() {
        return Err(HelperError::Configuration(format!(
            "relative URL {path:?} requires Adapter.BaseUrl"
        )));
    }
    Ok(format!(
        "{}/{}",
        base_url.trim().trim_end_matches('/'),
        path.trim_start_matches('/')
    ))
}

pub fn json_path<'a>(payload: &'a Map<String, Value>, path: &str) -> Result<&'a Value> {
    let mut current = payload;
    let mut value = None;
    for token in path
        .split('.')
        .map(str::trim)
        .filter(|item| !item.is_empty())
    {
        value = current.get(token);
        let found = value.ok_or_else(|| {
            HelperError::Input(format!("missing configured JSON response path {path:?}"))
        })?;
        if let Value::Object(next) = found {
            current = next;
        }
    }
    value.ok_or_else(|| HelperError::Input("empty JSON response path".to_owned()))
}

pub fn optional_number(payload: &Map<String, Value>, path: Option<&str>) -> Result<Option<f64>> {
    let Some(path) = path else {
        return Ok(None);
    };
    let value = json_path(payload, path)?;
    let parsed = match value {
        Value::Number(number) => number.as_f64(),
        Value::String(text) => text.trim().parse::<f64>().ok(),
        Value::Bool(flag) => Some(if *flag { 1.0 } else { 0.0 }),
        _ => None,
    };
    Ok(parsed.filter(|number| number.is_finite()))
}

pub fn optional_bool(payload: &Map<String, Value>, path: Option<&str>) -> Result<Option<bool>> {
    let Some(path) = path else {
        return Ok(None);
    };
    let value = json_path(payload, path)?;
    Ok(match value {
        Value::Bool(flag) => Some(*flag),
        Value::Number(number) => number.as_f64().map(|item| item.trunc() > 0.0),
        Value::String(text) => {
            let normalized = text.trim().to_ascii_lowercase();
            match normalized.as_str() {
                "true" | "yes" | "on" | "enabled" => Some(true),
                "false" | "no" | "off" | "disabled" => Some(false),
                _ => normalized.parse::<i64>().ok().map(|item| item > 0),
            }
        }
        _ => None,
    })
}

pub fn optional_string(payload: &Map<String, Value>, path: Option<&str>) -> Result<Option<String>> {
    let Some(path) = path else {
        return Ok(None);
    };
    let value = json_path(payload, path)?;
    let text = match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    };
    Ok((!text.trim().is_empty()).then(|| text.trim().to_owned()))
}

fn decode_json_response(response: ureq::Response) -> Result<Map<String, Value>> {
    let mut reader = response.into_reader().take(MAX_RESPONSE_BYTES + 1);
    let mut bytes = Vec::new();
    reader
        .read_to_end(&mut bytes)
        .map_err(|error| HelperError::input("read HTTP energy response", &error))?;
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX_RESPONSE_BYTES {
        return Err(HelperError::Input(format!(
            "HTTP energy response exceeds {MAX_RESPONSE_BYTES} bytes"
        )));
    }
    match serde_json::from_slice::<Value>(&bytes)? {
        Value::Object(payload) => Ok(payload),
        _ => Err(HelperError::Input(
            "energy connector response must be a JSON object".to_owned(),
        )),
    }
}

fn http_error(error: ureq::Error) -> HelperError {
    match error {
        ureq::Error::Status(code, _) => {
            HelperError::Input(format!("HTTP energy request returned status {code}"))
        }
        ureq::Error::Transport(error) => {
            HelperError::Input(format!("HTTP energy request failed: {error}"))
        }
    }
}

fn digest_authorization(
    challenge: &str,
    method: &str,
    url: &str,
    username: &str,
    password: &str,
) -> Result<String> {
    let challenge = challenge.trim();
    let fields = challenge
        .strip_prefix("Digest ")
        .or_else(|| challenge.strip_prefix("digest "))
        .ok_or_else(|| {
            HelperError::Input("unsupported HTTP authentication challenge".to_owned())
        })?;
    let values = digest_fields(fields);
    let realm = required_digest_field(&values, "realm")?;
    let nonce = required_digest_field(&values, "nonce")?;
    let algorithm = values.get("algorithm").map_or("MD5", String::as_str);
    if !algorithm.eq_ignore_ascii_case("MD5") {
        return Err(HelperError::Input(format!(
            "unsupported HTTP digest algorithm {algorithm:?}"
        )));
    }
    let request_target = request_uri(url)?;
    let qop = values.get("qop").and_then(|raw| {
        raw.split(',')
            .map(str::trim)
            .find(|value| value.eq_ignore_ascii_case("auth"))
    });
    let seed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| HelperError::Runtime(format!("system clock precedes epoch: {error}")))?
        .as_nanos();
    let cnonce = format!(
        "{:x}",
        md5::compute(format!("{}:{seed}", std::process::id()))
    );
    let ha1 = format!(
        "{:x}",
        md5::compute(format!("{username}:{realm}:{password}"))
    );
    let ha2 = format!("{:x}", md5::compute(format!("{method}:{request_target}")));
    let response = if qop.is_some() {
        format!(
            "{:x}",
            md5::compute(format!("{ha1}:{nonce}:00000001:{cnonce}:auth:{ha2}"))
        )
    } else {
        format!("{:x}", md5::compute(format!("{ha1}:{nonce}:{ha2}")))
    };
    let mut result = format!(
        "Digest username=\"{}\", realm=\"{}\", nonce=\"{}\", uri=\"{}\", response=\"{}\", algorithm=MD5",
        escaped_header(username),
        escaped_header(realm),
        escaped_header(nonce),
        escaped_header(&request_target),
        response
    );
    if qop.is_some() {
        result.push_str(", qop=auth, nc=00000001, cnonce=\"");
        result.push_str(&cnonce);
        result.push('"');
    }
    if let Some(opaque) = values.get("opaque") {
        result.push_str(", opaque=\"");
        result.push_str(&escaped_header(opaque));
        result.push('"');
    }
    Ok(result)
}

fn digest_fields(raw: &str) -> std::collections::BTreeMap<String, String> {
    let mut fields = std::collections::BTreeMap::new();
    let mut token = String::new();
    let mut quoted = false;
    for character in raw.chars().chain(std::iter::once(',')) {
        match character {
            '"' => {
                quoted = !quoted;
                token.push(character);
            }
            ',' if !quoted => {
                if let Some((key, value)) = token.split_once('=') {
                    fields.insert(
                        key.trim().to_ascii_lowercase(),
                        value.trim().trim_matches('"').to_owned(),
                    );
                }
                token.clear();
            }
            _ => token.push(character),
        }
    }
    fields
}

fn required_digest_field<'a>(
    fields: &'a std::collections::BTreeMap<String, String>,
    key: &str,
) -> Result<&'a str> {
    fields
        .get(key)
        .map(String::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| HelperError::Input(format!("HTTP digest challenge is missing {key}")))
}

fn request_uri(url: &str) -> Result<String> {
    let scheme = url.find("://").ok_or_else(|| {
        HelperError::Configuration("HTTP connector URL must be absolute".to_owned())
    })?;
    let rest = &url[scheme + 3..];
    Ok(rest
        .find('/')
        .map_or_else(|| "/".to_owned(), |index| rest[index..].to_owned()))
}

fn escaped_header(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

fn base64(input: &str) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let bytes = input.as_bytes();
    let mut output = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let first = chunk[0];
        let second = chunk.get(1).copied().unwrap_or(0);
        let third = chunk.get(2).copied().unwrap_or(0);
        output.push(char::from(TABLE[usize::from(first >> 2)]));
        output.push(char::from(
            TABLE[usize::from(((first & 0x03) << 4) | (second >> 4))],
        ));
        output.push(if chunk.len() > 1 {
            char::from(TABLE[usize::from(((second & 0x0f) << 2) | (third >> 6))])
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            char::from(TABLE[usize::from(third & 0x3f)])
        } else {
            '='
        });
    }
    output
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{base64, digest_fields, json_path, optional_bool, resolved_url};

    #[test]
    fn shared_url_and_json_paths_match_template_contracts() {
        assert_eq!(
            resolved_url("http://host/", "/status"),
            Ok("http://host/status".to_owned())
        );
        assert!(resolved_url("", "/status").is_err());
        assert_eq!(
            resolved_url("", "https://host/status"),
            Ok("https://host/status".to_owned())
        );
        let payload = json!({"battery": {"soc": 55}});
        let object = payload.as_object().cloned().unwrap_or_default();
        assert_eq!(json_path(&object, "battery.soc"), Ok(&json!(55)));
    }

    #[test]
    fn basic_and_digest_helpers_are_deterministic() {
        assert_eq!(base64("user:pass"), "dXNlcjpwYXNz");
        let fields = digest_fields("realm=\"test\", nonce=\"abc\", qop=\"auth\"");
        assert_eq!(fields.get("nonce").map(String::as_str), Some("abc"));
    }

    #[test]
    fn numeric_booleans_follow_the_python_binary_flag_contract() {
        let payload = json!({
            "positive": 1.5,
            "fraction": 0.5,
            "negative": -2.0,
            "positive_text": "2",
            "negative_text": "-2",
            "fraction_text": "1.5"
        });
        let object = payload.as_object().cloned().unwrap_or_default();
        assert_eq!(optional_bool(&object, Some("positive")), Ok(Some(true)));
        assert_eq!(optional_bool(&object, Some("fraction")), Ok(Some(false)));
        assert_eq!(optional_bool(&object, Some("negative")), Ok(Some(false)));
        assert_eq!(
            optional_bool(&object, Some("positive_text")),
            Ok(Some(true))
        );
        assert_eq!(
            optional_bool(&object, Some("negative_text")),
            Ok(Some(false))
        );
        assert_eq!(optional_bool(&object, Some("fraction_text")), Ok(None));
    }
}
