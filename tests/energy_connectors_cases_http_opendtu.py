# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_connectors_cases_common import *


class _EnergyConnectorsHttpOpenDtuCases:
    def test_read_energy_source_snapshot_dispatches_to_template_http_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://hybrid.local\n"
                "[EnergyRequest]\nMethod=GET\nUrl=/state\n"
                "[EnergyResponse]\nSocPath=data.soc\nUsableCapacityWhPath=data.capacity_wh\n"
                "BatteryPowerPath=data.battery_power_w\nAcPowerPath=data.ac_power_w\n"
                "PvInputPowerPath=data.pv_input_power_w\nGridInteractionPath=data.grid_power_w\n"
                "OperatingModePath=data.mode\n"
                "OnlinePath=data.online\nConfidencePath=data.confidence\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "data": {
                        "soc": 74.5,
                        "capacity_wh": 12000.0,
                        "battery_power_w": -1800.0,
                        "ac_power_w": 3200.0,
                        "pv_input_power_w": 2500.0,
                        "grid_power_w": -600.0,
                        "mode": "self-consumption",
                        "online": True,
                        "confidence": 0.8,
                    }
                }
            )
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="hybrid",
                role="hybrid-inverter",
                connector_type="template_http",
                config_path=config_path,
                service_name="external-hybrid",
            )

            snapshot = read_energy_source_snapshot(owner, source, 100.0)

            self.assertEqual(snapshot.source_id, "hybrid")
            self.assertEqual(snapshot.role, "hybrid-inverter")
            self.assertEqual(snapshot.service_name, "external-hybrid")
            self.assertEqual(snapshot.soc, 74.5)
            self.assertEqual(snapshot.usable_capacity_wh, 12000.0)
            self.assertEqual(snapshot.net_battery_power_w, -1800.0)
            self.assertEqual(snapshot.charge_power_w, 1800.0)
            self.assertEqual(snapshot.discharge_power_w, 0.0)
            self.assertEqual(snapshot.ac_power_w, 3200.0)
            self.assertEqual(snapshot.pv_input_power_w, 2500.0)
            self.assertEqual(snapshot.grid_interaction_w, -600.0)
            self.assertEqual(snapshot.operating_mode, "self-consumption")
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 0.8)
            self.assertEqual(snapshot.captured_at, 100.0)
            session.get.assert_called_once_with(url="http://hybrid.local/state", timeout=2.0)

    def test_template_http_connector_normalizes_out_of_range_values_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://battery.local\n"
                "[EnergyRequest]\nUrl=/snapshot\n"
                "[EnergyResponse]\nSocPath=soc\nUsableCapacityWhPath=capacity_wh\nConfidencePath=confidence\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "soc": 150.0,
                    "capacity_wh": -1.0,
                    "confidence": 5.0,
                }
            )
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="external-battery",
                role="battery",
                connector_type="template_http",
                config_path=config_path,
            )

            snapshot = read_energy_source_snapshot(owner, source, 123.0)

            self.assertIsNone(snapshot.soc)
            self.assertIsNone(snapshot.usable_capacity_wh)
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 1.0)

    def test_read_energy_source_snapshot_uses_source_capacity_fallback_and_dbus_connector(self) -> None:
        forwarded: list[tuple[str, float]] = []

        def _dbus_snapshot(source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
            forwarded.append((source.source_id, now))
            return EnergySourceSnapshot(
                source_id=source.source_id,
                role=source.role,
                service_name="com.victronenergy.battery.demo",
                soc=55.0,
                usable_capacity_wh=source.usable_capacity_wh,
                online=True,
                confidence=1.0,
                captured_at=now,
            )

        owner = SimpleNamespace(_dbus_energy_source_snapshot=_dbus_snapshot)
        source = EnergySourceDefinition(
            source_id="victron",
            role="battery",
            connector_type="dbus",
            usable_capacity_wh=5120.0,
        )

        snapshot = read_energy_source_snapshot(owner, source, 50.0)

        self.assertEqual(forwarded, [("victron", 50.0)])
        self.assertEqual(snapshot.usable_capacity_wh, 5120.0)
        bad_owner = SimpleNamespace(_dbus_energy_source_snapshot=MagicMock(return_value={"source_id": "bad"}))
        with self.assertRaisesRegex(TypeError, "_dbus_energy_source_snapshot must return EnergySourceSnapshot"):
            read_energy_source_snapshot(bad_owner, source, 51.0)

    def test_template_http_connector_uses_source_capacity_when_response_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://battery.local\n"
                "[EnergyRequest]\nUrl=/snapshot\n"
                "[EnergyResponse]\nSocPath=soc\nBatteryPowerPath=battery_power_w\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"soc": 61.0, "battery_power_w": 900.0})
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="external-battery",
                role="battery",
                connector_type="template_http",
                config_path=config_path,
                usable_capacity_wh=7000.0,
            )

            snapshot = read_energy_source_snapshot(owner, source, 200.0)

            self.assertEqual(snapshot.usable_capacity_wh, 7000.0)
            self.assertEqual(snapshot.discharge_power_w, 900.0)

    def test_read_energy_source_snapshot_dispatches_to_opendtu_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://opendtu.local\nRequestTimeoutSeconds=2.0\n"
                "[OpenDTU]\nStatusUrl=/api/livedata/status\nInverterStatusUrl=/api/livedata/status?inv=${serial}\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "inverters": [
                        {
                            "serial": "114182000000",
                            "reachable": True,
                            "producing": True,
                            "data_age": 4,
                            "AC": {"0": {"Power": {"v": 120.5}}},
                            "DC": {
                                "0": {"Power": {"v": 70.0}},
                                "1": {"Power": {"v": 68.0}},
                            },
                        },
                        {
                            "serial": "114182000001",
                            "reachable": False,
                            "producing": False,
                            "data_age": 700,
                            "AC": {"0": {"Power": {"v": 0.0}}},
                            "DC": {"0": {"Power": {"v": 0.0}}},
                        },
                    ],
                    "total": {"Power": {"v": 120.5}},
                }
            )
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="pv",
                role="inverter",
                connector_type="opendtu_http",
                config_path=config_path,
                service_name="opendtu",
            )

            snapshot = read_energy_source_snapshot(owner, source, 150.0)

            self.assertEqual(snapshot.service_name, "opendtu")
            self.assertEqual(snapshot.ac_power_w, 120.5)
            self.assertEqual(snapshot.pv_input_power_w, 138.0)
            self.assertEqual(snapshot.operating_mode, "producing")
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 0.5)
            self.assertEqual(snapshot.captured_at, 150.0)
            session.get.assert_called_once_with(url="http://opendtu.local/api/livedata/status", timeout=2.0)

    def test_opendtu_connector_fetches_detail_payloads_for_older_api_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://opendtu.local\nRequestTimeoutSeconds=2.0\n"
                "[OpenDTU]\nStatusUrl=/api/livedata/status\nInverterStatusUrl=/api/livedata/status?inv=${serial}\n"
                "InverterSerials=114182000000\n",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse(
                    {
                        "inverters": [
                            {
                                "serial": "114182000000",
                                "reachable": True,
                                "producing": True,
                                "data_age": 5,
                            },
                            {
                                "serial": "114182000001",
                                "reachable": True,
                                "producing": True,
                                "data_age": 5,
                            },
                        ],
                        "total": {"Power": {"v": 222.0}},
                    }
                ),
                _FakeResponse(
                    {
                        "inverters": [
                            {
                                "serial": "114182000000",
                                "reachable": True,
                                "producing": True,
                                "data_age": 5,
                                "AC": {"0": {"Power": {"v": 111.0}}},
                                "DC": {"0": {"Power": {"v": 118.0}}},
                            }
                        ]
                    }
                ),
            ]
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="pv",
                role="inverter",
                connector_type="opendtu_http",
                config_path=config_path,
            )

            snapshot = read_energy_source_snapshot(owner, source, 151.0)

            self.assertEqual(snapshot.ac_power_w, 111.0)
            self.assertEqual(snapshot.pv_input_power_w, 118.0)
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 1.0)
            self.assertEqual(session.get.call_count, 2)
            session.get.assert_any_call(url="http://opendtu.local/api/livedata/status?inv=114182000000", timeout=2.0)

    def test_opendtu_connector_treats_night_idle_payload_as_online_without_detail_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://opendtu.local\nRequestTimeoutSeconds=2.0\n"
                "[OpenDTU]\nStatusUrl=/api/livedata/status\nInverterStatusUrl=/api/livedata/status?inv=${serial}\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "inverters": [
                        {
                            "serial": "138284795068",
                            "reachable": False,
                            "producing": False,
                            "data_age": 6119,
                        },
                        {
                            "serial": "138284597017",
                            "reachable": False,
                            "producing": False,
                            "data_age": 5984,
                        },
                    ],
                    "total": {"Power": {"v": 0.0}},
                    "hints": {"radio_problem": False},
                }
            )
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="pv",
                role="inverter",
                connector_type="opendtu_http",
                config_path=config_path,
            )

            snapshot = read_energy_source_snapshot(owner, source, 152.0)

            self.assertEqual(snapshot.ac_power_w, 0.0)
            self.assertIsNone(snapshot.pv_input_power_w)
            self.assertEqual(snapshot.operating_mode, "idle")
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 1.0)
            self.assertEqual(session.get.call_count, 1)
            session.get.assert_called_once_with(url="http://opendtu.local/api/livedata/status", timeout=2.0)

    def test_opendtu_connector_keeps_radio_problem_payload_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://opendtu.local\nRequestTimeoutSeconds=2.0\n"
                "[OpenDTU]\nStatusUrl=/api/livedata/status\nInverterStatusUrl=/api/livedata/status?inv=${serial}\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "inverters": [
                        {
                            "serial": "138284795068",
                            "reachable": False,
                            "producing": False,
                            "data_age": 6119,
                        }
                    ],
                    "total": {"Power": {"v": 0.0}},
                    "hints": {"radio_problem": True},
                }
            )
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="pv",
                role="inverter",
                connector_type="opendtu_http",
                config_path=config_path,
            )

            snapshot = read_energy_source_snapshot(owner, source, 153.0)

            self.assertFalse(snapshot.online)
            self.assertEqual(snapshot.confidence, 0.0)
            session.get.assert_called_once_with(url="http://opendtu.local/api/livedata/status", timeout=2.0)

    def test_opendtu_connector_keeps_hybrid_source_strict_for_unreachable_idle_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://opendtu.local\nRequestTimeoutSeconds=2.0\n"
                "[OpenDTU]\nStatusUrl=/api/livedata/status\nInverterStatusUrl=/api/livedata/status?inv=${serial}\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "inverters": [
                        {
                            "serial": "138284795068",
                            "reachable": False,
                            "producing": False,
                            "data_age": 6119,
                        }
                    ],
                    "total": {"Power": {"v": 0.0}},
                    "hints": {"radio_problem": False},
                }
            )
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="hybrid-like",
                role="hybrid-inverter",
                connector_type="opendtu_http",
                config_path=config_path,
            )

            snapshot = read_energy_source_snapshot(owner, source, 154.0)

            self.assertFalse(snapshot.online)
            self.assertEqual(snapshot.confidence, 0.0)
