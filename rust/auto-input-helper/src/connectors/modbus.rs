//! Minimal native Modbus TCP, UDP, and serial-RTU energy connector.

use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs, UdpSocket};
use std::process::Command;
use std::thread;
use std::time::Duration;

use serialport::{DataBits, FlowControl, Parity, StopBits};

use crate::connectors::EnergyConnector;
use crate::connectors::common::{finite, load_connector_document, section_text};
use crate::energy::{EnergySourceDefinition, EnergySourceSnapshot};
use crate::error::{HelperError, Result};
use crate::ini::IniDocument;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TransportKind {
    Tcp,
    Udp,
    SerialRtu,
}

#[derive(Clone, Debug)]
struct TransportSettings {
    kind: TransportKind,
    unit_id: u8,
    timeout_seconds: f64,
    host: String,
    port: u16,
    device: String,
    baudrate: u32,
    data_bits: DataBits,
    parity: Parity,
    stop_bits: StopBits,
    owner_stop_command: Option<String>,
    owner_start_command: Option<String>,
    retry_count: u32,
    retry_delay_seconds: f64,
}

#[derive(Clone, Debug)]
struct FieldSettings {
    register_type: String,
    address: u16,
    data_type: String,
    scale: f64,
    word_order: String,
}

#[derive(Clone, Debug)]
struct FieldRead {
    name: &'static str,
    settings: FieldSettings,
}

#[derive(Clone, Debug)]
enum FieldValue {
    Number(f64),
    Text(String),
}

enum ModbusTransport {
    Tcp { transaction_id: u16 },
    Udp { transaction_id: u16 },
    Serial(SerialTransport),
}

struct SerialTransport {
    owned: bool,
    settings: TransportSettings,
}

impl Drop for SerialTransport {
    fn drop(&mut self) {
        if self.owned {
            if let Some(command) = &self.settings.owner_start_command {
                let _ = Command::new(command).arg(&self.settings.device).status();
            }
        }
    }
}

/// Cached Modbus settings and one-field-per-step progress.
pub struct ModbusConnector {
    transport_settings: TransportSettings,
    transport: ModbusTransport,
    fields: Vec<FieldRead>,
    values: BTreeMap<&'static str, FieldValue>,
    next_field_index: usize,
    operating_mode_map: BTreeMap<String, String>,
    ac_power_scope_key: String,
    pv_input_power_scope_key: String,
    grid_interaction_scope_key: String,
}

impl ModbusConnector {
    pub fn load(source: &EnergySourceDefinition, default_timeout: f64) -> Result<Self> {
        let document = load_connector_document(&source.config_path)?;
        let transport_settings = load_transport(&document, default_timeout)?;
        let field_specs = [
            ("soc", "SocRead"),
            ("usable_capacity", "UsableCapacityRead"),
            ("battery_power", "BatteryPowerRead"),
            ("charge_limit_power", "ChargeLimitPowerRead"),
            ("discharge_limit_power", "DischargeLimitPowerRead"),
            ("ac_power", "AcPowerRead"),
            ("pv_input_power", "PvInputPowerRead"),
            ("grid_interaction", "GridInteractionRead"),
            ("operating_mode", "OperatingModeRead"),
        ];
        let mut fields = Vec::new();
        for (name, section) in field_specs {
            if let Some(settings) = field_settings(&document, section)? {
                fields.push(FieldRead { name, settings });
            }
        }
        if fields.is_empty() && source.usable_capacity_wh.is_none() {
            return Err(HelperError::Configuration(format!(
                "energy source {:?} requires a Modbus read section or usable capacity",
                source.source_id
            )));
        }
        let operating_mode_map = document
            .section_entries("OperatingModeMap")
            .cloned()
            .unwrap_or_default();
        let transport = match transport_settings.kind {
            TransportKind::Tcp => ModbusTransport::Tcp { transaction_id: 0 },
            TransportKind::Udp => ModbusTransport::Udp { transaction_id: 0 },
            TransportKind::SerialRtu => ModbusTransport::Serial(SerialTransport {
                owned: false,
                settings: transport_settings.clone(),
            }),
        };
        Ok(Self {
            transport_settings,
            transport,
            fields,
            values: BTreeMap::new(),
            next_field_index: 0,
            operating_mode_map,
            ac_power_scope_key: section_text(&document, "Aggregation", "AcPowerScopeKey", ""),
            pv_input_power_scope_key: section_text(
                &document,
                "Aggregation",
                "PvInputPowerScopeKey",
                "",
            ),
            grid_interaction_scope_key: section_text(
                &document,
                "Aggregation",
                "GridInteractionScopeKey",
                "",
            ),
        })
    }

