# SPDX-License-Identifier: GPL-3.0-or-later
from tests.control_api_http_cases_tail_support import *  # noqa: F401,F403


class __ControlApiHttpTailCasesPart3:
    def test_rate_limiter_default_request_window_contracts_are_exact(self) -> None:
        limiter = ControlApiRateLimiter()

        for index in range(30):
            with self.subTest(index=index):
                self.assertEqual(limiter.allow_request("client-a", now=10.0), (True, 0.0))

        self.assertEqual(limiter.allow_request("client-a", now=10.0), (False, 5.0))
        self.assertEqual(limiter.allow_request("client-b", now=10.0), (True, 0.0))

    def test_rate_limiter_request_retry_and_boundary_contracts_are_exact(self) -> None:
        limiter = ControlApiRateLimiter(max_requests=1, window_seconds=5.0)

        self.assertEqual(limiter.allow_request("client-a", now=10.0), (True, 0.0))
        self.assertEqual(limiter.allow_request("client-a", now=14.5), (False, 0.5))
        self.assertEqual(limiter.allow_request("client-a", now=15.0), (True, 0.0))

    def test_rate_limiter_request_window_clamp_keeps_short_window_usable(self) -> None:
        limiter = ControlApiRateLimiter(max_requests=1, window_seconds=0.0)

        self.assertEqual(limiter.allow_request("client-a", now=10.0), (True, 0.0))
        self.assertEqual(limiter.allow_request("client-a", now=11.0), (True, 0.0))

    def test_rate_limiter_default_critical_cooldown_contracts_are_exact(self) -> None:
        limiter = ControlApiRateLimiter()

        self.assertEqual(limiter.allow_command("client-a", "trigger_software_update", now=20.0), (True, 0.0))
        self.assertEqual(limiter.allow_command("client-a", "trigger_software_update", now=21.0), (False, 1.0))
        self.assertEqual(limiter.allow_command("client-a", "trigger_software_update", now=22.0), (True, 0.0))

    def test_rate_limiter_one_second_cooldown_is_still_enforced(self) -> None:
        limiter = ControlApiRateLimiter(critical_cooldown_seconds=1.0)

        self.assertEqual(limiter.allow_command("client-a", "trigger_software_update", now=20.0), (True, 0.0))
        self.assertEqual(limiter.allow_command("client-a", "trigger_software_update", now=20.5), (False, 0.5))
        self.assertEqual(limiter.allow_command("client-a", "trigger_software_update", now=21.0), (True, 0.0))

    def test_rate_limiter_critical_cooldown_is_scoped_by_client_and_command(self) -> None:
        limiter = ControlApiRateLimiter(critical_cooldown_seconds=5.0)

        self.assertEqual(limiter.allow_command("client-a", "trigger_software_update", now=0.0), (True, 0.0))
        self.assertEqual(limiter.allow_command("client-a", "reset_phase_lockout", now=0.0), (True, 0.0))
        self.assertEqual(limiter.allow_command("client-b", "trigger_software_update", now=0.0), (True, 0.0))
        self.assertEqual(limiter.allow_command("client-a", "trigger_software_update", now=4.5), (False, 0.5))
        self.assertEqual(limiter.allow_command("client-a", "trigger_software_update", now=5.0), (True, 0.0))

    def test_rate_limiter_non_critical_and_zero_cooldown_never_create_cooldown(self) -> None:
        limiter = ControlApiRateLimiter(critical_cooldown_seconds=2.0)

        self.assertEqual(limiter.allow_command("client-a", "set_mode", now=0.0), (True, 0.0))
        self.assertEqual(limiter.allow_command("client-a", "set_mode", now=1.0), (True, 0.0))

        no_cooldown = ControlApiRateLimiter(critical_cooldown_seconds=0.0)
        self.assertEqual(no_cooldown.allow_command("client-a", "trigger_software_update", now=0.0), (True, 0.0))
        self.assertEqual(no_cooldown.allow_command("client-a", "trigger_software_update", now=0.0), (True, 0.0))
        self.assertEqual(no_cooldown.allow_command("client-a", "trigger_software_update", now=-1.0), (True, 0.0))
