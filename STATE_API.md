# Local State API

The local HTTP API also exposes a read/state surface beside the command
endpoint.

For the short architectural overview of Control vs State vs Events, see
[API_OVERVIEW.md](API_OVERVIEW.md).

For operator quick checks and target-system CLI examples, see
[API_OPERATOR_GUIDE.md](API_OPERATOR_GUIDE.md).
For local PC-based development and API/CLI iteration, see
[DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md).

This API is intentionally:

- local-first
- versioned
- read-oriented
- sourced from existing service state, not a second model

It reuses the same listener configured through:

```ini
ControlApiEnabled=1
ControlApiHost=127.0.0.1
ControlApiPort=8765
ControlApiReadToken=
ControlApiControlToken=
ControlApiLocalhostOnly=1
ControlApiUnixSocketPath=
```

Read access requires:

- `Authorization: Bearer <read-or-control-token>` when read auth is configured
- a local client when `ControlApiLocalhostOnly=1`

Successful state and capability reads also expose the current local state token:

- `ETag`
- `X-State-Token`

Clients can pass that token back to `POST /v1/control/command` via `If-Match`
or `X-State-Token` to protect writes against stale state assumptions.

## Endpoints

### `GET /v1/state/summary`

Returns the compact state-summary string already used by logs and recovery
paths.

### `GET /v1/state/runtime`

Returns the current runtime-state snapshot used for runtime persistence.

### `GET /v1/state/automation`

Returns one automation-friendly snapshot for clients that need reliable read
and write coordination. This endpoint intentionally combines the most useful
read surfaces into one payload and includes the current `state_token`.

Typical fields in `state` include:

- `state_token`: use this as `If-Match` for the next write
- `command_endpoint`: currently `/v1/control/command`
- `events_endpoint`: currently `/v1/events`
- `safe_write`: header names and the recommended read-then-write flow
- `writable.command_names`
- `writable.scope_requirements`
- `operational`
- `auto_decision`
- `health`
- `topology`
- `diagnostics`

Recommended write flow:

1. Read `/v1/state/automation`.
2. Send `POST /v1/control/command` with `If-Match: <state_token>`.
3. Add `Idempotency-Key` for retry-safe automation writes.
4. If the API returns `409`, read `/v1/state/automation` again and retry only
   after checking that the intended command is still safe.

### `GET /v1/state/operational`

Returns a normalized operator-facing snapshot for automation and monitoring.

Stable fields in `state` include:

- `mode`
- `enable`
- `startstop`
- `autostart`
- `active_phase_selection`
- `requested_phase_selection`
- `backend_mode`
- `meter_backend`
- `switch_backend`
- `charger_backend`
- `auto_state`
- `auto_state_code`
- `fault_active`
- `fault_reason`
- `software_update_state`
- `software_update_state_code`
- `software_update_available`
- `software_update_no_update_active`
- `runtime_overrides_active`
- `runtime_overrides_path`
- `auto_decision`
- `combined_battery_soc`
- `combined_battery_source_count`
- `combined_battery_online_source_count`
- `combined_battery_charge_power_w`
- `combined_battery_discharge_power_w`
- `combined_battery_net_power_w`
- `combined_battery_ac_power_w`
- `combined_battery_pv_input_power_w`
- `combined_battery_grid_interaction_w`
- `combined_battery_headroom_charge_w`
- `combined_battery_headroom_discharge_w`
- `expected_near_term_export_w`
- `expected_near_term_import_w`
- `combined_battery_average_confidence`
- `combined_battery_battery_source_count`
- `combined_battery_hybrid_inverter_source_count`
- `combined_battery_inverter_source_count`
- `combined_battery_learning_profile_count`
- `combined_battery_observed_max_charge_power_w`
- `combined_battery_observed_max_discharge_power_w`
- `combined_battery_observed_max_ac_power_w`
- `combined_battery_observed_max_pv_input_power_w`
- `combined_battery_observed_max_grid_import_w`
- `combined_battery_observed_max_grid_export_w`
- `combined_battery_average_active_charge_power_w`
- `combined_battery_average_active_discharge_power_w`
- `combined_battery_average_active_power_delta_w`
- `combined_battery_power_smoothing_ratio`
- `combined_battery_typical_response_delay_seconds`
- `combined_battery_support_bias`
- `combined_battery_day_support_bias`
- `combined_battery_night_support_bias`
- `combined_battery_import_support_bias`
- `combined_battery_export_bias`
- `combined_battery_battery_first_export_bias`
- `combined_battery_reserve_band_floor_soc`
- `combined_battery_reserve_band_ceiling_soc`
- `combined_battery_reserve_band_width_soc`

