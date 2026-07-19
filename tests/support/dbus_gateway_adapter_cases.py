# SPDX-License-Identifier: GPL-3.0-or-later
"""Composition root for all responsibility-focused gateway adapter cases."""

from __future__ import annotations

from tests.support import dbus_gateway_adaptive_tick_cases as adaptive_tick_cases
from tests.support import dbus_gateway_circuit_resource_cases as circuit_resource_cases
from tests.support import dbus_gateway_command_rate_cases as command_rate_cases
from tests.support import dbus_gateway_health_history_cases as health_history_cases
from tests.support import dbus_gateway_health_metrics_cases as health_metrics_cases
from tests.support import dbus_gateway_health_slo_cases as health_slo_cases
from tests.support import dbus_gateway_introspection_background_cases as introspection_background_cases
from tests.support import dbus_gateway_introspection_execution_cases as introspection_execution_cases
from tests.support import dbus_gateway_introspection_request_cases as introspection_request_cases
from tests.support import dbus_gateway_process_health_cases as process_health_cases
from tests.support import dbus_gateway_process_io_cases as process_io_cases
from tests.support import dbus_gateway_process_loop_cases as process_loop_cases
from tests.support import dbus_gateway_read_aggregate_cases as read_aggregate_cases
from tests.support import (
    dbus_gateway_read_executor_aggregate_contracts_cases as read_executor_aggregate_contracts_cases,
)
from tests.support import dbus_gateway_read_executor_direct_cases as read_executor_direct_cases
from tests.support import dbus_gateway_read_pv_cases as read_pv_cases
from tests.support import dbus_gateway_read_scheduler_config_cases as read_scheduler_config_cases
from tests.support import dbus_gateway_read_target_contracts_cases as read_target_contracts_cases
from tests.support import dbus_gateway_regulation_cases as regulation_cases
from tests.support import dbus_gateway_socket_cases as socket_cases
from tests.support import dbus_gateway_write_burst_cases as write_burst_cases
from tests.support import dbus_gateway_write_command_dispatch_cases as write_command_dispatch_cases
from tests.support import dbus_gateway_write_followup_cases as write_followup_cases
from tests.support import dbus_gateway_write_health_boundaries_cases as write_health_boundaries_cases
from tests.support import dbus_gateway_write_lifecycle_cases as write_lifecycle_cases
from tests.support import dbus_gateway_write_publish_cases as write_publish_cases
from tests.support import dbus_gateway_write_registration_cases as write_registration_cases
from tests.support import dbus_gateway_write_support_cases as write_support_cases


class AllGatewayAdapterCases(
    health_metrics_cases.GatewayHealthMetricCases,
    read_target_contracts_cases.GatewayReadTargetContractCases,
    read_executor_direct_cases.GatewayReadExecutorDirectCases,
    read_executor_aggregate_contracts_cases.GatewayReadExecutorAggregateContractCases,
    command_rate_cases.GatewayCommandRateCases,
    circuit_resource_cases.GatewayCircuitResourceCases,
    read_scheduler_config_cases.GatewayReadSchedulerConfigCases,
    write_publish_cases.GatewayWritePublishCases,
    write_lifecycle_cases.GatewayWriteLifecycleCases,
    write_health_boundaries_cases.GatewayWriteHealthBoundaryCases,
    write_support_cases.GatewayWriteSupportCases,
    write_registration_cases.GatewayWriteRegistrationCases,
    write_command_dispatch_cases.GatewayWriteCommandDispatchCases,
    write_followup_cases.GatewayWriteFollowupCases,
    read_pv_cases.GatewayPvReadCases,
    read_aggregate_cases.GatewayAggregateReadCases,
    socket_cases.GatewaySocketCases,
    health_slo_cases.GatewayHealthSloCases,
    write_burst_cases.GatewayWriteBurstCases,
    regulation_cases.GatewayRegulationCases,
    health_history_cases.GatewayHealthHistoryCases,
    adaptive_tick_cases.GatewayAdaptiveTickCases,
    introspection_request_cases.GatewayIntrospectionRequestCases,
    introspection_background_cases.GatewayIntrospectionBackgroundCases,
    introspection_execution_cases.GatewayIntrospectionExecutionCases,
    process_loop_cases.GatewayProcessLoopCases,
    process_health_cases.GatewayProcessHealthCases,
    process_io_cases.GatewayProcessIoCases,
):
    """Combine split cases without exposing them as duplicate unittest suites."""
