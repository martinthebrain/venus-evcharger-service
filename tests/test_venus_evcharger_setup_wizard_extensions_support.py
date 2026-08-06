# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import cast
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard import WizardAnswers, configure_wallbox, default_template_path, main


class _TestShellyWallboxSetupWizardExtensionsHelperRole:
    def _simple_relay_config(self, temp_dir: str, device_instance: int = 76) -> Path:
        config_path = Path(temp_dir) / "config.ini"
        configure_wallbox(
            WizardAnswers(
                profile="simple_relay",
                host_input="192.0.2.44",
                meter_host_input=None,
                switch_host_input=None,
                charger_host_input=None,
                device_instance=device_instance,
                phase="L1",
                policy_mode="manual",
                digest_auth=False,
                username="",
                password="",
                charger_backend=None,
            ),
            config_path=config_path,
            template_path=default_template_path(),
            imported_from=None,
        )
        return config_path

    def _run_json_inventory_action(self, config_path: Path, args: list[str]) -> tuple[int, dict[str, object]]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--json", "--non-interactive", "--config-path", str(config_path), *args])
        return rc, json.loads(stdout.getvalue())

    @staticmethod

    def _inventory_profile(payload: dict[str, object], profile_id: str) -> dict[str, object]:
        return cast(dict[str, object], _TestShellyWallboxSetupWizardExtensionsHelperRole._inventory_item(payload, "profiles", profile_id))

    @staticmethod

    def _inventory_binding(payload: dict[str, object], binding_id: str) -> dict[str, object]:
        return cast(dict[str, object], _TestShellyWallboxSetupWizardExtensionsHelperRole._inventory_item(payload, "bindings", binding_id))

    @staticmethod

    def _inventory_item(payload: dict[str, object], collection_key: str, item_id: str) -> object:
        items = _TestShellyWallboxSetupWizardExtensionsHelperRole._inventory_collection(payload, collection_key)
        item = next(filter(lambda candidate: _TestShellyWallboxSetupWizardExtensionsHelperRole._has_item_id(candidate, item_id), items), None)
        if item is not None:
            return item
        raise AssertionError(f"inventory item not found: {collection_key}.{item_id}")

    @staticmethod

    def _inventory_collection(payload: dict[str, object], collection_key: str) -> list[object]:
        inventory = payload["inventory"]
        assert isinstance(inventory, dict)
        items = inventory[collection_key]
        assert isinstance(items, list)
        return items

    @staticmethod

    def _has_item_id(candidate: object, item_id: str) -> bool:
        return isinstance(candidate, dict) and candidate.get("id") == item_id

    @staticmethod

    def _inventory_has_binding(payload: dict[str, object], binding_id: str) -> bool:
        inventory = payload["inventory"]
        assert isinstance(inventory, dict)
        bindings = inventory["bindings"]
        assert isinstance(bindings, list)
        return any(isinstance(binding, dict) and binding["id"] == binding_id for binding in bindings)


__all__ = [name for name in globals() if not name.startswith("__")]
