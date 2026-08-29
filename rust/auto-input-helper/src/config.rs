//! Typed configuration for the native Auto input helper runtime.

use std::collections::BTreeSet;
use std::path::{Component, Path, PathBuf};

use crate::energy::{
    ConnectorType, EnergyRole, EnergySourceDefinition, ExternalPollingPolicy,
    MAX_EXTERNAL_CYCLE_BUDGET_SECONDS, PvProjectionPolicy,
};
use crate::error::{HelperError, Result};
use crate::ini::{IniDefaults, read_bounded_text};

const MAX_CONFIG_BYTES: u64 = 2 * 1024 * 1024;
const DEFAULT_GATEWAY_RUN_DIR: &str = "/run/venus-evcharger";

/// Freshness, confidence, and switching policy for grid fusion.
#[derive(Clone, Debug, PartialEq)]
pub struct GridFusionConfig {
    pub enabled: bool,
    pub primary_source_id: String,
    pub backup_source_id: String,
    pub primary_max_age_seconds: f64,
    pub backup_max_age_seconds: f64,
    pub minimum_confidence: f64,
    pub failover_samples: u32,
    pub recovery_samples: u32,
    pub failover_hold_seconds: f64,
    pub mismatch_absolute_watts: f64,
    pub mismatch_relative: f64,
    pub mismatch_samples: u32,
    pub future_tolerance_seconds: f64,
}

/// Immutable settings consumed by the native helper runtime.
#[derive(Clone, Debug, PartialEq)]
pub struct HelperConfig {
    pub config_path: PathBuf,
    pub snapshot_path: PathBuf,
    pub gateway_run_dir: PathBuf,
    pub energy_inputs_path: PathBuf,
    pub energy_topology_path: PathBuf,
    pub command_dir: PathBuf,
    pub gateway_max_age_seconds: f64,
    pub gateway_error_retry_seconds: f64,
    pub pv_poll_seconds: f64,
    pub grid_poll_seconds: f64,
    pub battery_poll_seconds: f64,
    pub loop_seconds: f64,
    pub validation_poll_seconds: f64,
    pub topology_refresh_seconds: f64,
    pub grid_backup_source_id: String,
    pub grid_fusion: GridFusionConfig,
    pub gateway_energy_source: Option<EnergySourceDefinition>,
    pub energy_sources: Vec<EnergySourceDefinition>,
    pub use_combined_battery_soc: bool,
    pub energy_source_request_timeout_seconds: f64,
    pub external_polling: ExternalPollingPolicy,
    pub pv_projection: PvProjectionPolicy,
}