`auto_decision` is the structured "why did it start/stop?" view for local
GUIs and scripts. It mirrors the most important DBus diagnostics without
forcing clients to parse path-keyed data:

- `reason`
- `state`
- `state_code`
- `relay_intent`
- `surplus_watts`
- `grid_watts`
- `soc_percent`
- `start_threshold_watts`
- `stop_threshold_watts`
- `profile`
- `threshold_mode`
- `combined_battery_direction_change_count`
- `battery_discharge_balance_mode`
- `battery_discharge_balance_target_distribution_mode`
- `battery_discharge_balance_error_w`
- `battery_discharge_balance_max_abs_error_w`
- `battery_discharge_balance_total_discharge_w`
- `battery_discharge_balance_eligible_source_count`
- `battery_discharge_balance_active_source_count`
- `battery_discharge_balance_control_candidate_count`
- `battery_discharge_balance_control_ready_count`
- `battery_discharge_balance_supported_control_source_count`
- `battery_discharge_balance_experimental_control_source_count`
- `battery_discharge_balance_policy_enabled`
- `battery_discharge_balance_warning_active`
- `battery_discharge_balance_warning_error_w`
- `battery_discharge_balance_warn_threshold_w`
- `battery_discharge_balance_bias_mode`
- `battery_discharge_balance_bias_gate_active`
- `battery_discharge_balance_bias_start_error_w`
- `battery_discharge_balance_bias_penalty_w`
- `battery_discharge_balance_coordination_policy_enabled`
- `battery_discharge_balance_coordination_support_mode`
- `battery_discharge_balance_coordination_feasibility`
- `battery_discharge_balance_coordination_gate_active`
- `battery_discharge_balance_coordination_start_error_w`
- `battery_discharge_balance_coordination_penalty_w`
- `battery_discharge_balance_coordination_advisory_active`
- `battery_discharge_balance_coordination_advisory_reason`
- `battery_discharge_balance_victron_bias_enabled`
- `battery_discharge_balance_victron_bias_active`
- `battery_discharge_balance_victron_bias_source_id`
- `battery_discharge_balance_victron_bias_topology_key`
- `battery_discharge_balance_victron_bias_activation_mode`
- `battery_discharge_balance_victron_bias_activation_gate_active`
- `battery_discharge_balance_victron_bias_support_mode`
- `battery_discharge_balance_victron_bias_learning_profile_key`
- `battery_discharge_balance_victron_bias_learning_profile_action_direction`
- `battery_discharge_balance_victron_bias_learning_profile_site_regime`
- `battery_discharge_balance_victron_bias_learning_profile_direction`
- `battery_discharge_balance_victron_bias_learning_profile_day_phase`
- `battery_discharge_balance_victron_bias_learning_profile_reserve_phase`
- `battery_discharge_balance_victron_bias_learning_profile_sample_count`
- `battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds`
- `battery_discharge_balance_victron_bias_learning_profile_estimated_gain`
- `battery_discharge_balance_victron_bias_learning_profile_overshoot_count`
- `battery_discharge_balance_victron_bias_learning_profile_settled_count`
- `battery_discharge_balance_victron_bias_learning_profile_stability_score`
- `battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second`
- `battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts`
- `battery_discharge_balance_victron_bias_source_error_w`
- `battery_discharge_balance_victron_bias_pid_output_w`
- `battery_discharge_balance_victron_bias_setpoint_w`
- `battery_discharge_balance_victron_bias_telemetry_clean`
- `battery_discharge_balance_victron_bias_telemetry_clean_reason`
- `battery_discharge_balance_victron_bias_response_delay_seconds`
- `battery_discharge_balance_victron_bias_estimated_gain`
- `battery_discharge_balance_victron_bias_overshoot_active`
- `battery_discharge_balance_victron_bias_overshoot_count`
- `battery_discharge_balance_victron_bias_settling_active`
- `battery_discharge_balance_victron_bias_settled_count`
- `battery_discharge_balance_victron_bias_stability_score`
- `battery_discharge_balance_victron_bias_oscillation_lockout_enabled`
- `battery_discharge_balance_victron_bias_oscillation_lockout_active`
- `battery_discharge_balance_victron_bias_oscillation_lockout_reason`
- `battery_discharge_balance_victron_bias_oscillation_lockout_until`
- `battery_discharge_balance_victron_bias_oscillation_direction_change_count`
- `battery_discharge_balance_victron_bias_recommended_kp`
- `battery_discharge_balance_victron_bias_recommended_ki`
- `battery_discharge_balance_victron_bias_recommended_kd`
- `battery_discharge_balance_victron_bias_recommended_deadband_watts`
- `battery_discharge_balance_victron_bias_recommended_max_abs_watts`
- `battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second`
- `battery_discharge_balance_victron_bias_recommended_activation_mode`
- `battery_discharge_balance_victron_bias_recommendation_confidence`
- `battery_discharge_balance_victron_bias_recommendation_reason`
- `battery_discharge_balance_victron_bias_recommendation_profile_key`
- `battery_discharge_balance_victron_bias_recommendation_ini_snippet`
- `battery_discharge_balance_victron_bias_recommendation_hint`
- `battery_discharge_balance_victron_bias_auto_apply_enabled`
- `battery_discharge_balance_victron_bias_auto_apply_active`
- `battery_discharge_balance_victron_bias_auto_apply_reason`
- `battery_discharge_balance_victron_bias_auto_apply_generation`
- `battery_discharge_balance_victron_bias_auto_apply_observation_window_active`
- `battery_discharge_balance_victron_bias_auto_apply_observation_window_until`
- `battery_discharge_balance_victron_bias_auto_apply_last_param`
- `battery_discharge_balance_victron_bias_rollback_enabled`
- `battery_discharge_balance_victron_bias_rollback_active`
- `battery_discharge_balance_victron_bias_rollback_reason`
- `battery_discharge_balance_victron_bias_rollback_stable_profile_key`
- `battery_discharge_balance_victron_bias_reason`

