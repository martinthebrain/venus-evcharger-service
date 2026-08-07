# SPDX-License-Identifier: GPL-3.0-or-later
"""Own every real VeDbusService instance created by the gateway adapter."""

from __future__ import annotations

import configparser
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeGuard

from vedbus import VeDbusService

from venus_evcharger.dbus_adapter.contracts import CommandOutcome, DbusServiceLike
from venus_evcharger.dbus_adapter.publication.identity import companion_concrete_identity
from venus_evcharger.dbus_adapter.publication.schema import (
    COMPANION_PUBLICATION_SPECS,
    EVCS_PUBLICATION_SPECS,
    PublicationPathSpec,
    validate_fields,
)
from venus_evcharger.dbus_gateway import DbusCacheStore, dbus_path_key, evcs_path_freshness_kind, venus_control_route
from venus_evcharger.dbus_gateway_core import _json_ready
from venus_evcharger.ipc.command_mailbox import CommandMailbox
from venus_evcharger.ipc.core_commands import core_control_command_payload
from venus_evcharger.ipc.gateway_publication import (
    PublishCompanionFields,
    PublishEvcsFields,
    RegisterCompanionPublication,
    RegisterEvcsPublication,
)
from venus_evcharger.ports.gateway_publication import (
    CompanionServiceIdentity,
    CompanionServiceKind,
    EvcsServiceIdentity,
)

EVCS_SERVICE_ID = "evcs"
MAX_UPDATE_INDEX = 255


@dataclass(frozen=True, slots=True)
class PublicationFieldObservation:
    """One semantic value with separate change, confirmation, and service heartbeat times."""

    value: object
    changed_at: float
    confirmed_at: float
    service_heartbeat_at: float
    changed_monotonic: float
    confirmed_monotonic: float
    service_heartbeat_monotonic: float

    @property
    def observed_at(self) -> float:
        """Health freshness is based on field confirmation, not value changes."""
        return self.confirmed_at

    @property
    def observed_monotonic(self) -> float:
        """Return the monotonic field-confirmation anchor."""
        return self.confirmed_monotonic


@dataclass(slots=True)
class RegisteredPublicationService:
    """One independently registered concrete DBus service."""

    service_id: str
    kind: str
    service_name: str
    device_instance: int
    service: DbusServiceLike
    values: dict[str, object]
    semantic_values: dict[str, object]
    field_changed_at: dict[str, float]
    field_confirmed_at: dict[str, float]
    publication_heartbeat_at: float
    field_changed_monotonic: dict[str, float]
    field_confirmed_monotonic: dict[str, float]
    publication_heartbeat_monotonic: float
    update_index: int = 0


@dataclass(frozen=True, slots=True)
class PublicationServicePlan:
    """Validated construction inputs for one concrete publication service."""

    service_id: str
    kind: str
    service_name: str
    device_instance: int
    identity_paths: Mapping[str, object]
    specs: Mapping[str, PublicationPathSpec]
    initial_fields: Mapping[str, object]
    gui_writes: bool


