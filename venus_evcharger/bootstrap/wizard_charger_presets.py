# SPDX-License-Identifier: GPL-3.0-or-later
"""Device-specific charger presets for the setup wizard."""

from __future__ import annotations

from typing import Literal

from venus_evcharger.bootstrap.wizard_models import WizardChargerBackend, WizardTransportKind

WizardChargerPreset = Literal[
    "abb-terra-ac-modbus",
    "cfos-power-brain-modbus",
    "openwb-modbus-secondary",
]

CHARGER_PRESET_LABELS: tuple[tuple[WizardChargerPreset, str], ...] = (
    ("abb-terra-ac-modbus", "ABB Terra AC over Modbus"),
    ("cfos-power-brain-modbus", "cFos Power Brain over Modbus"),
    ("openwb-modbus-secondary", "openWB secondary over Modbus"),
)
CHARGER_PRESET_VALUES = tuple(item[0] for item in CHARGER_PRESET_LABELS)

_PRESET_BACKENDS: dict[WizardChargerPreset, WizardChargerBackend] = {
    "abb-terra-ac-modbus": "modbus_charger",
    "cfos-power-brain-modbus": "modbus_charger",
    "openwb-modbus-secondary": "modbus_charger",
}
_PRESET_TCP_PORTS: dict[WizardChargerPreset, int] = {
    "abb-terra-ac-modbus": 502,
    "cfos-power-brain-modbus": 4701,
    "openwb-modbus-secondary": 1502,
}
_PRESET_UNIT_IDS: dict[WizardChargerPreset, int] = {
    "abb-terra-ac-modbus": 1,
    "cfos-power-brain-modbus": 1,
    "openwb-modbus-secondary": 1,
}


def charger_preset_backend(charger_preset: str | None) -> WizardChargerBackend | None:
    preset = _charger_preset(charger_preset)
    if preset is None:
        return None
    return _PRESET_BACKENDS.get(preset)


def apply_charger_preset_backend(
    charger_preset: str | None,
    backend: WizardChargerBackend | None,
) -> WizardChargerBackend | None:
    preset_backend = charger_preset_backend(charger_preset)
    return preset_backend if preset_backend is not None else backend


def relevant_charger_presets(backend: WizardChargerBackend | None) -> tuple[str, ...]:
    if backend != "modbus_charger":
        return ()
    return CHARGER_PRESET_VALUES


def preset_transport_port(
    charger_preset: str | None,
    transport_kind: WizardTransportKind,
) -> int | None:
    preset = _charger_preset(charger_preset)
    if preset is None or transport_kind != "tcp":
        return None
    return _PRESET_TCP_PORTS.get(preset)


def preset_transport_unit_id(charger_preset: str | None) -> int | None:
    preset = _charger_preset(charger_preset)
    if preset is None:
        return None
    return _PRESET_UNIT_IDS.get(preset)


def _charger_preset(charger_preset: str | None) -> WizardChargerPreset | None:
    """Return one known charger preset identifier, if the raw value is supported."""
    if charger_preset is None:
        return None
    for preset in CHARGER_PRESET_VALUES:
        if charger_preset == preset:
            return preset
    return None