impl HelperConfig {
    /// Load and validate one helper configuration.
    ///
    /// # Errors
    ///
    /// Returns an error for unreadable files, invalid values, or unsafe paths.
    pub fn load(config_path: &Path, snapshot_override: Option<&Path>) -> Result<Self> {
        let text = read_bounded_text(config_path, MAX_CONFIG_BYTES, "Auto input configuration")?;
        let defaults = IniDefaults::parse(&text)?;
        let gateway_run_dir = absolute_path(
            value(&defaults, "DbusGatewayRunDir", DEFAULT_GATEWAY_RUN_DIR),
            "DbusGatewayRunDir",
        )?;
        let default_snapshot = value(
            &defaults,
            "AutoInputSnapshotPath",
            "/run/dbus-venus-evcharger-auto.json",
        );
        let snapshot_path = match snapshot_override {
            Some(path) => absolute_path_from_path(path, "snapshot path")?,
            None => absolute_path(default_snapshot, "AutoInputSnapshotPath")?,
        };
        let poll_fallback_ms = number(&defaults, "PollIntervalMs", 1_000.0)?;
        let auto_poll_ms = number(&defaults, "AutoInputPollIntervalMs", poll_fallback_ms)?;
        let pv_poll_seconds = poll_seconds(&defaults, "AutoPvPollIntervalMs", auto_poll_ms)?;
        let grid_poll_seconds = poll_seconds(&defaults, "AutoGridPollIntervalMs", auto_poll_ms)?;
        let battery_poll_seconds =
            poll_seconds(&defaults, "AutoBatteryPollIntervalMs", auto_poll_ms)?;
        let fastest = pv_poll_seconds
            .min(grid_poll_seconds)
            .min(battery_poll_seconds);
        let loop_seconds = (auto_poll_ms.max(200.0) / 1_000.0).min(fastest);
        let energy = energy_settings(&defaults, fastest)?;
        let grid_fusion = grid_fusion_config(&defaults, gateway_max_age(&defaults)?)?;
        validate_energy_references(&energy, &grid_fusion, battery_poll_seconds)?;
        let command_dir_default = gateway_run_dir.join("dbus-commands");
        let command_dir =
            optional_absolute_path(&defaults, "DbusGatewayCommandDir", &command_dir_default)?;
        Ok(Self {
            config_path: config_path.to_path_buf(),
            snapshot_path,
            energy_inputs_path: gateway_run_dir.join("energy-inputs.v4.bin"),
            energy_topology_path: gateway_run_dir.join("energy-topology.json"),
            command_dir,
            gateway_run_dir,
            gateway_max_age_seconds: gateway_max_age(&defaults)?,
            gateway_error_retry_seconds: bounded_number(
                &defaults,
                "DbusGatewayErrorRetrySeconds",
                30.0,
                1.0,
                Some(300.0),
            )?,
            pv_poll_seconds,
            grid_poll_seconds,
            battery_poll_seconds,
            loop_seconds,
            validation_poll_seconds: bounded_number(
                &defaults,
                "AutoInputValidationPollSeconds",
                30.0,
                5.0,
                None,
            )?,
            topology_refresh_seconds: bounded_number(
                &defaults,
                "EnergyTopologyRefreshSeconds",
                60.0,
                5.0,
                None,
            )?,
            grid_backup_source_id: grid_fusion.backup_source_id.clone(),
            grid_fusion,
            gateway_energy_source: energy.gateway_source,
            energy_sources: energy.external_sources,
            use_combined_battery_soc: energy.use_combined_soc,
            energy_source_request_timeout_seconds: energy.request_timeout_seconds,
            external_polling: energy.polling,
            pv_projection: energy.pv_projection,
        })
    }
}

#[derive(Debug)]
struct EnergySettings {
    gateway_source: Option<EnergySourceDefinition>,
    external_sources: Vec<EnergySourceDefinition>,
    use_combined_soc: bool,
    request_timeout_seconds: f64,
    polling: ExternalPollingPolicy,
    pv_projection: PvProjectionPolicy,
}

fn energy_settings(defaults: &IniDefaults, fastest_poll_seconds: f64) -> Result<EnergySettings> {
    let source_ids = configured_source_ids(defaults)?;
    let mut gateway_source = source_ids
        .is_empty()
        .then(|| legacy_gateway_source(defaults))
        .transpose()?;
    let mut external_sources = Vec::new();
    for source_id in source_ids {
        let definition = source_definition(defaults, &source_id)?;
        if source_id == "victron" {
            gateway_source = Some(definition);
        } else {
            if definition.connector_type.is_none() {
                return Err(HelperError::Configuration(format!(
                    "Auto energy source {source_id:?} has no supported non-DBus connector"
                )));
            }
            external_sources.push(definition);
        }
    }
    energy_runtime_settings(
        defaults,
        fastest_poll_seconds,
        gateway_source,
        external_sources,
    )
}

fn configured_source_ids(defaults: &IniDefaults) -> Result<Vec<String>> {
    let source_ids = csv_items(defaults.get("AutoEnergySources").unwrap_or(""));
    let mut unique = BTreeSet::new();
    for source_id in &source_ids {
        if !unique.insert(source_id.clone()) {
            return Err(HelperError::Configuration(
                "AutoEnergySources contains duplicate source ids".to_owned(),
            ));
        }
    }
    Ok(source_ids)
}