These combined battery fields are populated from the normalized multi-source
energy snapshot used by Auto mode. With `AutoEnergySources=...`, the service
can aggregate several battery or hybrid-inverter sources and expose the
combined result here for local tooling and troubleshooting.

### Combined battery state details

- `combined_battery_soc` is the aggregated SOC view used for operator-facing
  diagnostics
- `combined_battery_source_count` counts configured/readable energy sources in
  the current snapshot
- `combined_battery_online_source_count` counts sources that were online in the
  current snapshot
- `combined_battery_charge_power_w` sums currently observed charging power
- `combined_battery_discharge_power_w` sums currently observed discharge power
- `combined_battery_net_power_w` is the signed combined battery power
- `combined_battery_ac_power_w` sums configured AC-side power visibility
- `combined_battery_pv_input_power_w` sums configured PV-side visibility from
  hybrid or inverter-like sources
- `combined_battery_grid_interaction_w` sums known grid import/export influence
  from external energy sources
- `combined_battery_headroom_charge_w` is the conservative remaining charging
  headroom derived from current charge activity plus learned observed maxima
- `combined_battery_headroom_discharge_w` is the conservative remaining
  discharge headroom derived from current discharge activity plus learned
  observed maxima
- `expected_near_term_export_w` is a small near-term export estimate derived
  from current grid interaction, charge activity, bias, and learned response
  delay
- `expected_near_term_import_w` is a small near-term import estimate derived
  from current grid interaction, discharge activity, bias, and learned
  response delay
- `combined_battery_average_confidence` is the mean confidence reported by the
  current normalized sources
- `combined_battery_battery_source_count`,
  `combined_battery_hybrid_inverter_source_count`, and
  `combined_battery_inverter_source_count` split the current source set by
  role
