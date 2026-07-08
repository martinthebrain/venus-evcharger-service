# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from venus_evcharger.bootstrap.wizard_models import WizardAnswers


def answer_defaults(answers: WizardAnswers) -> dict[str, object]:
    return {
        "profile": answers.profile,
        "host_input": answers.host_input,
        "meter_host_input": answers.meter_host_input,
        "switch_host_input": answers.switch_host_input,
        "charger_host_input": answers.charger_host_input,
        "device_instance": answers.device_instance,
        "phase": answers.phase,
        "policy_mode": answers.policy_mode,
        "digest_auth": answers.digest_auth,
        "username": answers.username,
        "password_present": bool(answers.password),
        "topology_preset": answers.topology_preset,
        "charger_backend": answers.charger_backend,
        "charger_preset": answers.charger_preset,
        "request_timeout_seconds": answers.request_timeout_seconds,
        "cerbo_relay_index": answers.cerbo_relay_index,
        "cerbo_relay_contact_mode": answers.cerbo_relay_contact_mode,
        "switch_group_supported_phase_selections": answers.switch_group_supported_phase_selections,
        "auto_start_surplus_watts": answers.auto_start_surplus_watts,
        "auto_stop_surplus_watts": answers.auto_stop_surplus_watts,
        "auto_min_soc": answers.auto_min_soc,
        "auto_resume_soc": answers.auto_resume_soc,
        "scheduled_enabled_days": answers.scheduled_enabled_days,
        "scheduled_latest_end_time": answers.scheduled_latest_end_time,
        "scheduled_night_current_amps": answers.scheduled_night_current_amps,
        "transport_kind": answers.transport_kind,
        "transport_host": answers.transport_host,
        "transport_port": answers.transport_port,
        "transport_device": answers.transport_device,
        "transport_unit_id": answers.transport_unit_id,
    }