fn energy_runtime_settings(
    defaults: &IniDefaults,
    fastest_poll_seconds: f64,
    gateway_source: Option<EnergySourceDefinition>,
    external_sources: Vec<EnergySourceDefinition>,
) -> Result<EnergySettings> {
    let policy = value(defaults, "AutoPvSourcePolicy", "gateway_preferred")
        .trim()
        .to_ascii_lowercase()
        .replace('-', "_");
    if !matches!(
        policy.as_str(),
        "gateway_only" | "gateway_preferred" | "external_preferred" | "external_only"
    ) {
        return Err(HelperError::Configuration(format!(
            "unsupported AutoPvSourcePolicy {policy:?}"
        )));
    }
    let backoff_base = bounded_number(
        defaults,
        "ExternalEnergySourceBackoffBaseSeconds",
        5.0,
        0.1,
        None,
    )?;
    let backoff_max = bounded_number(
        defaults,
        "ExternalEnergySourceBackoffMaxSeconds",
        60.0,
        0.1,
        None,
    )?;
    if backoff_max < backoff_base {
        return Err(HelperError::Configuration(
            "External energy-source backoff maximum must cover its base".to_owned(),
        ));
    }
    Ok(EnergySettings {
        gateway_source,
        external_sources,
        use_combined_soc: boolean_with_default(defaults.get("AutoUseCombinedBatterySoc"), true),
        request_timeout_seconds: bounded_number(
            defaults,
            "ExternalEnergySourceRequestTimeoutSeconds",
            2.0,
            0.1,
            None,
        )?,
        polling: ExternalPollingPolicy {
            poll_interval_seconds: bounded_number(
                defaults,
                "ExternalEnergySourcePollIntervalSeconds",
                fastest_poll_seconds,
                0.2,
                None,
            )?,
            backoff_base_seconds: backoff_base,
            backoff_max_seconds: backoff_max,
            last_good_max_age_seconds: bounded_number(
                defaults,
                "ExternalEnergySourceLastGoodMaxAgeSeconds",
                30.0,
                0.0,
                None,
            )?,
            cycle_budget_seconds: bounded_number(
                defaults,
                "ExternalEnergySourceCycleBudgetSeconds",
                2.0,
                0.05,
                Some(MAX_EXTERNAL_CYCLE_BUDGET_SECONDS),
            )?,
        },
        pv_projection: PvProjectionPolicy {
            name: policy,
            external_source_id: value(defaults, "AutoPvExternalSource", "")
                .trim()
                .to_owned(),
        },
    })
}

fn legacy_gateway_source(defaults: &IniDefaults) -> Result<EnergySourceDefinition> {
    Ok(EnergySourceDefinition {
        source_id: "primary_battery".to_owned(),
        profile_name: "semantic-gateway-battery".to_owned(),
        role: EnergyRole::Battery,
        connector_type: None,
        config_path: String::new(),
        service_name: String::new(),
        usable_capacity_wh: positive_optional(defaults.get("AutoBatteryCapacityWh")),
        battery_chemistry: value(defaults, "AutoBatteryChemistry", "lfp").to_ascii_lowercase(),
        capacity_auto_estimate: boolean_with_default(
            defaults.get("AutoBatteryCapacityAutoEstimate"),
            true,
        ),
        capacity_estimate_min_soc: number(defaults, "AutoBatteryCapacityEstimateMinSoc", 95.0)?
            .max(0.0),
        capacity_startup_recheck_seconds: number(
            defaults,
            "AutoBatteryCapacityStartupRecheckSeconds",
            300.0,
        )?
        .max(0.0),
        estimated_capacity_wh: positive_optional(defaults.get("AutoBatteryCapacityEstimatedWh")),
        estimated_capacity_ah: positive_optional(defaults.get("AutoBatteryCapacityEstimatedAh")),
        estimated_capacity_nominal_voltage_v: positive_optional(
            defaults.get("AutoBatteryCapacityEstimatedNominalVoltage"),
        ),
        estimated_capacity_cell_count: positive_integer(
            defaults.get("AutoBatteryCapacityEstimatedCellCount"),
        ),
        physical_id: String::new(),
        physical_priority: 0,
    })
}