    fn read_field(&mut self, field: &FieldSettings, timeout_seconds: f64) -> Result<f64> {
        let register_type = field.register_type.as_str();
        let (function, count) = match register_type {
            "coil" => (0x01, 1),
            "discrete" => (0x02, 1),
            "holding" => (0x03, register_count(&field.data_type)),
            "input" => (0x04, register_count(&field.data_type)),
            _ => {
                return Err(HelperError::Configuration(format!(
                    "unsupported Modbus register type {register_type:?}"
                )));
            }
        };
        let payload = [
            field.address.to_be_bytes().as_slice(),
            count.to_be_bytes().as_slice(),
        ]
        .concat();
        let response = exchange(
            &mut self.transport,
            &self.transport_settings,
            function,
            &payload,
            timeout_seconds,
        )?;
        let value = if matches!(register_type, "coil" | "discrete") {
            decode_bit_response(&response)?
        } else {
            decode_register_response(&response, &field.data_type, &field.word_order, count)?
        };
        let scaled = value * field.scale;
        if !scaled.is_finite() {
            return Err(HelperError::Input(
                "Modbus energy field returned a non-finite value".to_owned(),
            ));
        }
        Ok(scaled)
    }

    fn snapshot(&self, source: &EnergySourceDefinition, observed_at: f64) -> EnergySourceSnapshot {
        let soc = self
            .number_value("soc")
            .filter(|value| (0.0..=100.0).contains(value));
        let usable_capacity_wh = match self.number_value("usable_capacity") {
            Some(value) if value > 0.0 => Some(value),
            Some(_) => None,
            None => source.usable_capacity_wh,
        };
        EnergySourceSnapshot {
            source_id: source.source_id.clone(),
            role: source.role,
            service_name: if source.service_name.is_empty() {
                if !self.transport_settings.host.is_empty() {
                    self.transport_settings.host.clone()
                } else if !self.transport_settings.device.is_empty() {
                    self.transport_settings.device.clone()
                } else {
                    source.config_path.clone()
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
            net_battery_power_w: self.number_value("battery_power"),
            charge_limit_power_w: self.number_value("charge_limit_power"),
            discharge_limit_power_w: self.number_value("discharge_limit_power"),
            ac_power_w: self.number_value("ac_power"),
            pv_input_power_w: self.number_value("pv_input_power"),
            grid_interaction_w: self.number_value("grid_interaction"),
            ac_power_scope_key: render_scope(
                &self.ac_power_scope_key,
                source,
                &self.transport_settings,
            ),
            pv_input_power_scope_key: render_scope(
                &self.pv_input_power_scope_key,
                source,
                &self.transport_settings,
            ),
            grid_interaction_scope_key: render_scope(
                &self.grid_interaction_scope_key,
                source,
                &self.transport_settings,
            ),
            operating_mode: self
                .values
                .get("operating_mode")
                .and_then(|value| match value {
                    FieldValue::Text(text) => Some(text.clone()),
                    FieldValue::Number(_) => None,
                })
                .unwrap_or_default(),
            online: true,
            confidence: 1.0,
            captured_at: Some(observed_at),
            physical_id: source.physical_id.clone(),
            physical_priority: source.physical_priority,
        }
    }

    fn number_value(&self, key: &str) -> Option<f64> {
        self.values.get(key).and_then(|value| match value {
            FieldValue::Number(number) => Some(*number),
            FieldValue::Text(_) => None,
        })
    }

    fn reset_progress(&mut self) {
        self.values.clear();
        self.next_field_index = 0;
    }
}

impl EnergyConnector for ModbusConnector {
    fn read_step(
        &mut self,
        source: &EnergySourceDefinition,
        observed_at: f64,
        timeout_seconds: f64,
    ) -> Result<Option<EnergySourceSnapshot>> {
        if self.fields.is_empty() {
            return Ok(Some(self.snapshot(source, observed_at)));
        }
        let field = self
            .fields
            .get(self.next_field_index)
            .cloned()
            .ok_or_else(|| HelperError::Runtime("Modbus read progress is invalid".to_owned()))?;
        let timeout = self
            .transport_settings
            .timeout_seconds
            .min(timeout_seconds)
            .max(0.001);
        let value = match self.read_field(&field.settings, timeout) {
            Ok(value) => value,
            Err(error) => {
                self.reset_progress();
                return Err(error);
            }
        };
        let progress_value = if field.name == "operating_mode" {
            let raw = if value.fract() == 0.0 {
                format!("{value:.0}")
            } else {
                value.to_string()
            };
            FieldValue::Text(self.operating_mode_map.get(&raw).cloned().unwrap_or(raw))
        } else {
            FieldValue::Number(value)
        };
        self.values.insert(field.name, progress_value);
        self.next_field_index = self.next_field_index.saturating_add(1);
        if self.next_field_index < self.fields.len() {
            return Ok(None);
        }
        let snapshot = self.snapshot(source, observed_at);
        self.reset_progress();
        Ok(Some(snapshot))
    }
}

fn load_transport(document: &IniDocument, default_timeout: f64) -> Result<TransportSettings> {
    let kind = transport_kind(document);
    let (host, port, serial, retry_count, retry_delay_seconds) = match kind {
        TransportKind::Tcp | TransportKind::Udp => {
            let (host, port) = network_settings(document)?;
            (host, port, default_serial_settings(), 0, 0.2)
        }
        TransportKind::SerialRtu => {
            let serial = serial_settings(document)?;
            validate_serial_device(&serial.device)?;
            (
                String::new(),
                0,
                serial,
                parse_nonnegative_u32(document.get("Transport", "RetryCount"), 1),
                finite(document.get("Transport", "RetryDelaySeconds"))
                    .unwrap_or(0.2)
                    .max(0.0),
            )
        }
    };
    let timeout_seconds = finite(document.get("Transport", "RequestTimeoutSeconds"))
        .filter(|value| *value > 0.0)
        .unwrap_or(default_timeout);
    Ok(TransportSettings {
        kind,
        unit_id: unit_id(document)?,
        timeout_seconds,
        host,
        port,
        device: serial.device,
        baudrate: serial.baudrate,
        data_bits: serial.data_bits,
        parity: serial.parity,
        stop_bits: serial.stop_bits,
        owner_stop_command: serial.owner_stop_command,
        owner_start_command: serial.owner_start_command,
        retry_count,
        retry_delay_seconds,
    })
}

struct SerialSettings {
    device: String,
    baudrate: u32,
    data_bits: DataBits,
    parity: Parity,
    stop_bits: StopBits,
    owner_stop_command: Option<String>,
    owner_start_command: Option<String>,
}

const fn default_serial_settings() -> SerialSettings {
    SerialSettings {
        device: String::new(),
        baudrate: 9_600,
        data_bits: DataBits::Eight,
        parity: Parity::None,
        stop_bits: StopBits::One,
        owner_stop_command: None,
        owner_start_command: None,
    }
}

fn transport_kind(document: &IniDocument) -> TransportKind {
    match section_text(
        document,
        "Adapter",
        "Transport",
        &section_text(document, "Transport", "Type", "tcp"),
    )
    .to_ascii_lowercase()
    .as_str()
    {
        "serial" | "rtu" | "serial_rtu" => TransportKind::SerialRtu,
        "udp" => TransportKind::Udp,
        _ => TransportKind::Tcp,
    }
}

fn unit_id(document: &IniDocument) -> Result<u8> {
    let raw = section_text(
        document,
        "Transport",
        "UnitId",
        &section_text(document, "Transport", "SlaveId", "1"),
    );
    let parsed = raw
        .parse::<u16>()
        .map_err(|error| HelperError::Configuration(format!("invalid Modbus unit id: {error}")))?;
    if parsed > 247 {
        return Err(HelperError::Configuration(
            "Modbus unit id must be between 0 and 247".to_owned(),
        ));
    }
    u8::try_from(parsed)
        .map_err(|error| HelperError::Configuration(format!("invalid Modbus unit id: {error}")))
}

fn network_settings(document: &IniDocument) -> Result<(String, u16)> {
    let host = section_text(document, "Transport", "Host", "");
    if host.is_empty() {
        return Err(HelperError::Configuration(
            "Modbus network transport requires Transport.Host".to_owned(),
        ));
    }
    let port = parse_positive_u16(
        &section_text(document, "Transport", "Port", "502"),
        "Modbus port",
    )?;
    Ok((host, port))
}

fn serial_settings(document: &IniDocument) -> Result<SerialSettings> {
    let owner = section_text(document, "Transport", "PortOwner", "none").to_ascii_lowercase();
    let owner_enabled = matches!(
        owner.as_str(),
        "venus" | "venus_serial_starter" | "serial-starter" | "victron"
    );
    let baudrate = section_text(document, "Transport", "Baudrate", "9600")
        .parse::<u32>()
        .map_err(|error| HelperError::Configuration(format!("invalid baudrate: {error}")))?;
    if baudrate == 0 {
        return Err(HelperError::Configuration(
            "Modbus baudrate must be positive".to_owned(),
        ));
    }
    Ok(SerialSettings {
        device: section_text(document, "Transport", "Device", ""),
        baudrate,
        data_bits: data_bits(document)?,
        parity: parity(document)?,
        stop_bits: stop_bits(document)?,
        owner_stop_command: owner_enabled.then(|| {
            section_text(
                document,
                "Transport",
                "PortOwnerStopCommand",
                "/opt/victronenergy/serial-starter/stop-tty.sh",
            )
        }),
        owner_start_command: owner_enabled.then(|| {
            section_text(
                document,
                "Transport",
                "PortOwnerStartCommand",
                "/opt/victronenergy/serial-starter/start-tty.sh",
            )
        }),
    })
}

fn data_bits(document: &IniDocument) -> Result<DataBits> {
    match section_text(document, "Transport", "Bytesize", "8").as_str() {
        "5" => Ok(DataBits::Five),
        "6" => Ok(DataBits::Six),
        "7" => Ok(DataBits::Seven),
        "8" => Ok(DataBits::Eight),
        value => Err(HelperError::Configuration(format!(
            "unsupported Modbus bytesize {value:?}"
        ))),
    }
}

fn parity(document: &IniDocument) -> Result<Parity> {
    match section_text(document, "Transport", "Parity", "N")
        .to_ascii_uppercase()
        .as_str()
    {
        "N" => Ok(Parity::None),
        "E" => Ok(Parity::Even),
        "O" => Ok(Parity::Odd),
        value => Err(HelperError::Configuration(format!(
            "unsupported Modbus parity {value:?}"
        ))),
    }
}

fn stop_bits(document: &IniDocument) -> Result<StopBits> {
    match section_text(document, "Transport", "StopBits", "1").as_str() {
        "1" => Ok(StopBits::One),
        "2" => Ok(StopBits::Two),
        value => Err(HelperError::Configuration(format!(
            "unsupported Modbus stop bits {value:?}"
        ))),
    }
}

fn validate_serial_device(device: &str) -> Result<()> {
    if device.is_empty() {
        return Err(HelperError::Configuration(
            "Modbus serial_rtu transport requires Transport.Device".to_owned(),
        ));
    }
    Ok(())
}

fn field_settings(document: &IniDocument, section: &str) -> Result<Option<FieldSettings>> {
    if !document.has_section(section) {
        return Ok(None);
    }
    let address = section_text(document, section, "Address", "");
    if address.is_empty() {
        return Ok(None);
    }
    let address = parse_u16(&address, "Modbus register address")?;
    let scale_text = document
        .get(section, "Scale")
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("1");
    let scale = scale_text.parse::<f64>().map_err(|error| {
        HelperError::Configuration(format!("invalid Modbus field scale: {error}"))
    })?;
    if !scale.is_finite() {
        return Err(HelperError::Configuration(
            "Modbus field scale must be finite".to_owned(),
        ));
    }
    let data_type = section_text(document, section, "DataType", "uint16").to_ascii_lowercase();
    if !matches!(
        data_type.as_str(),
        "bool" | "uint16" | "int16" | "uint32" | "int32" | "float32"
    ) {
        return Err(HelperError::Configuration(format!(
            "unsupported Modbus data type {data_type:?}"
        )));
    }
    let word_order = section_text(document, section, "WordOrder", "big").to_ascii_lowercase();
    if !matches!(word_order.as_str(), "big" | "little") {
        return Err(HelperError::Configuration(format!(
            "unsupported Modbus word order {word_order:?}"
        )));
    }
    Ok(Some(FieldSettings {
        register_type: section_text(document, section, "RegisterType", "holding")
            .to_ascii_lowercase(),
        address,
        data_type,
        scale,
        word_order,
    }))
}

fn exchange(
    transport: &mut ModbusTransport,
    settings: &TransportSettings,
    function: u8,
    payload: &[u8],
    timeout_seconds: f64,
) -> Result<Vec<u8>> {
    match transport {
        ModbusTransport::Tcp { transaction_id } => {
            *transaction_id = transaction_id.wrapping_add(1);
            exchange_tcp(
                settings,
                *transaction_id,
                function,
                payload,
                timeout_seconds,
            )
        }
        ModbusTransport::Udp { transaction_id } => {
            *transaction_id = transaction_id.wrapping_add(1);
            exchange_udp(
                settings,
                *transaction_id,
                function,
                payload,
                timeout_seconds,
            )
        }
        ModbusTransport::Serial(serial) => {
            exchange_serial(serial, function, payload, timeout_seconds)
        }
    }
}

fn request_frame(
    settings: &TransportSettings,
    transaction: u16,
    function: u8,
    payload: &[u8],
) -> Vec<u8> {
    let pdu_length = payload.len().saturating_add(2);
    let length = u16::try_from(pdu_length).unwrap_or(u16::MAX);
    [
        transaction.to_be_bytes().as_slice(),
        [0_u8, 0_u8].as_slice(),
        length.to_be_bytes().as_slice(),
        [settings.unit_id, function].as_slice(),
        payload,
    ]
    .concat()
}

fn exchange_tcp(
    settings: &TransportSettings,
    transaction: u16,
    function: u8,
    payload: &[u8],
    timeout_seconds: f64,
) -> Result<Vec<u8>> {
    let timeout = Duration::from_secs_f64(timeout_seconds);
    let address = first_address(&settings.host, settings.port)?;
    let mut socket = TcpStream::connect_timeout(&address, timeout)
        .map_err(|error| HelperError::input("connect Modbus TCP", &error))?;
    socket
        .set_read_timeout(Some(timeout))
        .and_then(|()| socket.set_write_timeout(Some(timeout)))
        .map_err(|error| HelperError::input("configure Modbus TCP timeout", &error))?;
    socket
        .write_all(&request_frame(settings, transaction, function, payload))
        .map_err(|error| HelperError::input("write Modbus TCP request", &error))?;
    let mut header = [0_u8; 7];
    socket
        .read_exact(&mut header)
        .map_err(|error| HelperError::input("read Modbus TCP header", &error))?;
    let length = usize::from(u16::from_be_bytes([header[4], header[5]]));
    if !(2..=254).contains(&length) {
        return Err(HelperError::Input(
            "Modbus TCP response has an invalid length".to_owned(),
        ));
    }
    validate_mbap(&header, transaction, settings.unit_id)?;
    let mut body = vec![0_u8; length - 1];
    socket
        .read_exact(&mut body)
        .map_err(|error| HelperError::input("read Modbus TCP response", &error))?;
    validate_pdu(body, function)
}

fn exchange_udp(
    settings: &TransportSettings,
    transaction: u16,
    function: u8,
    payload: &[u8],
    timeout_seconds: f64,
) -> Result<Vec<u8>> {
    let timeout = Duration::from_secs_f64(timeout_seconds);
    let socket = UdpSocket::bind("0.0.0.0:0")
        .map_err(|error| HelperError::input("bind Modbus UDP socket", &error))?;
    socket
        .set_read_timeout(Some(timeout))
        .and_then(|()| socket.set_write_timeout(Some(timeout)))
        .map_err(|error| HelperError::input("configure Modbus UDP timeout", &error))?;
    let address = first_address(&settings.host, settings.port)?;
    socket
        .send_to(
            &request_frame(settings, transaction, function, payload),
            address,
        )
        .map_err(|error| HelperError::input("write Modbus UDP request", &error))?;
    let mut response = [0_u8; 260];
    let (count, _) = socket
        .recv_from(&mut response)
        .map_err(|error| HelperError::input("read Modbus UDP response", &error))?;
    if count < 8 {
        return Err(HelperError::Input(
            "Modbus UDP response is incomplete".to_owned(),
        ));
    }
    validate_mbap(&response[..7], transaction, settings.unit_id)?;
    let length = usize::from(u16::from_be_bytes([response[4], response[5]]));
    if length < 2 || 6_usize.saturating_add(length) > count {
        return Err(HelperError::Input(
            "Modbus UDP response has an invalid length".to_owned(),
        ));
    }
    validate_pdu(response[7..6 + length].to_vec(), function)
}

fn exchange_serial(
    serial: &mut SerialTransport,
    function: u8,
    payload: &[u8],
    timeout_seconds: f64,
) -> Result<Vec<u8>> {
    serial.ensure_owned()?;
    let attempts = serial.settings.retry_count.saturating_add(1);
    let mut last_error = None;
    for attempt in 0..attempts {
        match exchange_serial_once(&serial.settings, function, payload, timeout_seconds) {
            Ok(response) => return Ok(response),
            Err(error) => last_error = Some(error),
        }
        if attempt + 1 < attempts {
            if let Some(command) = &serial.settings.owner_stop_command {
                run_owner_command(command, &serial.settings.device)?;
            }
            if serial.settings.retry_delay_seconds > 0.0 {
                thread::sleep(Duration::from_secs_f64(serial.settings.retry_delay_seconds));
            }
        }
    }
    Err(last_error
        .unwrap_or_else(|| HelperError::Input("Modbus serial exchange failed".to_owned())))
}

impl SerialTransport {
    fn ensure_owned(&mut self) -> Result<()> {
        if self.owned {
            return Ok(());
        }
        if let Some(command) = &self.settings.owner_stop_command {
            run_owner_command(command, &self.settings.device)?;
        }
        self.owned = true;
        Ok(())
    }
}

fn exchange_serial_once(
    settings: &TransportSettings,
    function: u8,
    payload: &[u8],
    timeout_seconds: f64,
) -> Result<Vec<u8>> {
    let timeout = Duration::from_secs_f64(timeout_seconds);
    let mut port = serialport::new(&settings.device, settings.baudrate)
        .data_bits(settings.data_bits)
        .flow_control(FlowControl::None)
        .parity(settings.parity)
        .stop_bits(settings.stop_bits)
        .timeout(timeout)
        .open()
        .map_err(|error| HelperError::Input(format!("open Modbus serial port: {error}")))?;
    port.clear(serialport::ClearBuffer::All)
        .map_err(|error| HelperError::Input(format!("clear Modbus serial port: {error}")))?;
    let mut frame = vec![settings.unit_id, function];
    frame.extend_from_slice(payload);
    let crc = modbus_crc(&frame);
    frame.extend_from_slice(&crc.to_le_bytes());
    port.write_all(&frame)
        .map_err(|error| HelperError::input("write Modbus RTU request", &error))?;
    let mut header = [0_u8; 3];
    port.read_exact(&mut header)
        .map_err(|error| HelperError::input("read Modbus RTU header", &error))?;
    let total = if header[1] & 0x80 != 0 {
        5
    } else if matches!(function, 0x01..=0x04) {
        usize::from(header[2]).saturating_add(5)
    } else {
        8
    };
    let mut response = header.to_vec();
    let mut remainder = vec![0_u8; total.saturating_sub(3)];
    port.read_exact(&mut remainder)
        .map_err(|error| HelperError::input("read Modbus RTU response", &error))?;
    response.extend_from_slice(&remainder);
    if response.first().copied() != Some(settings.unit_id) {
        return Err(HelperError::Input(
            "Modbus RTU response uses the wrong unit id".to_owned(),
        ));
    }
    let payload_end = response.len().saturating_sub(2);
    let expected = modbus_crc(&response[..payload_end]);
    let received = u16::from_le_bytes([response[payload_end], response[payload_end + 1]]);
    if expected != received {
        return Err(HelperError::Input(
            "Modbus RTU response has an invalid CRC".to_owned(),
        ));
    }
    validate_pdu(response[1..payload_end].to_vec(), function)
}

fn validate_mbap(header: &[u8], transaction: u16, unit_id: u8) -> Result<()> {
    if header.len() != 7
        || u16::from_be_bytes([header[0], header[1]]) != transaction
        || header[2] != 0
        || header[3] != 0
        || header[6] != unit_id
    {
        return Err(HelperError::Input(
            "Modbus response has an invalid MBAP header".to_owned(),
        ));
    }
    Ok(())
}

fn validate_pdu(response: Vec<u8>, expected_function: u8) -> Result<Vec<u8>> {
    let Some(function) = response.first().copied() else {
        return Err(HelperError::Input("empty Modbus response".to_owned()));
    };
    if function == expected_function | 0x80 {
        let code = response.get(1).copied().unwrap_or(0xff);
        return Err(HelperError::Input(format!(
            "Modbus device returned exception 0x{code:02x}"
        )));
    }
    if function != expected_function {
        return Err(HelperError::Input(
            "Modbus response uses an unexpected function".to_owned(),
        ));
    }
    Ok(response)
}

fn decode_bit_response(response: &[u8]) -> Result<f64> {
    if response.len() != 3 || response[1] != 1 {
        return Err(HelperError::Input(
            "Modbus bit response is incomplete".to_owned(),
        ));
    }
    Ok(if response[2] & 1 != 0 { 1.0 } else { 0.0 })
}

fn decode_register_response(
    response: &[u8],
    data_type: &str,
    word_order: &str,
    count: u16,
) -> Result<f64> {
    let expected_bytes = usize::from(count) * 2;
    if response.len() != expected_bytes + 2 || usize::from(response[1]) != expected_bytes {
        return Err(HelperError::Input(
            "Modbus register response is incomplete".to_owned(),
        ));
    }
    let mut words: Vec<u16> = response[2..]
        .chunks_exact(2)
        .map(|word| u16::from_be_bytes([word[0], word[1]]))
        .collect();
    if word_order == "little" && words.len() > 1 {
        words.reverse();
    }
    let value = match data_type {
        "bool" => f64::from(u8::from(words[0] != 0)),
        "uint16" => f64::from(words[0]),
        "int16" => f64::from(i16::from_be_bytes(words[0].to_be_bytes())),
        "uint32" => f64::from(u32::from_be_bytes(words_bytes(&words)?)),
        "int32" => f64::from(i32::from_be_bytes(words_bytes(&words)?)),
        "float32" => f64::from(f32::from_be_bytes(words_bytes(&words)?)),
        _ => {
            return Err(HelperError::Configuration(format!(
                "unsupported Modbus data type {data_type:?}"
            )));
        }
    };
    Ok(value)
}

fn words_bytes(words: &[u16]) -> Result<[u8; 4]> {
    if words.len() != 2 {
        return Err(HelperError::Input(
            "Modbus 32-bit value requires two registers".to_owned(),
        ));
    }
    let first = words[0].to_be_bytes();
    let second = words[1].to_be_bytes();
    Ok([first[0], first[1], second[0], second[1]])
}

fn register_count(data_type: &str) -> u16 {
    if matches!(data_type, "uint32" | "int32" | "float32") {
        2
    } else {
        1
    }
}

fn first_address(host: &str, port: u16) -> Result<std::net::SocketAddr> {
    (host, port)
        .to_socket_addrs()
        .map_err(|error| HelperError::input("resolve Modbus host", &error))?
        .next()
        .ok_or_else(|| HelperError::Input("Modbus host has no address".to_owned()))
}

fn run_owner_command(command: &str, device: &str) -> Result<()> {
    let status = Command::new(command)
        .arg(device)
        .status()
        .map_err(|error| HelperError::input("run Venus serial ownership helper", &error))?;
    if status.success() {
        return Ok(());
    }
    Err(HelperError::Input(
        "Venus serial ownership helper failed".to_owned(),
    ))
}

fn modbus_crc(frame: &[u8]) -> u16 {
    let mut crc = 0xffff_u16;
    for byte in frame {
        crc ^= u16::from(*byte);
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xa001
            } else {
                crc >> 1
            };
        }
    }
    crc
}

