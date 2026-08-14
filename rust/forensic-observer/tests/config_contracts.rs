use std::path::PathBuf;
use std::{fs, iter};

use tempfile::tempdir;
use venus_evcharger_forensic_observer::config::{BackendSelection, ObserverConfig};
use venus_evcharger_forensic_observer::ini::IniDocument;

fn config(text: &str) -> ObserverConfig {
    ObserverConfig {
        path: PathBuf::from("/tmp/config.ini"),
        source_text: text.to_owned(),
        ini: IniDocument::parse(text).unwrap_or_default(),
    }
}

#[test]
fn legacy_defaults_match_the_stable_backend_contract() {
    let selection = config("[DEFAULT]\n").backend_selection();
    assert_eq!(
        selection,
        Ok(BackendSelection {
            mode: "combined".to_owned(),
            meter_type: Some("shelly_meter".to_owned()),
            switch_type: Some("shelly_contactor_switch".to_owned()),
            charger_type: None,
            meter_config_path: None,
            switch_config_path: None,
            charger_config_path: None,
        })
    );
}

#[test]
fn paths_and_device_instance_use_exact_fallbacks() {
    let configured = config(
        "[DEFAULT]\nDeviceInstance=71\nAutoInputSnapshotPath= /tmp/auto \nGatewayDiagnosticsSnapshotPath= /tmp/gateway \n",
    );
    assert_eq!(configured.device_instance(), 71);
    assert_eq!(
        configured.auto_input_snapshot_path(),
        PathBuf::from("/tmp/auto")
    );
    assert_eq!(
        configured.gateway_diagnostics_path(),
        PathBuf::from("/tmp/gateway")
    );
    assert_eq!(
        configured.runtime_state_path(),
        PathBuf::from("/run/dbus-venus-evcharger-71.json")
    );

    let invalid = config("[DEFAULT]\nDeviceInstance=invalid\n");
    assert_eq!(invalid.device_instance(), 60);
}

#[test]
fn invalid_none_roles_fail_closed() {
    let invalid = config("[DEFAULT]\n[Backends]\nMode=split\nMeterType=none\n");
    assert!(invalid.backend_selection().is_err());

    let valid = config(
        "[DEFAULT]\n[Backends]\nMode=split\nMeterType=none\nSwitchType=none\nChargerType=goe_charger\n",
    );
    let selection = valid.backend_selection();
    assert!(selection.is_ok());
    let normalized = selection.unwrap_or_else(|_| BackendSelection {
        mode: String::new(),
        meter_type: None,
        switch_type: None,
        charger_type: None,
        meter_config_path: None,
        switch_config_path: None,
        charger_config_path: None,
    });
    assert_eq!(normalized.meter_type, None);
    assert_eq!(normalized.switch_type, None);
    assert_eq!(normalized.charger_type.as_deref(), Some("goe_charger"));
}

#[test]
fn configuration_file_size_is_bounded_before_parsing() {
    let directory = tempdir();
    assert!(directory.is_ok());
    let Some(directory) = directory.ok() else {
        return;
    };
    let path = directory.path().join("oversized.ini");
    let oversized = iter::repeat_n(b'x', 1_048_577).collect::<Vec<_>>();
    assert!(fs::write(&path, oversized).is_ok());
    assert!(ObserverConfig::load(&path).is_err());
}

#[test]
fn normalized_topology_roles_and_policy_are_validated() {
    let topology = config(
        "[DEFAULT]\n\
         [Topology]\nType=hybrid_topology\n\
         [Actuator]\nType=shelly_contactor_switch\n\
         [Measurement]\nType=actuator_native\n\
         [Charger]\nType=goe_charger\n\
         [Policy]\nMode=auto\n",
    );
    let selection = topology.backend_selection();
    assert!(selection.is_ok());
    let normalized = selection.unwrap_or_else(|_| BackendSelection {
        mode: String::new(),
        meter_type: None,
        switch_type: None,
        charger_type: None,
        meter_config_path: None,
        switch_config_path: None,
        charger_config_path: None,
    });
    assert_eq!(normalized.mode, "split");
    assert_eq!(normalized.meter_type.as_deref(), Some("shelly_meter"));
    assert_eq!(
        normalized.switch_type.as_deref(),
        Some("shelly_contactor_switch")
    );
    assert_eq!(normalized.charger_type.as_deref(), Some("goe_charger"));

    for invalid in [
        "[Topology]\nType=custom_topology\n[Policy]\nMode=invalid\n",
        "[Topology]\nType=custom_topology\n[Measurement]\nType=none\n[Policy]\nMode=auto\n",
        "[Topology]\nType=custom_topology\n[Measurement]\nType=fixed_reference\nReferenceWatts=invalid\n",
    ] {
        assert!(config(invalid).backend_selection().is_err());
    }
}

#[test]
fn external_meter_type_is_loaded_from_a_relative_bounded_adapter_file() {
    let directory = tempdir();
    assert!(directory.is_ok());
    let Some(directory) = directory.ok() else {
        return;
    };
    let adapter_path = directory.path().join("meter.ini");
    let config_path = directory.path().join("config.ini");
    assert!(fs::write(&adapter_path, "[Adapter]\nType=Template_Meter\n").is_ok());
    assert!(
        fs::write(
            &config_path,
            "[Topology]\nType=custom_topology\n[Measurement]\nType=external_meter\nConfigPath=meter.ini\n",
        )
        .is_ok()
    );
    let loaded = ObserverConfig::load(&config_path);
    assert!(loaded.is_ok());
    let selection = loaded.and_then(|value| value.backend_selection());
    assert_eq!(
        selection.ok().and_then(|value| value.meter_type),
        Some("template_meter".to_owned())
    );
}