class GatewayPublicationRegistry:
    """Translate semantic publications into isolated concrete DBus services."""

    def __init__(
        self,
        config: configparser.ConfigParser,
        *,
        evcs_service_name: str,
        cache: DbusCacheStore,
        core_commands: CommandMailbox,
        timed_publish: Callable[[Callable[[], object]], object],
    ) -> None:
        self._config = config
        self._evcs_service_name = evcs_service_name
        self._cache = cache
        self._core_commands = core_commands
        self._timed_publish = timed_publish
        self._services: dict[str, RegisteredPublicationService] = {}
        self._service_names: dict[str, str] = {}
        self._device_instances: dict[int, str] = {}

    @property
    def evcs_registered(self) -> bool:
        return EVCS_SERVICE_ID in self._services

    @property
    def registered_path_count(self) -> int:
        return sum(len(record.values) for record in self._services.values())

    @property
    def service_count(self) -> int:
        return len(self._services)

    def evcs_field_observation(self, field: str) -> PublicationFieldObservation | None:
        """Return one EVCS field without exposing its concrete DBus path."""
        record = self._services.get(EVCS_SERVICE_ID)
        normalized = str(field)
        if record is None or normalized not in record.semantic_values:
            return None
        return PublicationFieldObservation(
            value=record.semantic_values[normalized],
            changed_at=record.field_changed_at[normalized],
            confirmed_at=record.field_confirmed_at[normalized],
            service_heartbeat_at=record.publication_heartbeat_at,
            changed_monotonic=record.field_changed_monotonic[normalized],
            confirmed_monotonic=record.field_confirmed_monotonic[normalized],
            service_heartbeat_monotonic=record.publication_heartbeat_monotonic,
        )

    @property
    def evcs_publication_heartbeat_at(self) -> float:
        """Return the last EVCS publication receipt, including value-identical updates."""
        record = self._services.get(EVCS_SERVICE_ID)
        return 0.0 if record is None else record.publication_heartbeat_at

    @property
    def evcs_publication_heartbeat_monotonic(self) -> float:
        """Return the monotonic EVCS publication heartbeat."""
        record = self._services.get(EVCS_SERVICE_ID)
        return 0.0 if record is None else record.publication_heartbeat_monotonic

    def register_evcs(self, publication: RegisterEvcsPublication) -> CommandOutcome:
        existing = self._services.get(EVCS_SERVICE_ID)
        if existing is not None:
            return self._publish_fields(existing, publication.initial_fields, EVCS_PUBLICATION_SPECS)
        defaults = self._config["DEFAULT"]
        device_instance = _configured_int(defaults, "DeviceInstance", 60)
        record = self._create_service(
            PublicationServicePlan(
                service_id=EVCS_SERVICE_ID,
                kind="evcs",
                service_name=self._evcs_service_name,
                device_instance=device_instance,
                identity_paths=_evcs_identity_paths(publication.identity, defaults, device_instance),
                specs=EVCS_PUBLICATION_SPECS,
                initial_fields=publication.initial_fields,
                gui_writes=True,
            )
        )
        self._cache.set_local_service_registered(True, service_name=record.service_name)
        logging.info("DBus adapter registered EVCS service %s", record.service_name)
        return "applied"

    def publish_evcs(self, publication: PublishEvcsFields) -> CommandOutcome:
        record = self._services.get(EVCS_SERVICE_ID)
        if record is None:
            return "deferred"
        return self._publish_fields(record, publication.fields, EVCS_PUBLICATION_SPECS)

    def register_companion(self, publication: RegisterCompanionPublication) -> CommandOutcome:
        identity = publication.identity
        existing = self._services.get(identity.service_id)
        if existing is not None:
            if existing.kind != identity.kind:
                raise ValueError(f"Companion {identity.service_id!r} changed kind")
            return self._publish_fields(
                existing,
                publication.initial_fields,
                COMPANION_PUBLICATION_SPECS[identity.kind],
            )
        concrete = companion_concrete_identity(self._config["DEFAULT"], identity)
        self._create_service(
            PublicationServicePlan(
                service_id=identity.service_id,
                kind=identity.kind,
                service_name=concrete.service_name,
                device_instance=concrete.device_instance,
                identity_paths=_companion_identity_paths(identity, concrete.device_instance),
                specs=COMPANION_PUBLICATION_SPECS[identity.kind],
                initial_fields=publication.initial_fields,
                gui_writes=False,
            )
        )
        logging.info("DBus adapter registered companion %s as %s", identity.service_id, concrete.service_name)
        return "applied"

    def publish_companion(self, publication: PublishCompanionFields) -> CommandOutcome:
        record = self._services.get(publication.service_id)
        if record is None:
            return "deferred"
        kind = record.kind
        if not _is_companion_kind(kind):
            raise ValueError(f"Unsupported companion kind: {record.kind}")
        specs = COMPANION_PUBLICATION_SPECS[kind]
        return self._publish_fields(record, publication.fields, specs)

    def _create_service(self, plan: PublicationServicePlan) -> RegisteredPublicationService:
        self._reserve_identity(plan.service_id, plan.service_name, plan.device_instance)
        normalized = validate_fields(plan.initial_fields, plan.specs, surface=plan.kind)
        service = VeDbusService(plan.service_name, register=False)
        values: dict[str, object] = {}
        semantic_values = _initial_semantic_values(normalized, plan.specs)
        observed_at = time.time()
        observed_monotonic = time.monotonic()
        _add_identity_paths(service, values, plan.identity_paths)
        self._add_semantic_paths(
            service,
            values,
            semantic_values,
            plan.specs,
            gui_writes=plan.gui_writes,
        )
        _add_update_index(service, values)
        self._timed_publish(service.register)
        record = RegisteredPublicationService(
            service_id=plan.service_id,
            kind=plan.kind,
            service_name=plan.service_name,
            device_instance=plan.device_instance,
            service=service,
            values=values,
            semantic_values=semantic_values,
            field_changed_at=dict.fromkeys(semantic_values, observed_at),
            field_confirmed_at=dict.fromkeys(semantic_values, observed_at),
            publication_heartbeat_at=observed_at,
            field_changed_monotonic=dict.fromkeys(
                semantic_values,
                observed_monotonic,
            ),
            field_confirmed_monotonic=dict.fromkeys(
                semantic_values,
                observed_monotonic,
            ),
            publication_heartbeat_monotonic=observed_monotonic,
        )
        self._services[plan.service_id] = record
        self._cache_record(record)
        return record

    def _add_semantic_paths(
        self,
        service: DbusServiceLike,
        values: dict[str, object],
        semantic_values: Mapping[str, object],
        specs: Mapping[str, PublicationPathSpec],
        *,
        gui_writes: bool,
    ) -> None:
        for field, spec in specs.items():
            callback = self._change_callback(spec, gui_writes=gui_writes)
            service.add_path(
                spec.path,
                semantic_values[field],
                gettextcallback=spec.formatter,
                writeable=callback is not None,
                onchangecallback=callback,
            )
            values[spec.path] = semantic_values[field]

    def _change_callback(
        self,
        spec: PublicationPathSpec,
        *,
        gui_writes: bool,
    ) -> Callable[[str, object], bool] | None:
        return self._handle_gui_write if gui_writes and spec.writeable else None

    def _reserve_identity(self, service_id: str, service_name: str, device_instance: int) -> None:
        name_owner = self._service_names.get(service_name)
        instance_owner = self._device_instances.get(device_instance)
        if name_owner is not None and name_owner != service_id:
            raise ValueError(f"DBus service-name collision between {name_owner!r} and {service_id!r}")
        if instance_owner is not None and instance_owner != service_id:
            raise ValueError(f"DBus DeviceInstance collision between {instance_owner!r} and {service_id!r}")
        self._service_names[service_name] = service_id
        self._device_instances[device_instance] = service_id

    def _publish_fields(
        self,
        record: RegisteredPublicationService,
        fields: Mapping[str, object],
        specs: Mapping[str, PublicationPathSpec],
    ) -> CommandOutcome:
        normalized = validate_fields(fields, specs, surface=record.kind)
        changed = False
        observed_at = time.time()
        observed_monotonic = time.monotonic()
        record.publication_heartbeat_at = observed_at
        record.publication_heartbeat_monotonic = observed_monotonic
        for field, value in normalized.items():
            path = specs[field].path
            if record.values.get(path) == value:
                record.field_confirmed_at[field] = observed_at
                record.field_confirmed_monotonic[field] = observed_monotonic
                continue
            self._publish_service_value(record, path, value)
            record.values[path] = value
            record.semantic_values[field] = value
            record.field_changed_at[field] = observed_at
            record.field_confirmed_at[field] = observed_at
            record.field_changed_monotonic[field] = observed_monotonic
            record.field_confirmed_monotonic[field] = observed_monotonic
            self._cache_path(record, path, value)
            changed = True
        if changed:
            self._bump_update_index(record)
        return "applied"

    def _publish_service_value(
        self,
        record: RegisteredPublicationService,
        path: str,
        value: object,
    ) -> None:
        def apply() -> None:
            record.service[path] = value

        self._timed_publish(apply)

    def _bump_update_index(self, record: RegisteredPublicationService) -> None:
        record.update_index = 0 if record.update_index >= MAX_UPDATE_INDEX else record.update_index + 1
        self._timed_publish(lambda: record.service.__setitem__("/UpdateIndex", record.update_index))
        record.values["/UpdateIndex"] = record.update_index
        self._cache_path(record, "/UpdateIndex", record.update_index)

    def _handle_gui_write(self, path: str, value: object) -> bool:
        route = venus_control_route(path)
        if route is None:
            logging.warning("Rejected GUI write without semantic route: %s", path)
            return False
        self._core_commands.enqueue(
            core_control_command_payload(
                route.name,
                route.target,
                _json_ready(value),
                source="control-surface",
                origin="gateway-gui",
            )
        )
        return False

    def _cache_record(self, record: RegisteredPublicationService) -> None:
        for path, value in record.values.items():
            self._cache_path(record, path, value)

    def _cache_path(self, record: RegisteredPublicationService, path: str, value: object) -> None:
        freshness = evcs_path_freshness_kind(path) if record.kind == "evcs" else "local_owned"
        self._cache.update_value(
            dbus_path_key(record.service_name, path),
            value,
            source=f"{record.service_name}{path}",
            freshness_kind=freshness,
        )


