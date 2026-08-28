// SPDX-License-Identifier: GPL-3.0-or-later
//! Semantic publication registry backed by native Victron D-Bus services.

use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use serde_json::{Map, Value};

use crate::config::IniConfig;
use crate::dbus::{BusValue, PublishedService, TextFormat, WriteHandler};
use crate::energy::Clocks;
use crate::mailbox::Mailbox;

const MAX_UPDATE_INDEX: i32 = 255;
const SESSION_ACTIVE_POWER_WATTS: f64 = 50.0;
const SESSION_ACTIVE_CURRENT_AMPS: f64 = 0.2;
const GUI_MEASUREMENT_FIELDS: [&str; 9] = [
    "ac_power_w",
    "ac_current_a",
    "charge_current_a",
    "l1_power_w",
    "l1_current_a",
    "l2_power_w",
    "l2_current_a",
    "l3_power_w",
    "l3_current_a",
];
const GUI_CONTROL_FIELDS: [&str; 7] = [
    "connected",
    "mode",
    "start_stop",
    "enable",
    "auto_start",
    "status",
    "set_current",
];
const GUI_SESSION_FIELDS: [&str; 4] = [
    "energy_forward_kwh",
    "session_time_s",
    "session_energy_kwh",
    "charging_time_s",
];

mod contract;
mod identity;
mod observations;

use contract::{
    PathSpec, PublicationContract, object, path_freshness, positive_u64, text, text_format,
    validate_fields,
};
use identity::{
    companion_identity, companion_identity_paths, evcs_identity_paths, identity_path_count,
};

struct RegisteredService {
    kind: String,
    service: PublishedService,
    update_index: i32,
    observations: BTreeMap<String, LocalPathObservation>,
}

#[derive(Clone, Debug)]
struct LocalPathObservation {
    value: BusValue,
    changed_at: f64,
    confirmed_at: f64,
    confirmed_monotonic: f64,
    freshness_kind: String,
}