fn source_definition(defaults: &IniDefaults, source_id: &str) -> Result<EnergySourceDefinition> {
    let prefix = format!("AutoEnergySource.{source_id}.");
    let profile_raw = source_value(defaults, &prefix, "Profile").to_ascii_lowercase();
    let profile = profile_defaults(&profile_raw);
    let role_raw = source_value(defaults, &prefix, "Role");
    let role = EnergyRole::parse_or_battery(if role_raw.is_empty() {
        profile.role
    } else {
        &role_raw
    });
    let connector_raw = source_value(defaults, &prefix, "Type");
    let connector_type = ConnectorType::parse(if connector_raw.is_empty() {
        profile.connector
    } else {
        &connector_raw
    });
    let global_chemistry = value(defaults, "AutoBatteryChemistry", profile.chemistry);
    let chemistry = source_value(defaults, &prefix, "Chemistry");
    let auto_estimate_global = boolean_with_default(
        defaults.get("AutoBatteryCapacityAutoEstimate"),
        profile.auto_estimate,
    );
    Ok(EnergySourceDefinition {
        source_id: source_id.to_owned(),
        profile_name: profile.canonical.to_owned(),
        role,
        connector_type,
        config_path: source_value(defaults, &prefix, "ConfigPath"),
        service_name: source_value(defaults, &prefix, "Service"),
        usable_capacity_wh: positive_optional(defaults.get(&format!("{prefix}UsableCapacityWh"))),
        battery_chemistry: if chemistry.is_empty() {
            global_chemistry.to_ascii_lowercase()
        } else {
            chemistry.to_ascii_lowercase()
        },
        capacity_auto_estimate: boolean_with_default(
            defaults.get(&format!("{prefix}CapacityAutoEstimate")),
            auto_estimate_global,
        ),
        capacity_estimate_min_soc: source_number(
            defaults,
            &prefix,
            "CapacityEstimateMinSoc",
            number(
                defaults,
                "AutoBatteryCapacityEstimateMinSoc",
                profile.minimum_soc,
            )?,
        )?
        .max(0.0),
        capacity_startup_recheck_seconds: source_number(
            defaults,
            &prefix,
            "CapacityStartupRecheckSeconds",
            number(
                defaults,
                "AutoBatteryCapacityStartupRecheckSeconds",
                profile.recheck_seconds,
            )?,
        )?
        .max(0.0),
        estimated_capacity_wh: positive_optional(
            defaults.get(&format!("{prefix}CapacityEstimatedWh")),
        ),
        estimated_capacity_ah: positive_optional(
            defaults.get(&format!("{prefix}CapacityEstimatedAh")),
        ),
        estimated_capacity_nominal_voltage_v: positive_optional(
            defaults.get(&format!("{prefix}CapacityEstimatedNominalVoltage")),
        ),
        estimated_capacity_cell_count: positive_integer(
            defaults.get(&format!("{prefix}CapacityEstimatedCellCount")),
        ),
        physical_id: source_value(defaults, &prefix, "PhysicalId"),
        physical_priority: integer_with_default(
            defaults.get(&format!("{prefix}PhysicalPriority")),
            0,
        )?,
    })
}

struct ProfileDefaults<'a> {
    canonical: &'a str,
    role: &'a str,
    connector: &'a str,
    chemistry: &'a str,
    auto_estimate: bool,
    minimum_soc: f64,
    recheck_seconds: f64,
}

fn profile_defaults(profile: &str) -> ProfileDefaults<'_> {
    let canonical = profile_alias(profile);
    let (role, connector) = match canonical {
        "template-http-hybrid" => ("hybrid-inverter", "template_http"),
        "modbus-hybrid" => ("hybrid-inverter", "modbus"),
        "command-json-hybrid" => ("hybrid-inverter", "command_json"),
        "opendtu-pvinverter" => ("inverter", "opendtu_http"),
        name if is_known_huawei_profile(name) => ("hybrid-inverter", "modbus"),
        _ => ("battery", ""),
    };
    ProfileDefaults {
        canonical,
        role,
        connector,
        chemistry: "lfp",
        auto_estimate: true,
        minimum_soc: 95.0,
        recheck_seconds: 300.0,
    }
}