fn render_scope(
    template: &str,
    source: &EnergySourceDefinition,
    settings: &TransportSettings,
) -> String {
    if template.trim().is_empty() {
        return String::new();
    }
    let (host_value, port_value, device_value) = match settings.kind {
        TransportKind::Tcp | TransportKind::Udp => {
            (settings.host.as_str(), settings.port.to_string(), "None")
        }
        TransportKind::SerialRtu => ("None", "None".to_owned(), settings.device.as_str()),
    };
    template
        .replace("{source_id}", &source.source_id)
        .replace("{host}", host_value)
        .replace("{port}", &port_value)
        .replace("{unit_id}", &settings.unit_id.to_string())
        .replace("{device}", device_value)
}

fn parse_u16(raw: &str, label: &str) -> Result<u16> {
    let parsed = raw
        .parse::<u32>()
        .map_err(|error| HelperError::Configuration(format!("invalid {label}: {error}")))?;
    if parsed > u32::from(u16::MAX) {
        return Err(HelperError::Configuration(format!(
            "{label} is outside the supported range"
        )));
    }
    u16::try_from(parsed)
        .map_err(|error| HelperError::Configuration(format!("invalid {label}: {error}")))
}

fn parse_positive_u16(raw: &str, label: &str) -> Result<u16> {
    let value = parse_u16(raw, label)?;
    if value == 0 {
        return Err(HelperError::Configuration(format!(
            "{label} must be positive"
        )));
    }
    Ok(value)
}