- `combined_battery_learning_profile_count` counts runtime learning profiles
  that currently contribute observed maxima
- `combined_battery_observed_max_charge_power_w` and
  `combined_battery_observed_max_discharge_power_w` summarize the runtime-only
  learned maxima across sources
- `combined_battery_observed_max_ac_power_w`,
  `combined_battery_observed_max_pv_input_power_w`,
  `combined_battery_observed_max_grid_import_w`, and
  `combined_battery_observed_max_grid_export_w` summarize richer observed
  extrema across the current learning profiles
- `combined_battery_average_active_charge_power_w` and
  `combined_battery_average_active_discharge_power_w` expose the learned mean
  active charge/discharge levels
- `combined_battery_average_active_power_delta_w` exposes the learned mean
  active power change between comparable charge/discharge samples
- `combined_battery_power_smoothing_ratio` turns that variance into a
  normalized `0..1` smoothing score where higher values indicate steadier
  battery behavior
- `combined_battery_typical_response_delay_seconds` is the runtime-learned
  delay from inactive to active battery response or between strong direction
  changes
- `combined_battery_support_bias`, `combined_battery_day_support_bias`,
  `combined_battery_night_support_bias`,
  `combined_battery_import_support_bias`, and
  `combined_battery_export_bias` expose learned directional tendencies on a
  `-1..1` scale, including the current day/night split
- `combined_battery_battery_first_export_bias` distinguishes “battery absorbs
  export first” from “export leaves immediately” on the same `-1..1` scale
- `combined_battery_reserve_band_floor_soc`,
  `combined_battery_reserve_band_ceiling_soc`, and
  `combined_battery_reserve_band_width_soc` expose the currently learned
  conservative SOC reserve band across the contributing sources
- `combined_battery_direction_change_count` counts observed charge/discharge
  reversals in the runtime learning window
- `battery_discharge_balance_mode` describes the current fairness heuristic
  used for per-source discharge comparison
- `battery_discharge_balance_target_distribution_mode` repeats the currently
  active target-distribution rule in explicit coordination language
- `battery_discharge_balance_error_w` is the aggregate redistribution amount
  that would be needed to bring the active battery-like sources onto their
  current fair-share targets
- `battery_discharge_balance_max_abs_error_w` is the largest absolute
  per-source target deviation in the current snapshot
- `battery_discharge_balance_total_discharge_w` is the total currently active
  discharge power that the fairness calculation distributes
- `battery_discharge_balance_eligible_source_count` counts online battery-like
  sources that currently participate in the fairness model
- `battery_discharge_balance_active_source_count` counts participating sources
  that are actively discharging in the current snapshot
- `battery_discharge_balance_control_candidate_count` counts battery-like
  sources whose configured profile at least hints at some potential write path
- `battery_discharge_balance_control_ready_count` counts those candidates that
  are also online in the current snapshot
- `battery_discharge_balance_supported_control_source_count` counts sources
  whose current profile marks write support as supported
- `battery_discharge_balance_experimental_control_source_count` counts sources
  whose current profile marks write support as experimental
- `battery_discharge_balance_policy_enabled` shows whether the soft
  discharge-balance policy is enabled in config
- `battery_discharge_balance_warning_active` shows whether the current
  discharge imbalance crossed the configured warning threshold
- `battery_discharge_balance_warning_error_w` repeats the current imbalance
  magnitude when the warning is active
- `battery_discharge_balance_warn_threshold_w` shows the configured warning
  threshold in watts
- `battery_discharge_balance_bias_mode` shows how the soft imbalance penalty is
  gated, for example `always`, `export_only`, or
  `export_and_above_reserve_band`
- `battery_discharge_balance_bias_gate_active` shows whether the current site
  conditions actually allow the soft imbalance penalty to take effect
- `battery_discharge_balance_bias_start_error_w` shows when the soft
  discharge-balance penalty begins to ramp in
- `battery_discharge_balance_bias_penalty_w` shows the currently applied
  extra Auto-mode surplus penalty derived from that imbalance
- `battery_discharge_balance_coordination_policy_enabled` shows whether the
  second-stage conservative coordination penalty is enabled in config