fn is_known_huawei_profile(profile: &str) -> bool {
    const PROFILES: &[&str] = &[
        "huawei_ma_native_ap",
        "huawei_ma_native_lan",
        "huawei_ma_sdongle",
        "huawei_ma_smartlogger_modbus_tcp",
        "huawei_mb_native_ap",
        "huawei_mb_native_lan",
        "huawei_mb_sdongle",
        "huawei_mb_smartlogger_modbus_tcp",
        "huawei_mb_unit1",
        "huawei_mb_unit2",
        "huawei_smartlogger_modbus_tcp",
        "huawei_l1_native_ap",
        "huawei_l1_native_lan",
        "huawei_l1_sdongle",
        "huawei_l1_smartlogger_modbus_tcp",
        "huawei_lc0_native_ap",
        "huawei_lc0_native_lan",
        "huawei_lc0_sdongle",
        "huawei_lc0_smartlogger_modbus_tcp",
        "huawei_lb0_native_ap",
        "huawei_lb0_native_lan",
        "huawei_lb0_sdongle",
        "huawei_lb0_smartlogger_modbus_tcp",
        "huawei_m1_native_ap",
        "huawei_m1_native_lan",
        "huawei_m1_sdongle",
        "huawei_m1_smartlogger_modbus_tcp",
        "huawei_map0_native_ap",
        "huawei_map0_native_lan",
        "huawei_map0_sdongle",
        "huawei_map0_smartlogger_modbus_tcp",
        "huawei_map0_unit1",
        "huawei_map0_unit2",
        "huawei_mb0_native_ap",
        "huawei_mb0_native_lan",
        "huawei_mb0_sdongle",
        "huawei_mb0_smartlogger_modbus_tcp",
        "huawei_mb0_unit1",
        "huawei_mb0_unit2",
    ];
    PROFILES.contains(&profile)
}

fn profile_alias(profile: &str) -> &str {
    match profile {
        "template-http" | "http-hybrid" => "template-http-hybrid",
        "modbus" => "modbus-hybrid",
        "command-json" | "helper" => "command-json-hybrid",
        "opendtu" | "opendtu-inverter" | "growatt-opendtu" => "opendtu-pvinverter",
        "huawei_sun5000_lb0_native_ap" => "huawei_lb0_native_ap",
        "huawei_sun5000_lb0_native_lan" => "huawei_lb0_native_lan",
        "huawei_sun5000_lb0_sdongle" => "huawei_lb0_sdongle",
        "huawei_sun5000_lb0_smartlogger_modbus_tcp" => "huawei_lb0_smartlogger_modbus_tcp",
        "huawei_sun5000_map0_native_ap" => "huawei_map0_native_ap",
        "huawei_sun5000_map0_native_lan" => "huawei_map0_native_lan",
        "huawei_sun5000_map0_sdongle" => "huawei_map0_sdongle",
        "huawei_sun5000_map0_smartlogger_modbus_tcp" => "huawei_map0_smartlogger_modbus_tcp",
        "huawei_sun5000_map0_unit1" => "huawei_map0_unit1",
        "huawei_sun5000_map0_unit2" => "huawei_map0_unit2",
        value => value,
    }
}

fn grid_fusion_config(defaults: &IniDefaults, gateway_max_age: f64) -> Result<GridFusionConfig> {
    let enabled = boolean(defaults.get("AutoGridFusionEnabled"));
    let backup_max_age = if enabled {
        number(defaults, "AutoGridFusionBackupMaxAgeSeconds", 6.0)?
    } else {
        gateway_max_age
    };
    let config = GridFusionConfig {
        enabled,
        primary_source_id: value(defaults, "AutoGridFusionPrimarySource", "")
            .trim()
            .to_owned(),
        backup_source_id: value(defaults, "AutoGridFusionBackupSource", "victron")
            .trim()
            .to_owned(),
        primary_max_age_seconds: number(defaults, "AutoGridFusionPrimaryMaxAgeSeconds", 15.0)?,
        backup_max_age_seconds: backup_max_age,
        minimum_confidence: number(defaults, "AutoGridFusionMinimumConfidence", 0.5)?,
        failover_samples: positive_u32(defaults, "AutoGridFusionFailoverSamples", 3)?,
        recovery_samples: positive_u32(defaults, "AutoGridFusionRecoverySamples", 15)?,
        failover_hold_seconds: number(defaults, "AutoGridFusionFailoverHoldSeconds", 6.0)?,
        mismatch_absolute_watts: number(defaults, "AutoGridFusionMismatchAbsoluteWatts", 300.0)?,
        mismatch_relative: number(defaults, "AutoGridFusionMismatchRelative", 0.15)?,
        mismatch_samples: positive_u32(defaults, "AutoGridFusionMismatchSamples", 3)?,
        future_tolerance_seconds: number(defaults, "AutoGridFusionFutureToleranceSeconds", 1.0)?,
    };
    if config.enabled && config.primary_source_id.is_empty() {
        return Err(HelperError::Configuration(
            "Grid fusion requires a primary source id".to_owned(),
        ));
    }
    if config.backup_source_id.is_empty() {
        return Err(HelperError::Configuration(
            "Grid fusion requires a backup source id".to_owned(),
        ));
    }
    if config.primary_max_age_seconds < 0.0
        || config.backup_max_age_seconds < 0.0
        || config.failover_hold_seconds < 0.0
        || config.future_tolerance_seconds < 0.0
        || config.mismatch_absolute_watts < 0.0
        || config.mismatch_relative < 0.0
        || !(0.0..=1.0).contains(&config.minimum_confidence)
    {
        return Err(HelperError::Configuration(
            "Grid fusion policy contains an out-of-range value".to_owned(),
        ));
    }
    Ok(config)
}

