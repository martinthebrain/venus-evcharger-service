# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import patch

from venus_evcharger.backend.shelly_contactor_switch import ShellyContactorSwitchBackend
from venus_evcharger.backend.shelly_switch import ShellySwitchBackend


class TestShellyContactorSwitchContracts(unittest.TestCase):
    def test_constructor_forwards_service_default_path_and_contactor_mode(self) -> None:
        service = object()

        with patch.object(ShellySwitchBackend, "__init__", return_value=None) as parent_init:
            ShellyContactorSwitchBackend(service)

        parent_init.assert_called_once_with(
            service,
            config_path="",
            default_switching_mode="contactor",
        )


if __name__ == "__main__":
    unittest.main()
