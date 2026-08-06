# Huawei Integration

This guide is the short operator-facing entry point for Huawei energy-source
integration.

## Current Coverage

The current Huawei support covers the known Huawei hybrid platform families and
their connection variants on top of the MA/MB register platforms:

| Family / profile group | Access mode | Status | Notes |
| --- | --- | --- | --- |
| `huawei_ma_*` | platform baseline | supported | shared MA register platform |
| `huawei_l1_*`, `huawei_lc0_*`, `huawei_lb0_*`, `huawei_m1_*` | MA-family variants | supported | all `native_ap`, `native_lan`, `sdongle`, `smartlogger` variants are available |
| `huawei_mb_*` | platform baseline | supported | shared MB register platform |
| `huawei_map0_*`, `huawei_mb0_*` | MB-family variants | supported | all `native_ap`, `native_lan`, `sdongle`, `smartlogger` variants are available |
| `huawei_mb_unit1`, `huawei_mb_unit2` | MB unit split | supported | exposes one MB storage unit per source |
| `huawei_map0_unit1`, `huawei_map0_unit2`, `huawei_mb0_unit1`, `huawei_mb0_unit2` | MB-family unit split | supported | family-specific unit split variants |
| `huawei_smartlogger_modbus_tcp` | generic SmartLogger | supported | generic logger entry point |

Compatibility aliases also accept `SUN5000` naming for the `LB0` and `MAP0`
families.

## Template Choice

- Use [template-energy-source-huawei-ma-modbus.ini](deploy/venus/template-energy-source-huawei-ma-modbus.ini) for one MA-family inverter baseline.
- Use [template-energy-source-huawei-mb-modbus.ini](deploy/venus/template-energy-source-huawei-mb-modbus.ini) for one MB-family inverter baseline.
- Use [template-energy-source-huawei-mb-unit1-modbus.ini](deploy/venus/template-energy-source-huawei-mb-unit1-modbus.ini) and [template-energy-source-huawei-mb-unit2-modbus.ini](deploy/venus/template-energy-source-huawei-mb-unit2-modbus.ini) when you want to expose two MB energy-storage units separately.

Family mapping:

- `MA`, `L1`, `LC0`, `LB0`, and `M1` profiles resolve to the MA template family.
- `MB`, `MAP0`, and `MB0` profiles resolve to the MB template family.
- `*_unit1` and `*_unit2` profiles resolve to the matching MB unit template.

## When To Use `unit1` And `unit2`

- Use the plain MB template when one combined MB source is enough.
- Use `unit1` and `unit2` when you want both storage units visible as separate battery-like sources.
- `AcPowerRead`, `PvInputPowerRead`, and `GridInteractionRead` are inverter-global or meter-global on MB. The shipped aggregation scope keys deduplicate them automatically when both unit templates are enabled.

## Supported Fields

Current Huawei starter coverage by field:

| Field | MA baseline | MB baseline | MB `unit1` / `unit2` |
| --- | --- | --- | --- |
| `soc` | yes | yes | yes |
| `net_battery_power_w` | yes | yes | yes |
| `charge_power_w` / `discharge_power_w` | derived | derived | derived |
| `ac_output_power_w` | yes | yes | yes, auto-deduplicated |
| `pv_input_power_w` | yes | yes | yes, auto-deduplicated |
| `grid_interaction_w` | yes, meter-backed | yes, meter-backed | yes, auto-deduplicated |
| `operating_mode` | yes | yes | yes |
| battery charge/discharge limit power | yes where configured | yes where configured | yes where configured |
| usable capacity from live register | not assumed | not assumed | not assumed |

Important notes:

- Huawei battery-power sign is normalized to the repo-internal sign convention.
- Meter-backed grid interaction is normalized so repo import/export semantics
  remain consistent.
- If you want the Huawei-backed meter view visible to Venus as a separate grid
  service, the companion bridge can publish that through the optional external
  grid companion path.
- `UsableCapacityWh` is still an explicit operator value for weighted combined
  SOC. The wizard and recommendation flow surface this as a follow-up instead
  of guessing it.

## Supported Templates And Intent

| Template | Intended use |
| --- | --- |
| `template-energy-source-huawei-ma-modbus.ini` | one MA-family hybrid inverter as one energy source |
| `template-energy-source-huawei-mb-modbus.ini` | one MB-family hybrid inverter as one energy source |
| `template-energy-source-huawei-mb-unit1-modbus.ini` | first MB storage unit as separate battery-like source |
| `template-energy-source-huawei-mb-unit2-modbus.ini` | second MB storage unit as separate battery-like source |

## Current Limits

Known limits of the present Huawei implementation:

- Register handling is normalized by platform family, not by every firmware
  build or marketing SKU.
- Real-device validation remains the source of truth for site-specific
  port/unit-id/access behavior.
- The current recommendation producer is Huawei-specific; the surrounding
  bundle and wizard plumbing is generic, but Huawei is still the first real
  producer.

## Validation Command

First detect a working endpoint if needed:

```bash
python3 -m venus_evcharger.energy.probe detect-modbus-energy /data/etc/huawei-ma-modbus.ini --profile huawei_ma_native_ap
```