fn validate_energy_references(
    energy: &EnergySettings,
    fusion: &GridFusionConfig,
    battery_poll_seconds: f64,
) -> Result<()> {
    let external_ids: BTreeSet<&str> = energy
        .external_sources
        .iter()
        .map(|source| source.source_id.as_str())
        .collect();
    if fusion.enabled && !external_ids.contains(fusion.primary_source_id.as_str()) {
        return Err(HelperError::Configuration(format!(
            "AutoGridFusionPrimarySource {:?} is not present in external AutoEnergySources",
            fusion.primary_source_id
        )));
    }
    if fusion.enabled && fusion.primary_max_age_seconds < battery_poll_seconds {
        return Err(HelperError::Configuration(
            "AutoGridFusionPrimaryMaxAgeSeconds must cover AutoBatteryPollIntervalMs".to_owned(),
        ));
    }
    if !energy.pv_projection.external_source_id.is_empty()
        && !external_ids.contains(energy.pv_projection.external_source_id.as_str())
    {
        return Err(HelperError::Configuration(format!(
            "AutoPvExternalSource {:?} is not present in external AutoEnergySources",
            energy.pv_projection.external_source_id
        )));
    }
    if matches!(
        energy.pv_projection.name.as_str(),
        "external_preferred" | "external_only"
    ) && external_ids.is_empty()
    {
        return Err(HelperError::Configuration(format!(
            "AutoPvSourcePolicy {:?} requires an external energy source",
            energy.pv_projection.name
        )));
    }
    Ok(())
}

fn gateway_max_age(defaults: &IniDefaults) -> Result<f64> {
    bounded_number(defaults, "DbusGatewayMaxAgeSeconds", 10.0, 0.0, None)
}

fn csv_items(raw: &str) -> Vec<String> {
    raw.split(',')
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .map(str::to_owned)
        .collect()
}

fn boolean(raw: Option<&str>) -> bool {
    boolean_with_default(raw, false)
}

fn boolean_with_default(raw: Option<&str>, fallback: bool) -> bool {
    raw.map_or(fallback, |value| {
        matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        )
    })
}

fn source_value(defaults: &IniDefaults, prefix: &str, suffix: &str) -> String {
    defaults
        .get(&format!("{prefix}{suffix}"))
        .map_or("", str::trim)
        .to_owned()
}

fn source_number(defaults: &IniDefaults, prefix: &str, suffix: &str, fallback: f64) -> Result<f64> {
    number(defaults, &format!("{prefix}{suffix}"), fallback)
}

fn positive_optional(raw: Option<&str>) -> Option<f64> {
    let value = raw.map(str::trim).filter(|value| !value.is_empty())?;
    let Ok(parsed) = value.parse::<f64>() else {
        return None;
    };
    (parsed.is_finite() && parsed > 0.0).then_some(parsed)
}

fn positive_integer(raw: Option<&str>) -> Option<u32> {
    let value = raw.map(str::trim).filter(|value| !value.is_empty())?;
    let Ok(parsed) = value.parse::<u32>() else {
        return None;
    };
    (parsed > 0).then_some(parsed)
}

fn integer_with_default(raw: Option<&str>, fallback: i32) -> Result<i32> {
    let Some(value) = raw.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(fallback);
    };
    value
        .parse::<i32>()
        .map_err(|error| HelperError::Configuration(format!("integer value is invalid: {error}")))
}