fn parse_nonnegative_u32(raw: Option<&str>, fallback: u32) -> u32 {
    raw.and_then(|value| value.trim().parse::<i64>().ok())
        .map_or(fallback, |value| {
            u32::try_from(value.max(0)).unwrap_or(u32::MAX)
        })
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    use tempfile::tempdir;

    use super::{ModbusConnector, decode_register_response, modbus_crc};
    use crate::connectors::EnergyConnector;
    use crate::energy::{ConnectorType, EnergyRole, EnergySourceDefinition};

    fn source(config_path: String) -> EnergySourceDefinition {
        EnergySourceDefinition {
            source_id: "hybrid".to_owned(),
            profile_name: "modbus-hybrid".to_owned(),
            role: EnergyRole::HybridInverter,
            connector_type: Some(ConnectorType::Modbus),
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

    fn serve_registers(
        listener: &TcpListener,
        replies: Vec<(u16, u16, Vec<u8>)>,
    ) -> std::io::Result<()> {
        for (expected_address, expected_count, data) in replies {
            let (mut stream, _) = listener.accept()?;
            let mut request = [0_u8; 12];
            stream.read_exact(&mut request)?;
            assert_eq!(request[2..4], [0, 0]);
            assert_eq!(request[6], 7);
            assert_eq!(request[7], 3);
            assert_eq!(
                u16::from_be_bytes([request[8], request[9]]),
                expected_address
            );
            assert_eq!(
                u16::from_be_bytes([request[10], request[11]]),
                expected_count
            );
            let mut pdu = vec![3, u8::try_from(data.len()).map_err(std::io::Error::other)?];
            pdu.extend_from_slice(&data);
            let length = u16::try_from(pdu.len() + 1).map_err(std::io::Error::other)?;
            let response = [
                request[..2].to_vec(),
                vec![0, 0],
                length.to_be_bytes().to_vec(),
                vec![7],
                pdu,
            ]
            .concat();
            stream.write_all(&response)?;
        }
        Ok(())
    }

    #[test]
    fn decodes_modbus_words_and_crc() {
        assert_eq!(
            decode_register_response(&[3, 4, 0x41, 0x48, 0, 0], "float32", "big", 2),
            Ok(12.5)
        );
        assert_eq!(modbus_crc(&[1, 3, 0, 0, 0, 1]), 0x0a84);
    }

    #[test]
    fn modbus_tcp_reads_one_field_per_step_and_builds_the_contract_snapshot()
    -> Result<(), Box<dyn std::error::Error>> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let address = listener.local_addr()?;
        let server = thread::spawn(move || {
            serve_registers(
                &listener,
                vec![
                    (10, 1, 805_u16.to_be_bytes().to_vec()),
                    (11, 1, (-1_200_i16).to_be_bytes().to_vec()),
                    (12, 2, (-450_i32).to_be_bytes().to_vec()),
                    (14, 1, 2_u16.to_be_bytes().to_vec()),
                ],
            )
        });
        let directory = tempdir()?;
        let config_path = directory.path().join("connector.ini");
        fs::write(
            &config_path,
            format!(
                "[Transport]\nType=tcp\nHost={}\nPort={}\nUnitId=7\nBaudrate=not-used\nParity=not-used\n[SocRead]\nAddress=10\nDataType=uint16\nScale=0.1\n[BatteryPowerRead]\nAddress=11\nDataType=int16\n[GridInteractionRead]\nAddress=12\nDataType=int32\n[OperatingModeRead]\nAddress=14\nDataType=uint16\n[OperatingModeMap]\n2=hybrid\n[Aggregation]\nGridInteractionScopeKey={{host}}:{{port}}:{{unit_id}}:{{device}}\n",
                address.ip(),
                address.port()
            ),
        )?;
        let definition = source(config_path.to_string_lossy().into_owned());
        let mut connector = ModbusConnector::load(&definition, 1.0)?;
        assert!(connector.read_step(&definition, 100.0, 1.0)?.is_none());
        assert!(connector.read_step(&definition, 100.0, 1.0)?.is_none());
        assert!(connector.read_step(&definition, 100.0, 1.0)?.is_none());
        let snapshot = connector
            .read_step(&definition, 100.0, 1.0)?
            .ok_or("Modbus connector did not complete")?;
        server.join().map_err(|_| "Modbus test server failed")??;

        assert_eq!(snapshot.soc, Some(80.5));
        assert_eq!(snapshot.net_battery_power_w, Some(-1_200.0));
        assert_eq!(snapshot.grid_interaction_w, Some(-450.0));
        assert_eq!(snapshot.operating_mode, "hybrid");
        assert_eq!(
            snapshot.grid_interaction_scope_key,
            format!("{}:{}:7:None", address.ip(), address.port())
        );
        assert_eq!(snapshot.service_name, address.ip().to_string());
        assert_eq!(snapshot.captured_at, Some(100.0));
        Ok(())
    }

    #[test]
    fn transport_specific_options_and_numeric_invariants_are_validated()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempdir()?;
        let network_path = directory.path().join("network.ini");
        fs::write(
            &network_path,
            "[Transport]\nType=tcp\nHost=127.0.0.1\nPort=502\nBaudrate=invalid-but-irrelevant\nParity=X\n[SocRead]\nAddress=1\n",
        )?;
        assert!(
            ModbusConnector::load(&source(network_path.to_string_lossy().into_owned()), 1.0)
                .is_ok()
        );

        let serial_path = directory.path().join("serial.ini");
        fs::write(
            &serial_path,
            "[Transport]\nType=serial_rtu\nDevice=/dev/null\nPort=invalid-but-irrelevant\n[SocRead]\nAddress=1\n",
        )?;
        assert!(
            ModbusConnector::load(&source(serial_path.to_string_lossy().into_owned()), 1.0).is_ok()
        );

        fs::write(
            &serial_path,
            "[Transport]\nType=serial_rtu\nDevice=/dev/null\nBaudrate=0\n[SocRead]\nAddress=1\n",
        )?;
        assert!(
            ModbusConnector::load(&source(serial_path.to_string_lossy().into_owned()), 1.0)
                .is_err()
        );

        fs::write(
            &network_path,
            "[Transport]\nType=tcp\nHost=127.0.0.1\n[SocRead]\nAddress=1\nScale=invalid\n",
        )?;
        assert!(
            ModbusConnector::load(&source(network_path.to_string_lossy().into_owned()), 1.0)
                .is_err()
        );
        Ok(())
    }
}
