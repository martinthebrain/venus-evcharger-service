# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.auto.policy import (
    AutoLearnChargePowerPolicy,
    AutoPolicy,
    AutoStopEwmaPolicy,
    AutoThresholdProfile,
)
from venus_evcharger.controllers.state import ServiceStateController

STATE_SUMMARY_TIME = "venus_evcharger.controllers.state_summary.time.time"
STATE_RUNTIME_PARSER_READ = "venus_evcharger.controllers.state_runtime._CasePreservingConfigParser.read"
STATE_RUNTIME_WRITE = "venus_evcharger.controllers.state_runtime_overrides.write_text_atomically"
STATE_RUNTIME_LOG_WARNING = "venus_evcharger.controllers.state_json.logging.warning"
STATE_RESTORE_WRITE = "venus_evcharger.controllers.state_restore.write_text_atomically"
STATE_RESTORE_LOG_WARNING = "venus_evcharger.controllers.state_restore.logging.warning"
from tests.venus_evcharger_test_fixtures import make_runtime_state_service, make_state_validation_service



class ServiceStateControllerTestBase(unittest.TestCase):
    @staticmethod
    def _normalize_mode(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, (int, float, str)):
            return int(value)
        return 0