fn positive_u32(defaults: &IniDefaults, key: &str, fallback: u32) -> Result<u32> {
    let raw = defaults.get(key).map_or("", str::trim);
    let parsed = if raw.is_empty() {
        fallback
    } else {
        raw.parse::<u32>().map_err(|error| {
            HelperError::Configuration(format!("{key} is not a positive sample count: {error}"))
        })?
    };
    if parsed == 0 {
        return Err(HelperError::Configuration(format!(
            "{key} must be a positive sample count"
        )));
    }
    Ok(parsed)
}

fn value<'a>(defaults: &'a IniDefaults, key: &str, fallback: &'a str) -> &'a str {
    defaults
        .get(key)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(fallback)
}

fn number(defaults: &IniDefaults, key: &str, fallback: f64) -> Result<f64> {
    let raw = defaults.get(key).map_or("", str::trim);
    let raw = if raw.is_empty() {
        fallback.to_string()
    } else {
        raw.to_owned()
    };
    let parsed = raw
        .parse::<f64>()
        .map_err(|error| HelperError::Configuration(format!("{key} is not numeric: {error}")))?;
    if !parsed.is_finite() {
        return Err(HelperError::Configuration(format!("{key} must be finite")));
    }
    Ok(parsed)
}

fn bounded_number(
    defaults: &IniDefaults,
    key: &str,
    fallback: f64,
    minimum: f64,
    maximum: Option<f64>,
) -> Result<f64> {
    let value = number(defaults, key, fallback)?.max(minimum);
    Ok(maximum.map_or(value, |limit| value.min(limit)))
}

fn poll_seconds(defaults: &IniDefaults, key: &str, fallback_ms: f64) -> Result<f64> {
    Ok(number(defaults, key, fallback_ms)?.max(200.0) / 1_000.0)
}

fn optional_absolute_path(defaults: &IniDefaults, key: &str, fallback: &Path) -> Result<PathBuf> {
    defaults
        .get(key)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map_or_else(
            || Ok(fallback.to_path_buf()),
            |configured| absolute_path(configured, key),
        )
}

fn absolute_path(raw: &str, label: &str) -> Result<PathBuf> {
    absolute_path_from_path(Path::new(raw), label)
}

