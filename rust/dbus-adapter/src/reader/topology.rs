// SPDX-License-Identifier: GPL-3.0-or-later
//! Semantic source resolution and topology descriptions.

use std::collections::BTreeSet;

use super::{EnergyReader, ReadKey, ReadMember};
use crate::energy::{EnergySource, opaque_source_id};

impl EnergyReader {
    pub(super) fn members(&self, key: ReadKey) -> Vec<ReadMember> {
        match key {
            ReadKey::Grid => self
                .policy
                .grid_paths
                .iter()
                .map(|path| ReadMember {
                    service: self.policy.grid_service.clone(),
                    path: path.clone(),
                    source_id: "grid-primary".to_owned(),
                })
                .collect(),
            ReadKey::Pv => self.pv_members(),
            ReadKey::BatteryPower => vec![ReadMember {
                service: self.policy.battery_power_service.clone(),
                path: self.policy.battery_power_path.clone(),
                source_id: self.battery_source_id(),
            }],
            ReadKey::BatterySoc
            | ReadKey::BatteryCapacityWh
            | ReadKey::BatteryCapacityAh
            | ReadKey::BatteryVoltage => {
                let Some(service) = self.selected_battery_service() else {
                    return Vec::new();
                };
                let path = match key {
                    ReadKey::BatterySoc => &self.policy.battery_soc_path,
                    ReadKey::BatteryCapacityWh => &self.policy.capacity_wh_path,
                    ReadKey::BatteryCapacityAh => &self.policy.capacity_ah_path,
                    ReadKey::BatteryVoltage => &self.policy.voltage_path,
                    ReadKey::Grid | ReadKey::Pv | ReadKey::BatteryPower => unreachable!(),
                };
                vec![ReadMember {
                    source_id: opaque_source_id("battery", &service),
                    service,
                    path: path.clone(),
                }]
            }
        }
    }

    fn pv_members(&self) -> Vec<ReadMember> {
        let monotonic_at = crate::energy::Clocks::now().map_or(0.0, |clocks| clocks.monotonic);
        self.pv_candidate_members()
            .into_iter()
            .filter(|member| {
                self.pv_dormancy
                    .probe_allowed(&member.source_id, monotonic_at)
            })
            .collect()
    }

    pub(super) fn pv_candidate_members(&self) -> Vec<ReadMember> {
        if !self.policy.aggregate_service.is_empty()
            && !self.policy.aggregate_paths.is_empty()
            && self.service_is_advertised(&self.policy.aggregate_service)
        {
            return self
                .policy
                .aggregate_paths
                .iter()
                .map(|path| ReadMember {
                    service: self.policy.aggregate_service.clone(),
                    path: path.clone(),
                    source_id: opaque_source_id("pv-ac", &self.policy.aggregate_service),
                })
                .collect();
        }
        let mut members = self
            .pv_ac_services()
            .into_iter()
            .map(|service| ReadMember {
                source_id: opaque_source_id("pv-ac", &service),
                service,
                path: self.policy.pv_path.clone(),
            })
            .collect::<Vec<_>>();
        if self.policy.use_dc
            && !self.policy.dc_service.is_empty()
            && !self.policy.dc_path.is_empty()
            && self.service_is_advertised(&self.policy.dc_service)
        {
            members.push(ReadMember {
                source_id: opaque_source_id("pv-dc", &self.policy.dc_service),
                service: self.policy.dc_service.clone(),
                path: self.policy.dc_path.clone(),
            });
        }
        members
    }

    pub(super) fn pv_descriptor_members(&self) -> Vec<ReadMember> {
        let mut members = self
            .validated_pv
            .values()
            .cloned()
            .map(|member| (member.source_id.clone(), member))
            .collect::<std::collections::BTreeMap<_, _>>();
        for member in self.pv_candidate_members() {
            members.insert(member.source_id.clone(), member);
        }
        members.into_values().collect()
    }

    fn pv_ac_services(&self) -> Vec<String> {
        if !self.policy.pv_service.is_empty() {
            return self
                .service_is_advertised(&self.policy.pv_service)
                .then(|| self.policy.pv_service.clone())
                .into_iter()
                .collect();
        }
        self.services
            .iter()
            .filter(|service| service.starts_with(&self.policy.pv_prefix))
            .take(self.policy.max_pv_services)
            .cloned()
            .collect()
    }

    fn service_is_advertised(&self, service: &str) -> bool {
        self.services.iter().any(|name| name == service)
    }

    fn selected_battery_service(&self) -> Option<String> {
        if !self.policy.battery_service.is_empty() {
            return Some(self.policy.battery_service.clone());
        }
        self.services
            .iter()
            .find(|service| service.starts_with(&self.policy.battery_prefix))
            .cloned()
    }

    fn battery_source_id(&self) -> String {
        self.selected_battery_service().map_or_else(
            || "battery-unresolved".to_owned(),
            |service| opaque_source_id("battery", &service),
        )
    }

    pub(super) fn source_ids(&self, key: ReadKey) -> Vec<String> {
        let members = if key == ReadKey::Pv {
            self.pv_descriptor_members()
        } else {
            self.members(key)
        };
        members
            .into_iter()
            .map(|member| member.source_id)
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }

    pub(super) fn source_descriptors(&self) -> Vec<EnergySource> {
        let advertised = |service: &str| {
            if !self.service_discovery_completed {
                "unknown"
            } else if self.services.iter().any(|name| name == service) {
                "online"
            } else {
                "offline"
            }
        };
        let mut sources = vec![EnergySource {
            source_id: "grid-primary".to_owned(),
            kind: "grid".to_owned(),
            state: advertised(&self.policy.grid_service).to_owned(),
            capabilities: vec!["power".to_owned()],
        }];
        for member in self
            .pv_descriptor_members()
            .into_iter()
            .filter(|member| member.source_id.starts_with("pv-ac-"))
        {
            sources.push(EnergySource {
                source_id: member.source_id,
                kind: "pv_ac".to_owned(),
                state: advertised(&member.service).to_owned(),
                capabilities: vec!["power".to_owned()],
            });
        }
        for member in self
            .pv_descriptor_members()
            .into_iter()
            .filter(|member| member.source_id.starts_with("pv-dc-"))
        {
            sources.push(EnergySource {
                source_id: member.source_id,
                kind: "pv_dc".to_owned(),
                state: advertised(&member.service).to_owned(),
                capabilities: vec!["power".to_owned()],
            });
        }
        if let Some(service) = self.selected_battery_service() {
            let mut capabilities = vec!["soc".to_owned(), "net_power".to_owned()];
            if !self.policy.capacity_wh_path.is_empty() {
                capabilities.push("capacity_wh".to_owned());
            }
            if !self.policy.capacity_ah_path.is_empty() {
                capabilities.push("capacity_ah".to_owned());
            }
            if !self.policy.voltage_path.is_empty() {
                capabilities.push("voltage".to_owned());
            }
            sources.push(EnergySource {
                source_id: opaque_source_id("battery", &service),
                kind: "battery".to_owned(),
                state: advertised(&service).to_owned(),
                capabilities,
            });
        }
        sources
    }
}