#[derive(Clone, Debug)]
pub struct PublicationCacheValue {
    pub key: String,
    pub value: Value,
    pub source: String,
    pub changed_at: f64,
    pub confirmed_at: f64,
    pub confirmed_monotonic: f64,
    pub freshness_kind: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PublicationFieldObservation {
    pub value: Value,
    pub changed_at: f64,
    pub confirmed_at: f64,
    pub confirmed_monotonic: f64,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct GuiFreshness {
    pub maximum_age_seconds: f64,
    pub measurement_max_age_seconds: f64,
    pub control_max_age_seconds: f64,
    pub session_max_age_seconds: f64,
    pub missing_count: usize,
    pub measurement_missing_count: usize,
    pub control_missing_count: usize,
    pub session_missing_count: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PublicationOutcome {
    Applied,
    Deferred,
    Dropped,
}

pub struct PublicationRegistry {
    contract: PublicationContract,
    config: IniConfig,
    evcs_service_name: String,
    core_mailbox: Arc<Mailbox>,
    services: HashMap<String, RegisteredService>,
    service_names: HashMap<String, String>,
    device_instances: HashMap<i64, String>,
    field_orders: HashMap<(String, String), u64>,
    evcs_observations: HashMap<String, PublicationFieldObservation>,
    evcs_heartbeat_at: f64,
    evcs_heartbeat_monotonic: f64,
}

impl PublicationRegistry {
    pub fn new(
        config: IniConfig,
        evcs_service_name: String,
        core_mailbox: Arc<Mailbox>,
    ) -> Result<Self, String> {
        let contract = PublicationContract::load()?;
        Ok(Self {
            contract,
            config,
            evcs_service_name,
            core_mailbox,
            services: HashMap::new(),
            service_names: HashMap::new(),
            device_instances: HashMap::new(),
            field_orders: HashMap::new(),
            evcs_observations: HashMap::new(),
            evcs_heartbeat_at: 0.0,
            evcs_heartbeat_monotonic: 0.0,
        })
    }

    pub fn apply(&mut self, command: &Map<String, Value>) -> Result<PublicationOutcome, String> {
        match command.get("kind").and_then(Value::as_str).unwrap_or("") {
            "register_evcs" => self.register_evcs(command),
            "publish_evcs_fields" => self.publish("evcs", command),
            "register_companion" => self.register_companion(command),
            "publish_companion_fields" => {
                let Some(service_id) = command.get("service_id").and_then(Value::as_str) else {
                    return Ok(PublicationOutcome::Dropped);
                };
                self.publish(service_id, command)
            }
            _ => Ok(PublicationOutcome::Dropped),
        }
    }

    fn register_evcs(
        &mut self,
        command: &Map<String, Value>,
    ) -> Result<PublicationOutcome, String> {
        if self.services.contains_key("evcs") {
            return self.publish("evcs", command);
        }
        let Some(identity) = object(command, "identity") else {
            return Ok(PublicationOutcome::Dropped);
        };
        let fields = object(command, "fields").cloned().unwrap_or_default();
        let device_instance = self.config.i64("DeviceInstance", 60);
        let identity_paths = evcs_identity_paths(&self.config, identity, device_instance)?;
        let service_name = self.evcs_service_name.clone();
        self.create_service(
            "evcs",
            "evcs",
            &service_name,
            device_instance,
            identity_paths,
            &fields,
        )?;
        Ok(PublicationOutcome::Applied)
    }

    fn register_companion(
        &mut self,
        command: &Map<String, Value>,
    ) -> Result<PublicationOutcome, String> {
        let Some(identity) = object(command, "identity") else {
            return Ok(PublicationOutcome::Dropped);
        };
        let Some(service_id) = text(identity, "service_id") else {
            return Ok(PublicationOutcome::Dropped);
        };
        let Some(kind) = text(identity, "kind") else {
            return Ok(PublicationOutcome::Dropped);
        };
        if self.services.contains_key(service_id) {
            return self.publish(service_id, command);
        }
        if !self.contract.companion.contains_key(kind) {
            return Ok(PublicationOutcome::Dropped);
        }
        let (service_name, device_instance) = companion_identity(&self.config, kind, service_id);
        let identity_paths = companion_identity_paths(identity, device_instance)?;
        let fields = object(command, "fields").cloned().unwrap_or_default();
        self.create_service(
            service_id,
            kind,
            &service_name,
            device_instance,
            identity_paths,
            &fields,
        )?;
        Ok(PublicationOutcome::Applied)
    }

    fn create_service(
        &mut self,
        service_id: &str,
        kind: &str,
        service_name: &str,
        device_instance: i64,
        identity_paths: Vec<(String, BusValue, TextFormat, bool)>,
        fields: &Map<String, Value>,
    ) -> Result<(), String> {
        self.reserve_identity(service_id, service_name, device_instance)?;
        let specs = self
            .specs(kind)
            .ok_or_else(|| format!("unknown publication surface: {kind}"))?;
        validate_fields(fields, specs)?;
        let mut initial = identity_paths;
        for (field, spec) in specs {
            let raw = fields.get(field).unwrap_or(&spec.default);
            initial.push((
                spec.path.clone(),
                BusValue::from_json(raw)?,
                text_format(spec.formatter.as_deref())?,
                spec.writeable,
            ));
        }
        initial.push((
            "/UpdateIndex".to_owned(),
            BusValue::I32(0),
            TextFormat::Default,
            false,
        ));
        let write_handler = (kind == "evcs").then(|| self.gui_write_handler());
        let clocks = Clocks::now()?;
        let observations = initial
            .iter()
            .map(|(path, value, _, _)| {
                (
                    path.clone(),
                    LocalPathObservation {
                        value: value.clone(),
                        changed_at: clocks.epoch,
                        confirmed_at: clocks.epoch,
                        confirmed_monotonic: clocks.monotonic,
                        freshness_kind: path_freshness(kind, path, specs),
                    },
                )
            })
            .collect();
        let service = PublishedService::register(service_name, initial, write_handler.as_ref())?;
        self.services.insert(
            service_id.to_owned(),
            RegisteredService {
                kind: kind.to_owned(),
                service,
                update_index: 0,
                observations,
            },
        );
        if kind == "evcs" {
            self.record_evcs_observations(fields, clocks);
        }
        Ok(())
    }

    fn publish(
        &mut self,
        service_id: &str,
        command: &Map<String, Value>,
    ) -> Result<PublicationOutcome, String> {
        let Some(record) = self.services.get(service_id) else {
            return Ok(PublicationOutcome::Deferred);
        };
        let kind = record.kind.clone();
        let Some(fields) = object(command, "fields") else {
            return Ok(PublicationOutcome::Dropped);
        };
        let specs = self
            .specs(&kind)
            .cloned()
            .ok_or_else(|| format!("unknown publication surface: {kind}"))?;
        validate_fields(fields, &specs)?;
        let accepted = self.accepted_fields(service_id, fields, command);
        let accepted_observations = accepted
            .iter()
            .map(|field| ((*field).clone(), fields[*field].clone()))
            .collect::<Map<_, _>>();
        let updates = accepted
            .iter()
            .map(|field| {
                let spec = &specs[*field];
                Ok((
                    spec.path.clone(),
                    BusValue::from_json(&fields[*field])?,
                    spec.freshness_kind.clone(),
                ))
            })
            .collect::<Result<Vec<_>, String>>()?;
        let clocks = Clocks::now()?;
        let record = self
            .services
            .get_mut(service_id)
            .ok_or_else(|| "publication service disappeared".to_owned())?;
        let mut changed = false;
        for (path, value, freshness_kind) in updates {
            changed |= record.service.publish(&path, &value)?;
            record_local_observation(
                &mut record.observations,
                path,
                value,
                freshness_kind,
                clocks,
            );
        }
        if changed {
            record.update_index = if record.update_index >= MAX_UPDATE_INDEX {
                0
            } else {
                record.update_index + 1
            };
            record
                .service
                .publish("/UpdateIndex", &BusValue::I32(record.update_index))?;
            record_local_observation(
                &mut record.observations,
                "/UpdateIndex".to_owned(),
                BusValue::I32(record.update_index),
                "local_owned".to_owned(),
                clocks,
            );
        }
        if kind == "evcs" {
            self.record_evcs_observations(&accepted_observations, clocks);
        }
        Ok(PublicationOutcome::Applied)
    }

    fn record_evcs_observations(&mut self, fields: &Map<String, Value>, clocks: Clocks) {
        self.evcs_heartbeat_at = clocks.epoch;
        self.evcs_heartbeat_monotonic = clocks.monotonic;
        for (field, value) in fields {
            let previous = self.evcs_observations.get(field);
            let changed_at = previous.map_or(clocks.epoch, |item| {
                if item.value == *value {
                    item.changed_at
                } else {
                    clocks.epoch
                }
            });
            self.evcs_observations.insert(
                field.clone(),
                PublicationFieldObservation {
                    value: value.clone(),
                    changed_at,
                    confirmed_at: clocks.epoch,
                    confirmed_monotonic: clocks.monotonic,
                },
            );
        }
    }

    fn accepted_fields<'a>(
        &mut self,
        service_id: &str,
        fields: &'a Map<String, Value>,
        command: &Map<String, Value>,
    ) -> Vec<&'a String> {
        let default_order = positive_u64(command.get("transport_order"));
        let field_orders = command
            .get("transport_field_orders")
            .and_then(Value::as_object);
        fields
            .keys()
            .filter(|field| {
                let order = field_orders
                    .and_then(|orders| positive_u64(orders.get(*field)))
                    .or(default_order);
                let Some(order) = order else {
                    return true;
                };
                let key = (service_id.to_owned(), (*field).clone());
                let previous = self.field_orders.get(&key).copied().unwrap_or(0);
                if order < previous {
                    return false;
                }
                self.field_orders.insert(key, order);
                true
            })
            .collect()
    }

    fn specs(&self, kind: &str) -> Option<&BTreeMap<String, PathSpec>> {
        if kind == "evcs" {
            Some(&self.contract.evcs)
        } else {
            self.contract.companion.get(kind)
        }
    }

    fn gui_write_handler(&self) -> WriteHandler {
        let routes = self
            .contract
            .evcs
            .values()
            .filter_map(|spec| spec.route.clone().map(|route| (spec.path.clone(), route)))
            .collect::<HashMap<_, _>>();
        let mailbox = self.core_mailbox.clone();
        Arc::new(move |path, value| {
            if let Some(route) = routes.get(path) {
                let _ignored =
                    mailbox.enqueue_core_control(&route.name, &route.target, &value.to_json());
            }
            false
        })
    }

    fn reserve_identity(
        &mut self,
        service_id: &str,
        service_name: &str,
        device_instance: i64,
    ) -> Result<(), String> {
        if self
            .service_names
            .get(service_name)
            .is_some_and(|owner| owner != service_id)
        {
            return Err(format!("D-Bus service-name collision: {service_name}"));
        }
        if self
            .device_instances
            .get(&device_instance)
            .is_some_and(|owner| owner != service_id)
        {
            return Err(format!("D-Bus DeviceInstance collision: {device_instance}"));
        }
        self.service_names
            .insert(service_name.to_owned(), service_id.to_owned());
        self.device_instances
            .insert(device_instance, service_id.to_owned());
        Ok(())
    }
}

fn record_local_observation(
    observations: &mut BTreeMap<String, LocalPathObservation>,
    path: String,
    value: BusValue,
    freshness_kind: String,
    clocks: Clocks,
) {
    let changed_at = observations.get(&path).map_or(clocks.epoch, |previous| {
        if previous.value == value {
            previous.changed_at
        } else {
            clocks.epoch
        }
    });
    observations.insert(
        path,
        LocalPathObservation {
            value,
            changed_at,
            confirmed_at: clocks.epoch,
            confirmed_monotonic: clocks.monotonic,
            freshness_kind,
        },
    );
}