fn absolute_path_from_path(path: &Path, label: &str) -> Result<PathBuf> {
    if !path.is_absolute() {
        return Err(HelperError::Configuration(format!(
            "{label} must be absolute"
        )));
    }
    let mut normalized = PathBuf::from("/");
    for component in path.components() {
        match component {
            Component::RootDir | Component::CurDir => {}
            Component::Normal(value) => normalized.push(value),
            Component::ParentDir => {
                normalized.pop();
            }
            Component::Prefix(_) => {
                return Err(HelperError::Configuration(format!(
                    "{label} has an unsupported path prefix"
                )));
            }
        }
    }
    Ok(normalized)
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::HelperConfig;

    fn config(
        text: &str,
    ) -> Result<(tempfile::TempDir, std::path::PathBuf), Box<dyn std::error::Error>> {
        let directory = tempdir()?;
        let path = directory.path().join("config.ini");
        fs::write(&path, format!("[DEFAULT]\n{text}"))?;
        Ok((directory, path))
    }

    #[test]
    fn native_defaults_match_the_python_runtime_contract() -> Result<(), Box<dyn std::error::Error>>
    {
        let (_directory, path) = config(
            "DbusGatewayRunDir=/run/test-gateway\nAutoInputSnapshotPath=/run/test-auto.json\nAutoInputPollIntervalMs=2000\nAutoBatteryPollIntervalMs=10000\nAutoEnergySources=\n",
        )?;
        let loaded = HelperConfig::load(&path, None)?;
        assert!((loaded.loop_seconds - 2.0).abs() < f64::EPSILON);
        assert!((loaded.battery_poll_seconds - 10.0).abs() < f64::EPSILON);
        assert_eq!(
            loaded.energy_inputs_path,
            std::path::Path::new("/run/test-gateway/energy-inputs.v4.bin")
        );
        let Some(gateway) = loaded.gateway_energy_source else {
            return Err("legacy semantic gateway source is missing".into());
        };
        assert_eq!(gateway.source_id, "primary_battery");
        assert!(gateway.connector_type.is_none());
        Ok(())
    }

    #[test]
    fn legacy_capacity_options_form_one_transport_neutral_gateway_source()
    -> Result<(), Box<dyn std::error::Error>> {
        let (_directory, path) = config(
            "AutoBatteryService=com.victronenergy.battery.private\nAutoBatteryCapacityWh=5120\nAutoBatteryCapacityEstimatedWh=4800\nAutoBatteryCapacityEstimatedAh=100\nAutoBatteryCapacityEstimatedNominalVoltage=48\nAutoBatteryCapacityEstimatedCellCount=15\n",
        )?;
        let loaded = HelperConfig::load(&path, None)?;
        let Some(source) = loaded.gateway_energy_source else {
            return Err("legacy semantic gateway source is missing".into());
        };
        assert_eq!(source.usable_capacity_wh, Some(5_120.0));
        assert_eq!(source.estimated_capacity_wh, Some(4_800.0));
        assert_eq!(source.estimated_capacity_ah, Some(100.0));
        assert_eq!(source.estimated_capacity_nominal_voltage_v, Some(48.0));
        assert_eq!(source.estimated_capacity_cell_count, Some(15));
        assert!(source.service_name.is_empty());
        assert!(source.config_path.is_empty());
        Ok(())
    }

    #[test]
    fn external_connectors_are_normalized_for_native_execution()
    -> Result<(), Box<dyn std::error::Error>> {
        let (_directory, path) = config(
            "AutoEnergySources=victron,huawei\nAutoEnergySource.huawei.Profile=huawei_ma_native_ap\nAutoEnergySource.huawei.ConfigPath=/data/huawei.ini\nAutoPvSourcePolicy=external-preferred\n",
        )?;
        let loaded = HelperConfig::load(&path, None)?;
        assert_eq!(loaded.energy_sources.len(), 1);
        assert_eq!(
            loaded.energy_sources[0]
                .connector_type
                .map(crate::energy::ConnectorType::as_str),
            Some("modbus")
        );
        Ok(())
    }

    #[test]
    fn unknown_huawei_profile_does_not_invent_a_modbus_connector()
    -> Result<(), Box<dyn std::error::Error>> {
        let (_directory, path) = config(
            "AutoEnergySources=huawei\nAutoEnergySource.huawei.Profile=huawei_typo_native_ap\nAutoEnergySource.huawei.ConfigPath=/data/huawei.ini\n",
        )?;
        assert!(HelperConfig::load(&path, None).is_err());

        let (_directory, path) = config(
            "AutoEnergySources=huawei\nAutoEnergySource.huawei.Profile=huawei_typo_native_ap\nAutoEnergySource.huawei.Type=modbus\nAutoEnergySource.huawei.ConfigPath=/data/huawei.ini\n",
        )?;
        assert_eq!(
            HelperConfig::load(&path, None)?.energy_sources[0]
                .connector_type
                .map(crate::energy::ConnectorType::as_str),
            Some("modbus")
        );
        Ok(())
    }

    #[test]
    fn invalid_external_policy_without_sources_fails_closed()
    -> Result<(), Box<dyn std::error::Error>> {
        let (_directory, path) = config("AutoEnergySources=\nAutoPvSourcePolicy=external_only\n")?;
        assert!(HelperConfig::load(&path, None).is_err());
        Ok(())
    }

    #[test]
    fn external_cycle_budget_is_hard_capped_for_threadless_runtime()
    -> Result<(), Box<dyn std::error::Error>> {
        let (_directory, path) = config("ExternalEnergySourceCycleBudgetSeconds=120\n")?;
        let loaded = HelperConfig::load(&path, None)?;
        assert!((loaded.external_polling.cycle_budget_seconds - 3.0).abs() < f64::EPSILON);
        Ok(())
    }

    #[test]
    fn explicit_pv_source_must_name_an_external_connector() -> Result<(), Box<dyn std::error::Error>>
    {
        let (_directory, path) = config(
            "AutoEnergySources=victron\nAutoPvSourcePolicy=gateway_only\nAutoPvExternalSource=victron\n",
        )?;
        assert!(HelperConfig::load(&path, None).is_err());
        Ok(())
    }
}