- `battery_discharge_balance_coordination_support_mode` shows whether that
  second-stage penalty currently reacts only to `supported` write paths or may
  also react to `experimental` ones
- `battery_discharge_balance_coordination_feasibility` summarizes whether the
  current source mix looks `supported`, `experimental`, `partial`,
  `blocked_by_source_availability`, `observe_only`, or `not_needed` for real
  multi-ESS coordination
- `battery_discharge_balance_coordination_gate_active` shows whether that
  second-stage coordination penalty is currently allowed to take effect
- `battery_discharge_balance_coordination_start_error_w` shows when the
  coordination-stage penalty begins to ramp in
- `battery_discharge_balance_coordination_penalty_w` shows the currently
  applied coordination-stage surplus penalty
- `battery_discharge_balance_coordination_advisory_active` shows whether the
  current imbalance should be treated as operator-visible advisory rather than
  silently tolerated
- `battery_discharge_balance_coordination_advisory_reason` explains that
  advisory in compact repo terms such as
  `only_some_sources_offer_a_write_path`
- `battery_discharge_balance_victron_bias_enabled` shows whether the
  experimental Victron-side balance-bias controller is enabled in config
- `battery_discharge_balance_victron_bias_active` shows whether that
  controller is currently holding or applying a Victron-side setpoint
- `battery_discharge_balance_victron_bias_source_id` shows which source is
  treated as the local Victron-side battery path for this controller
- `battery_discharge_balance_victron_bias_topology_key` identifies the
  currently learned Victron-bias topology, so persisted learning can be reused
  only for the same source/service/path/energy-source combination
- `battery_discharge_balance_victron_bias_activation_mode` shows the current
  controller activation gate such as `always`, `export_only`,
  `above_reserve_band`, or `export_and_above_reserve_band`
- `battery_discharge_balance_victron_bias_activation_gate_active` shows
  whether that gate currently allows the controller to run
- `battery_discharge_balance_victron_bias_support_mode` shows whether that
  controller currently requires `supported` write support or may also use
  `experimental`
- `battery_discharge_balance_victron_bias_learning_profile_action_direction`
  splits telemetry between `more_export` and `less_export`
- `battery_discharge_balance_victron_bias_learning_profile_site_regime`,
  `..._day_phase`, and `..._reserve_phase` show the currently active profiled
  learning bucket
- `battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second`
  and `..._preferred_bias_limit_watts` expose the small formal learned profile
  values derived from that active bucket
- `battery_discharge_balance_victron_bias_source_error_w` repeats the signed
  per-source discharge-balance error used as the controller input
- `battery_discharge_balance_victron_bias_pid_output_w` shows the current PID
  controller output before the configured base setpoint is added
- `battery_discharge_balance_victron_bias_setpoint_w` shows the current target
  setpoint written or held for the Victron-side GX path
- `battery_discharge_balance_victron_bias_response_delay_seconds` is a
  runtime-learned response estimate for how long the shared site behavior
  typically takes to react measurably after a Victron-side bias command
- `battery_discharge_balance_victron_bias_estimated_gain` is a small learned
  effectiveness estimate for how much absolute discharge-balance error tends
  to improve per watt of commanded Victron-side bias
- `battery_discharge_balance_victron_bias_overshoot_active` shows whether the
  current learned command episode has already crossed through the error sign
  and therefore looks like an overshoot
- `battery_discharge_balance_victron_bias_overshoot_count` counts those
  overshoot episodes since service start
- `battery_discharge_balance_victron_bias_settling_active` shows whether the
  current learned command episode is still waiting to settle inside the
  configured deadband
- `battery_discharge_balance_victron_bias_settled_count` counts learned
  command episodes that reached the configured deadband without being reset
- `battery_discharge_balance_victron_bias_stability_score` is a conservative
  heuristic `0..1` runtime score derived from settling success, overshoot
  behavior, learned gain, and response delay
- `battery_discharge_balance_victron_bias_recommended_kp`,
  `battery_discharge_balance_victron_bias_recommended_ki`,
  `..._recommended_kd`, `..._recommended_deadband_watts`,
  `..._recommended_max_abs_watts`,
  `..._recommended_ramp_rate_watts_per_second`, and
  `..._recommended_activation_mode` are observational recommendations derived
  from the learned telemetry and the current controller tuning