def render_charger_preset_config(
    charger_preset: str,
    *,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> str:
    if charger_preset == "abb-terra-ac-modbus":
        return _abb_terra_ac_modbus_config(
            transport_kind,
            transport_host=transport_host,
            transport_port=transport_port,
            transport_device=transport_device,
            transport_unit_id=transport_unit_id,
        )
    if charger_preset == "cfos-power-brain-modbus":
        return _cfos_power_brain_modbus_config(
            transport_kind,
            transport_host=transport_host,
            transport_port=transport_port,
            transport_device=transport_device,
            transport_unit_id=transport_unit_id,
        )
    if charger_preset == "openwb-modbus-secondary":
        return _openwb_modbus_secondary_config(
            transport_kind,
            transport_host=transport_host,
            transport_port=transport_port,
            transport_device=transport_device,
            transport_unit_id=transport_unit_id,
        )
    raise ValueError(f"Unsupported charger preset '{charger_preset}'")


def _transport_block(
    transport_kind: str,
    *,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> str:
    if transport_kind == "tcp":
        return (
            "[Transport]\n"
            f"Host={transport_host}\n"
            f"Port={transport_port}\n"
            f"UnitId={transport_unit_id}\n"
        )
    return (
        "[Transport]\n"
        f"Device={transport_device}\n"
        "Baudrate=9600\n"
        "Parity=N\n"
        "StopBits=1\n"
        f"UnitId={transport_unit_id}\n"
    )


def _abb_terra_ac_modbus_config(
    transport_kind: str,
    *,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> str:
    return (
        "[Adapter]\n"
        "Type=modbus_charger\n"
        "Profile=generic\n"
        "Preset=abb-terra-ac-modbus\n"
        f"Transport={transport_kind}\n"
        f"{_transport_block(transport_kind, transport_host=transport_host, transport_port=transport_port, transport_device=transport_device, transport_unit_id=transport_unit_id)}"
        "[StateCurrent]\n"
        "RegisterType=holding\n"
        "Address=16398\n"
        "DataType=uint16\n"
        "Scale=1000\n"
        "[StateStatus]\n"
        "RegisterType=holding\n"
        "Address=16396\n"
        "DataType=uint16\n"
        "[StateFault]\n"
        "RegisterType=holding\n"
        "Address=16392\n"
        "DataType=uint16\n"
        "[EnableWrite]\n"
        "RegisterType=holding\n"
        "Address=16645\n"
        "TrueValue=0\n"
        "FalseValue=1\n"
        "[CurrentWrite]\n"
        "RegisterType=holding\n"
        "Address=16640\n"
        "DataType=uint16\n"
        "Scale=1000\n"
    )


def _cfos_power_brain_modbus_config(
    transport_kind: str,
    *,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> str:
    return (
        "[Adapter]\n"
        "Type=modbus_charger\n"
        "Profile=generic\n"
        "Preset=cfos-power-brain-modbus\n"
        f"Transport={transport_kind}\n"
        f"{_transport_block(transport_kind, transport_host=transport_host, transport_port=transport_port, transport_device=transport_device, transport_unit_id=transport_unit_id)}"
        "[Capabilities]\n"
        "SupportedPhaseSelections=P1,P1_P2_P3\n"
        "[StateCurrent]\n"
        "RegisterType=holding\n"
        "Address=8093\n"
        "DataType=uint16\n"
        "Scale=10\n"
        "[StateActualCurrent]\n"
        "RegisterType=holding\n"
        "Address=8095\n"
        "DataType=uint16\n"
        "Scale=10\n"
        "[StatePower]\n"
        "RegisterType=holding\n"
        "Address=8062\n"
        "DataType=int32\n"
        "[StateStatus]\n"
        "RegisterType=holding\n"
        "Address=8092\n"
        "DataType=uint16\n"
        "Map=0:waiting,1:vehicle-detected,2:charging,3:charging-ventilation,4:no-current,5:error,9:dc-sensor-error\n"
        "[EnableWrite]\n"
        "RegisterType=holding\n"
        "Address=8094\n"
        "TrueValue=1\n"
        "FalseValue=0\n"
        "[CurrentWrite]\n"
        "RegisterType=holding\n"
        "Address=8093\n"
        "DataType=uint16\n"
        "Scale=10\n"
        "[PhaseWrite]\n"
        "RegisterType=holding\n"
        "Address=8087\n"
        "DataType=uint16\n"
        "Map=P1:1,P1_P2_P3:0\n"
    )


def _openwb_modbus_secondary_config(
    transport_kind: str,
    *,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> str:
    return (
        "[Adapter]\n"
        "Type=modbus_charger\n"
        "Profile=generic\n"
        "Preset=openwb-modbus-secondary\n"
        f"Transport={transport_kind}\n"
        f"{_transport_block(transport_kind, transport_host=transport_host, transport_port=transport_port, transport_device=transport_device, transport_unit_id=transport_unit_id)}"
        "[Capabilities]\n"
        "SupportedPhaseSelections=P1\n"
        "EnableUsesCurrentWrite=1\n"
        "EnableDefaultCurrentAmps=6\n"
        "[StateCurrent]\n"
        "RegisterType=input\n"
        "Address=10116\n"
        "DataType=int16\n"
        "Scale=100\n"
        "[StatePower]\n"
        "RegisterType=input\n"
        "Address=10100\n"
        "DataType=int32\n"
        "[StateEnergy]\n"
        "RegisterType=input\n"
        "Address=10102\n"
        "DataType=int32\n"
        "Scale=1000\n"
        "[StateStatus]\n"
        "RegisterType=input\n"
        "Address=10115\n"
        "DataType=int16\n"
        "Map=0:idle,1:charging\n"
        "[CurrentWrite]\n"
        "RegisterType=holding\n"
        "Address=10171\n"
        "DataType=int16\n"
        "Scale=100\n"
    )
