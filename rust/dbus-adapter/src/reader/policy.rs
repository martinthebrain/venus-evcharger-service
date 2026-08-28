// SPDX-License-Identifier: GPL-3.0-or-later
//! Typed scheduling and source-selection policy for semantic energy reads.

use std::collections::HashMap;
use std::time::Duration;

use super::ReadKey;
use crate::config::IniConfig;

#[derive(Clone, Debug)]
pub(super) struct ReadPolicy {
    pub(super) grid_service: String,
    pub(super) grid_paths: Vec<String>,
    pub(super) pv_service: String,
    pub(super) pv_prefix: String,
    pub(super) pv_path: String,
    pub(super) dc_service: String,
    pub(super) dc_path: String,
    pub(super) use_dc: bool,
    pub(super) aggregate_service: String,
    pub(super) aggregate_paths: Vec<String>,
    pub(super) battery_service: String,
    pub(super) battery_prefix: String,
    pub(super) battery_soc_path: String,
    pub(super) battery_power_service: String,
    pub(super) battery_power_path: String,
    pub(super) capacity_wh_path: String,
    pub(super) capacity_ah_path: String,
    pub(super) voltage_path: String,
    pub(super) max_pv_services: usize,
    pub(super) service_interval: Duration,
    pub(super) missing_pv_service_interval: Duration,
    core_read_max_age: Duration,
    maximum_tick: Duration,
    pub(super) intervals: HashMap<ReadKey, Duration>,
}

impl ReadPolicy {
    pub(super) fn from_config(config: &IniConfig) -> Self {
        let mut intervals = HashMap::new();
        intervals.insert(
            ReadKey::Grid,
            interval(config, "DbusGatewayGridReadIntervalSeconds", 2.0),
        );
        intervals.insert(
            ReadKey::Pv,
            interval(config, "DbusGatewayPvReadIntervalSeconds", 2.0),
        );
        intervals.insert(
            ReadKey::BatterySoc,
            interval(config, "DbusGatewayBatterySocReadIntervalSeconds", 3.0),
        );
        intervals.insert(
            ReadKey::BatteryPower,
            interval(config, "DbusGatewayBatteryPowerReadIntervalSeconds", 10.0),
        );
        let metadata_interval =
            Duration::from_secs_f64(config.f64("AutoBatteryScanIntervalSeconds", 300.0).max(5.0));
        let capacity_wh_path = config.text("AutoBatteryCapacityWhPath", "");
        let capacity_ah_path = config.text("AutoBatteryCapacityAhPath", "/InstalledCapacity");
        let voltage_path = config.text("AutoBatteryVoltagePath", "/Dc/0/Voltage");
        if !capacity_wh_path.is_empty() {
            intervals.insert(ReadKey::BatteryCapacityWh, metadata_interval);
        }
        if !capacity_ah_path.is_empty() {
            intervals.insert(ReadKey::BatteryCapacityAh, metadata_interval);
        }
        if !voltage_path.is_empty() {
            intervals.insert(ReadKey::BatteryVoltage, metadata_interval);
        }
        let service_interval = Duration::from_secs_f64(
            config
                .f64("DbusGatewayServiceListIntervalSeconds", 900.0)
                .max(15.0),
        );
        let missing_pv_service_interval = Duration::from_secs_f64(
            config
                .f64("DbusGatewayMissingPvDiscoveryIntervalSeconds", 60.0)
                .max(15.0),
        )
        .min(service_interval);
        Self {
            grid_service: config.text("AutoGridService", "com.victronenergy.system"),
            grid_paths: ["AutoGridL1Path", "AutoGridL2Path", "AutoGridL3Path"]
                .into_iter()
                .zip([
                    "/Ac/Grid/L1/Power",
                    "/Ac/Grid/L2/Power",
                    "/Ac/Grid/L3/Power",
                ])
                .map(|(key, fallback)| config.text(key, fallback))
                .filter(|path| !path.is_empty())
                .collect(),
            pv_service: config.text("AutoPvService", ""),
            pv_prefix: config.text("AutoPvServicePrefix", "com.victronenergy.pvinverter"),
            pv_path: config.text("AutoPvPath", "/Ac/Power"),
            dc_service: config.text("AutoDcPvService", "com.victronenergy.system"),
            dc_path: config.text("AutoDcPvPath", "/Dc/Pv/Power"),
            use_dc: config.bool("AutoUseDcPv", true),
            aggregate_service: config.text("DbusGatewayPvAggregateService", ""),
            aggregate_paths: comma_paths(&config.text("DbusGatewayPvAggregatePaths", "")),
            battery_service: clean_example(config.text("AutoBatteryService", "")),
            battery_prefix: config.text("AutoBatteryServicePrefix", "com.victronenergy.battery"),
            battery_soc_path: config.text("AutoBatterySocPath", "/Dc/Battery/Soc"),
            battery_power_service: config
                .text("AutoBatteryPowerService", "com.victronenergy.system"),
            battery_power_path: config.text("AutoBatteryPowerPath", "/Dc/Battery/Power"),
            capacity_wh_path,
            capacity_ah_path,
            voltage_path,
            max_pv_services: usize::try_from(config.i64("AutoPvMaxServices", 10).max(1))
                .unwrap_or(10),
            service_interval,
            missing_pv_service_interval,
            core_read_max_age: Duration::from_secs_f64(
                config
                    .f64("DbusGatewaySloCoreReadMaxAgeSeconds", 5.0)
                    .max(0.1),
            ),
            maximum_tick: Duration::from_secs_f64(
                config.f64("DbusGatewayMaxTickSeconds", 1.0).max(0.05),
            ),
            intervals,
        }
    }