Then run the Huawei validation:

```bash
python3 -m venus_evcharger.energy.probe validate-huawei-energy /data/etc/huawei-mb-modbus.ini --profile huawei_mb_sdongle --host 192.168.8.1
```

When you want direct operator output instead of JSON:

```bash
python3 -m venus_evcharger.energy.probe validate-huawei-energy /data/etc/huawei-mb-modbus.ini --profile huawei_mb_sdongle --host 192.168.8.1 --emit ini
python3 -m venus_evcharger.energy.probe validate-huawei-energy /data/etc/huawei-mb-modbus.ini --profile huawei_mb_sdongle --host 192.168.8.1 --emit wizard-hint
```

When you also want persisted helper files:

```bash
python3 -m venus_evcharger.energy.probe validate-huawei-energy /data/etc/huawei-mb-modbus.ini --profile huawei_mb_sdongle --host 192.168.8.1 --write-recommendation-prefix /data/tmp/huawei-mb
```

This writes:

- `/data/tmp/huawei-mb.ini`
- `/data/tmp/huawei-mb.wizard.txt`
- `/data/tmp/huawei-mb.summary.txt`
- `/data/tmp/huawei-mb.manifest.json`

The generic bundle contract is described in
[ENERGY_RECOMMENDATION_SCHEMA.md](ENERGY_RECOMMENDATION_SCHEMA.md).

You can then feed that bundle into the setup wizard:

```bash
python3 -m venus_evcharger.bootstrap.wizard --non-interactive --dry-run --profile simple-relay --host 192.0.2.44 --energy-recommendation-prefix /data/tmp/huawei-mb
```

When you want the wizard to merge the suggested `AutoEnergySources=` and
`AutoEnergySource.<id>.*` lines directly into the generated main config, add:

```bash
python3 -m venus_evcharger.bootstrap.wizard --non-interactive --profile simple-relay --host 192.0.2.44 --energy-recommendation-prefix /data/tmp/huawei-mb --apply-energy-merge
```

When you already know the usable battery capacity, you can set it in the same
run:

```bash
python3 -m venus_evcharger.bootstrap.wizard --non-interactive --profile simple-relay --host 192.0.2.44 --energy-recommendation-prefix /data/tmp/huawei-mb --apply-energy-merge --energy-default-usable-capacity-wh 15360
```

When you want to merge more than one recommendation bundle in one wizard run,
repeat `--energy-recommendation-prefix`. For per-source capacities, use
`--energy-usable-capacity-wh <source_id>=<Wh>`:

```bash
python3 -m venus_evcharger.bootstrap.wizard --non-interactive --profile simple-relay --host 192.0.2.44 --energy-recommendation-prefix /data/tmp/huawei-unit1 --energy-recommendation-prefix /data/tmp/huawei-unit2 --apply-energy-merge --energy-usable-capacity-wh huawei_unit1=15360 --energy-usable-capacity-wh huawei_unit2=7680
```

The wizard result will copy the Huawei helper files into its output directory
and show the suggested `AutoEnergySource.huawei.*` block directly in the wizard
result text. The JSON wizard result also carries the parsed recommendation as a
structured suggested energy-source entry. In addition, the wizard now writes
`wizard-auto-energy-merge.ini` as a ready-to-merge helper for
`AutoEnergySources=` and the matching `AutoEnergySource.<id>.*` lines. When
`--apply-energy-merge` is used, the wizard also applies that merge directly to
the generated main config and records that in the wizard result. The wizard and
merge helper also surface a direct follow-up for
`AutoEnergySource.huawei.UsableCapacityWh=<set-me>` so weighted combined SOC can
be enabled intentionally instead of being left implicit.

## How To Read The Result

Important top-level fields:

- `validation_ok`: all required configured Huawei fields responded.
- `meter_block_detected`: the Huawei meter block around `37100` responded.
- `detected.host` / `detected.port` / `detected.unit_id`: the working endpoint.
- `recommendation.suggested_profile`: the Huawei profile that matched the run.
- `recommendation.suggested_template`: the template file to start from.
- `recommendation.suggested_config_path`: suggested destination under `/data/etc`.
- `recommendation.config_snippet`: copy-paste block for the main wallbox config.
- `recommendation.wizard_hint_block`: compact operator summary for notes, tickets, or a wizard UI.
- `recommendation.summary`: short operator summary you can copy into notes or tickets.

Typical successful outcome:

- use profile `huawei_mb_sdongle`
- detected `port=502`
- detected `unit_id=1`
- `meter_block_detected=true`
- suggested template `deploy/venus/template-energy-source-huawei-mb-modbus.ini`

## Valid Meter Block

A valid Huawei meter block means these reads succeed as a set:

- `37100` meter status
- `37113` meter active power
- `37119` positive active energy
- `37121` reverse active energy
- `37125` meter type

For `37113`:

- Huawei meter sign: `> 0` export to grid, `< 0` import from grid
- repo sign: `> 0` import from grid, `< 0` export to grid

The shipped templates already normalize that sign for `grid_interaction_w`.