def _evcs_identity_paths(
    identity: EvcsServiceIdentity,
    defaults: configparser.SectionProxy,
    device_instance: int,
) -> dict[str, object]:
    return {
        "/Mgmt/ProcessName": identity.process_name,
        "/Mgmt/ProcessVersion": identity.process_version,
        "/Mgmt/Connection": identity.connection_name,
        "/DeviceInstance": device_instance,
        "/ProductId": 0xFFFF,
        "/ProductName": identity.product_name,
        "/CustomName": identity.custom_name,
        "/FirmwareVersion": identity.firmware_version,
        "/HardwareVersion": identity.hardware_version,
        "/Serial": identity.serial,
        "/Position": _configured_int(defaults, "Position", 1),
    }


def _companion_identity_paths(
    identity: CompanionServiceIdentity,
    device_instance: int,
) -> dict[str, object]:
    return {
        "/Mgmt/ProcessName": identity.process_name,
        "/Mgmt/ProcessVersion": identity.process_version,
        "/Mgmt/Connection": identity.connection_name,
        "/DeviceInstance": device_instance,
        "/ProductId": 0xFFFF,
        "/ProductName": identity.product_name,
        "/CustomName": identity.custom_name,
        "/FirmwareVersion": identity.firmware_version,
        "/HardwareVersion": identity.hardware_version,
        "/Serial": identity.serial,
    }


def _configured_int(defaults: configparser.SectionProxy, key: str, fallback: int) -> int:
    try:
        return int(str(defaults.get(key, str(fallback))).strip())
    except ValueError:
        return fallback


def _is_companion_kind(value: str) -> TypeGuard[CompanionServiceKind]:
    return value in COMPANION_PUBLICATION_SPECS


def _initial_semantic_values(
    normalized: Mapping[str, object],
    specs: Mapping[str, PublicationPathSpec],
) -> dict[str, object]:
    return {field: normalized.get(field, spec.default) for field, spec in specs.items()}


def _add_identity_paths(
    service: DbusServiceLike,
    values: dict[str, object],
    identity_paths: Mapping[str, object],
) -> None:
    for path, value in identity_paths.items():
        service.add_path(path, value)
        values[path] = value


def _add_update_index(service: DbusServiceLike, values: dict[str, object]) -> None:
    service.add_path("/UpdateIndex", 0)
    values["/UpdateIndex"] = 0


__all__ = [
    "EVCS_SERVICE_ID",
    "GatewayPublicationRegistry",
    "PublicationFieldObservation",
    "PublicationServicePlan",
    "RegisteredPublicationService",
]