- `battery_discharge_balance_victron_bias_recommendation_confidence` is a
  conservative `0..1` confidence score for those observational recommendations
  based on sample count and stability
- `battery_discharge_balance_victron_bias_recommendation_reason` explains the
  current recommendation in compact repo terms such as
  `insufficient_telemetry`, `slow_response`, `overshoot_risk`, or
  `can_relax_conservatism`
- `battery_discharge_balance_victron_bias_recommendation_ini_snippet` is a
  copy-paste-ready INI block for the currently recommended Victron-bias
  tuning values
- `battery_discharge_balance_victron_bias_recommendation_hint` is a short
  operator-facing summary sentence for the same recommendation
- `battery_discharge_balance_victron_bias_auto_apply_enabled`,
  `..._auto_apply_active`, `..._auto_apply_reason`, and
  `..._auto_apply_generation` expose the guarded runtime self-tuning stage
- `battery_discharge_balance_victron_bias_reason` explains the current
  controller state in compact repo terms such as `applied`,
  `auto-mode-inactive-restored`, or `victron-source-support-blocked`

`GET /v1/state/runtime` also exposes the lower-level aggregation payload,
including:

- `combined_battery_usable_capacity_wh`
- `combined_battery_valid_soc_source_count`
- `combined_battery_headroom_charge_w`
- `combined_battery_headroom_discharge_w`
- `expected_near_term_export_w`
- `expected_near_term_import_w`
- `combined_battery_sources`
- `combined_battery_learning_profiles`

Each entry in `combined_battery_sources` may now also include:

- `charge_limit_power_w`
- `discharge_limit_power_w`
- `pv_input_power_w`
- `grid_interaction_w`
- `operating_mode`
- `ac_output_power_w`
- `discharge_balance_eligible`
- `discharge_balance_weight`
- `discharge_balance_weight_basis`
- `discharge_balance_available_energy_wh`
- `discharge_balance_reserve_floor_soc`
- `discharge_balance_target_distribution_mode`
- `discharge_balance_target_share`
- `discharge_balance_target_power_w`
- `discharge_balance_actual_power_w`
- `discharge_balance_error_w`
- `discharge_balance_relative_error`
- `discharge_balance_control_profile_name`
- `discharge_balance_control_connector_type`
- `discharge_balance_control_support`
- `discharge_balance_control_candidate`
- `discharge_balance_control_ready`
- `discharge_balance_control_reason`

For vendor-aware templates such as the Huawei Modbus starters, `operating_mode`
may already be a semantic label like `maximise_self_consumption` instead of a
raw numeric code. The Huawei starters now also populate `pv_input_power_w` from
the documented `Total input power` register and `grid_interaction_w` from the
meter active-power block with normalized sign semantics.

These per-source entries are also the basis for the optional companion DBus
bridge. When the companion bridge is enabled, normalized battery-like sources
can be published as `com.victronenergy.battery.external.*` services and
normalized inverter-like sources as `com.victronenergy.pvinverter.external.*`
services without changing the main EV charger DBus identity. Sources that
currently expose `grid_interaction_w` can also be published as optional
`com.victronenergy.grid.external.*` services when the grid companion path is
enabled in config.

The `discharge_balance_*` fields are diagnostic-only. They help identify
whether multiple ESS or battery-like sources are discharging in proportion to
their currently available energy and reserve headroom, but they do not yet
actively coordinate or command those sources.

`discharge_balance_weight_basis` makes the target basis explicit per source:

- `available_energy_above_reserve`
- `usable_capacity_fallback`
- `uniform_fallback`

The `discharge_balance_control_*` fields are also diagnostic-only. They do not
mean the service already commands that ESS; they only show whether the current
configured profile suggests no write path, an experimental one, or a supported
one.

The coordination-feasibility fields build on top of those write hints. The
optional coordination penalty still does not mean the service sends ESS
setpoints or truly orchestrates both systems. It only allows Auto mode to back
off a little more once at least two sources look control-ready and a large
imbalance is already visible.