    pub(super) fn success_delay(
        &self,
        key: ReadKey,
        interval_factor: f64,
        operation_count: usize,
    ) -> Duration {
        let base = self.intervals[&key];
        let requested = base.mul_f64(interval_factor.max(1.0));
        if !matches!(key, ReadKey::Grid | ReadKey::Pv | ReadKey::BatterySoc) {
            return requested;
        }
        let operations = u32::try_from(operation_count.max(1)).unwrap_or(u32::MAX);
        let reserve = self.maximum_tick.saturating_mul(operations);
        let freshness_delay = self.core_read_max_age.saturating_sub(reserve).max(base);
        requested.min(freshness_delay)
    }
}

fn interval(config: &IniConfig, key: &str, fallback: f64) -> Duration {
    Duration::from_secs_f64(config.f64(key, fallback).max(0.2))
}

fn comma_paths(value: &str) -> Vec<String> {
    value
        .split(',')
        .map(str::trim)
        .filter(|path| path.starts_with('/'))
        .map(str::to_owned)
        .collect()
}

fn clean_example(value: String) -> String {
    if value.ends_with(".example") {
        String::new()
    } else {
        value
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{ReadKey, ReadPolicy, clean_example};
    use crate::config::IniConfig;

    #[test]
    fn configured_read_cadences_and_example_cleanup_are_preserved() -> Result<(), String> {
        let config = IniConfig::parse(
            "[DEFAULT]\nDbusGatewayBatterySocReadIntervalSeconds=3\nAutoBatteryService=x.example\n",
        )?;
        let policy = ReadPolicy::from_config(&config);
        assert_eq!(policy.battery_service, "");
        assert_eq!(
            policy.intervals[&ReadKey::BatterySoc],
            Duration::from_secs(3)
        );
        assert_eq!(clean_example("service.example".to_owned()), "");
        assert_eq!(policy.missing_pv_service_interval, Duration::from_secs(60));
        Ok(())
    }

    #[test]
    fn missing_pv_discovery_interval_is_bounded_by_normal_discovery() -> Result<(), String> {
        let config = IniConfig::parse(
            "[DEFAULT]\nDbusGatewayServiceListIntervalSeconds=30\nDbusGatewayMissingPvDiscoveryIntervalSeconds=120\n",
        )?;
        let policy = ReadPolicy::from_config(&config);
        assert_eq!(policy.service_interval, Duration::from_secs(30));
        assert_eq!(policy.missing_pv_service_interval, Duration::from_secs(30));
        Ok(())
    }

    #[test]
    fn aggregate_override_replaces_discovered_members() -> Result<(), String> {
        let config = IniConfig::parse(
            "[DEFAULT]\nDbusGatewayPvAggregateService=com.victronenergy.system\nDbusGatewayPvAggregatePaths=/Ac/PvOnGrid/L1/Power,/Ac/PvOnGrid/L2/Power\n",
        )?;
        let policy = ReadPolicy::from_config(&config);
        assert_eq!(policy.aggregate_paths.len(), 2);
        Ok(())
    }

    #[test]
    fn slow_core_sources_remain_inside_the_freshness_budget() -> Result<(), String> {
        let config = IniConfig::parse(
            "[DEFAULT]\nDbusGatewayPvReadIntervalSeconds=2\nDbusGatewaySloCoreReadMaxAgeSeconds=5\nDbusGatewayMaxTickSeconds=1\n",
        )?;
        let policy = ReadPolicy::from_config(&config);
        assert_eq!(
            policy.success_delay(ReadKey::Pv, 3.0, 1),
            Duration::from_secs(4)
        );
        assert_eq!(
            policy.success_delay(ReadKey::Pv, 5.0, 3),
            Duration::from_secs(2)
        );
        assert_eq!(
            policy.success_delay(ReadKey::BatteryPower, 3.0, 1),
            Duration::from_secs(30)
        );
        Ok(())
    }
}