When the optional Auto discharge-balance policy is enabled, the operational
state can additionally show a warning state and a conservative extra surplus
penalty. This still does not command either ESS directly; it only makes Auto
mode more cautious while a large imbalance is present.

### `GET /v1/state/dbus-diagnostics`

Returns the compact outward diagnostics set that the service also publishes on
DBus. The returned `state` object uses DBus path names as stable keys.

For operator-facing "why did it start/stop?" views, start with:

- `/Auto/DecisionReason`
- `/Auto/DecisionState`
- `/Auto/DecisionRelayIntent`
- `/Auto/DecisionSurplusWatts`
- `/Auto/DecisionGridWatts`
- `/Auto/DecisionSocPercent`
- `/Auto/DecisionStartThresholdWatts`
- `/Auto/DecisionStopThresholdWatts`
- `/Auto/ScheduledReason`
- `/Auto/PhaseLockoutReason`
- `/Auto/ContactorLockoutReason`

### `GET /v1/state/topology`

Returns the currently effective topology view, including backend roles,
supported phase selections, available modes, and service identity.

### `GET /v1/state/update`

Returns the current software-update view, including:

- current version
- available version
- availability flag
- update state
- detail
- last check/run timestamps
- next scheduled check
- queued run time

### `GET /v1/state/config-effective`

Returns a safe effective-config subset for local tooling. This endpoint is
intentionally curated and excludes secrets such as passwords and tokens.

Typical fields include:

- device identity
- runtime paths
- control API binding settings
- companion bridge settings
- companion grid authoritative-source setting
- companion grid hold/smoothing settings
- companion grid smoothing jump-threshold settings
- `auto_use_combined_battery_soc`
- `auto_energy_source_ids`
- `auto_energy_source_profiles`
- `auto_energy_source_profile_details`
- backend selection
- selected scheduled/Auto policy basics

`auto_energy_source_profile_details` exposes a safe outward-facing metadata
view for configured presets, including vendor/platform/access-mode hints and
default probe candidates. This is especially useful when local tools should
explain why one profile such as `huawei_ma_native_ap` behaves differently from
`huawei_smartlogger_modbus_tcp` without re-encoding that knowledge.

### `GET /v1/state/health`

Returns a compact local health view, including:

- health reason and code
- fault state
- runtime-override activity
- control API binding status
- update staleness and recovery timestamps

### `GET /v1/state/healthz`

Returns a tiny liveness-style payload for local supervision.

This endpoint is intentionally lightweight and is also available without auth
when the local listener is enabled. It is still subject to the service's local
binding rules.

Typical fields include:

- `alive`
- `service`
- `api_version`

### `GET /v1/state/version`

Returns the current service and API version identity for local tooling.

Typical fields include:

- `service_version`
- `api_version`
- `product_name`
- `instance_id`

### `GET /v1/state/build`

Returns build and product identity metadata for the running service.

Typical fields include:

- `product_name`
- `connection_name`
- `hardware_version`
- `firmware_version`
- `runtime_state_path`

### `GET /v1/state/contracts`

Returns links and endpoint references for the active API contract surface.

Typical fields include:

- `openapi_path`
- `capabilities_path`
- `control_api_doc`
- `state_api_doc`
- `versioning_doc`

## Event stream relationship

`GET /v1/events` complements the read endpoints:

- `state/*` is the current snapshot surface
- `events` is the incremental change surface
- `events?kind=...` can narrow the incremental surface to selected event kinds

Clients that need a quick current picture should start with one or more
`state/*` reads or use `/v1/events?once=1` to receive a snapshot event plus
recent events.

## Curl examples

Read one operational snapshot:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/operational
```

Read topology:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/topology
```

Read update state:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/update
```

Read effective config:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/config-effective
```

Read health:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/health
```

Read healthz:

```bash
curl -s \
  http://127.0.0.1:8765/v1/state/healthz
```

Read version:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/version
```

Use a unix socket:

```bash
curl --unix-socket /run/venus-evcharger-control.sock \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://localhost/v1/state/summary
```

## Versioning

The formal versioning rules are documented in [API_VERSIONING.md](API_VERSIONING.md).
