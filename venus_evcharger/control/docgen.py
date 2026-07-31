# SPDX-License-Identifier: GPL-3.0-or-later
"""Generated Markdown blocks for the local Control API docs."""

from __future__ import annotations

from typing import Callable

from venus_evcharger.control.reference import render_control_api_command_matrix_markdown


def replace_generated_markdown_block(document: str, block_name: str, content: str) -> str:
    """Replace one generated Markdown block between BEGIN/END markers."""
    begin_marker = f"<!-- BEGIN:{block_name} -->"
    end_marker = f"<!-- END:{block_name} -->"
    begin = document.index(begin_marker) + len(begin_marker)
    end = document.index(end_marker)
    replacement = f"\n{content.rstrip()}\n"
    return f"{document[:begin]}{replacement}{document[end:]}"


def render_control_api_getting_started_markdown() -> str:
    """Render the practical getting-started block for the Control API docs."""
    return "\n".join(
        [
            "Official example files:",
            "",
            "- Python example: [examples/control_api_client.py](examples/control_api_client.py)",
            "- Small CLI: [venus_evchargerctl.py](venus_evchargerctl.py)",
            "- Target-system wrapper: [venus_evchargerctl.sh](deploy/venus/venus_evchargerctl.sh)",
            "- Local developer runbook: [DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md)",
            "",
            "CLI quick start:",
            "",
            "```bash",
            "python3 ./venus_evchargerctl.py --token READ-TOKEN health",
            "python3 ./venus_evchargerctl.py --token READ-TOKEN doctor",
            "python3 ./venus_evchargerctl.py --token READ-TOKEN capabilities",
            "python3 ./venus_evchargerctl.py --token READ-TOKEN state automation",
            "python3 ./venus_evchargerctl.py --token CONTROL-TOKEN command set-mode 1",
            "python3 ./venus_evchargerctl.py --token CONTROL-TOKEN safe-write set-mode 1",
            "python3 ./venus_evchargerctl.py --token CONTROL-TOKEN command set-current-setting 12.5 --target set_current",
            "python3 ./venus_evchargerctl.py --unix-socket /run/venus-evcharger-control.sock --token READ-TOKEN watch --kind command --once",
            "```",
            "",
            "Read capabilities with `curl`:",
            "",
            "```bash",
            "curl -s \\",
            "  -H 'Authorization: Bearer READ-TOKEN' \\",
            "  http://127.0.0.1:8765/v1/capabilities",
            "```",
            "",
            "Execute a command with `curl`:",
            "",
            "```bash",
            "curl -s \\",
            "  -H 'Authorization: Bearer CONTROL-TOKEN' \\",
            "  -H 'Idempotency-Key: mode-1' \\",
            "  -H 'Content-Type: application/json' \\",
            "  -d '{\"name\":\"set_mode\",\"value\":1}' \\",
            "  http://127.0.0.1:8765/v1/control/command",
            "```",
            "",
            "Use a unix socket:",
            "",
            "```bash",
            "curl --unix-socket /run/venus-evcharger-control.sock \\",
            "  -H 'Authorization: Bearer CONTROL-TOKEN' \\",
            "  -H 'Content-Type: application/json' \\",
            "  -d '{\"name\":\"set_mode\",\"value\":1}' \\",
            "  http://localhost/v1/control/command",
            "```",
            "",
            "Use `If-Match` with the current state token:",
            "",
            "```bash",
            "STATE_TOKEN=\"$(curl -s -D - -o /tmp/state.json \\",
            "  -H 'Authorization: Bearer READ-TOKEN' \\",
            "  http://127.0.0.1:8765/v1/state/automation \\",
            "  | awk -F': ' '/^X-State-Token:/ {print $2}' | tr -d '\\r')\"",
            "",
            "curl -s \\",
            "  -H 'Authorization: Bearer CONTROL-TOKEN' \\",
            "  -H 'Content-Type: application/json' \\",
            "  -H \"If-Match: \\\"$STATE_TOKEN\\\"\" \\",
            "  -d '{\"name\":\"set_mode\",\"value\":1}' \\",
            "  http://127.0.0.1:8765/v1/control/command",
            "```",
            "",
            "Python quick start:",
            "",
            "```python",
            "from venus_evcharger.control.client import LocalControlApiClient",
            "",
            "client = LocalControlApiClient(",
            "    base_url=\"http://127.0.0.1:8765\",",
            "    bearer_token=\"CONTROL-TOKEN\",",
            ")",
            "",
            "automation = client.automation()",
            "state_token = client.state_token_from_response(automation)",
            "result = client.safe_command(",
            "    {\"name\": \"set_mode\", \"value\": 1},",
            "    idempotency_key=\"set-mode-1\",",
            ").json()",
            "```",
        ]
    )


def render_api_overview_client_starting_points_markdown() -> str:
    """Render the short client-starting-points block for the overview doc."""
    return "\n".join(
        [
            "Practical local client entrypoints in this repository:",
            "",
            "- Python example: [examples/control_api_client.py](examples/control_api_client.py)",
            "- Small CLI: [venus_evchargerctl.py](venus_evchargerctl.py)",
            "- Target-system wrapper: [venus_evchargerctl.sh](deploy/venus/venus_evchargerctl.sh)",
            "",
            "Typical first commands:",
            "",
            "- `python3 ./venus_evchargerctl.py --token READ-TOKEN health`",
            "- `python3 ./venus_evchargerctl.py --token READ-TOKEN doctor`",
            "- `python3 ./venus_evchargerctl.py --token READ-TOKEN capabilities`",
            "- `python3 ./venus_evchargerctl.py --token READ-TOKEN state automation`",
            "- `python3 ./venus_evchargerctl.py --token CONTROL-TOKEN command set-mode 1`",
            "- `python3 ./venus_evchargerctl.py --token CONTROL-TOKEN safe-write set-mode 1`",
            "",
            "These sit on top of the same canonical command and state contract described in",
            "[CONTROL_API.md](CONTROL_API.md) and [STATE_API.md](STATE_API.md).",
        ]
    )


def render_readme_local_http_control_api_getting_started_markdown() -> str:
    """Render the short README quick-start block for the local API."""
    return "\n".join(
        [
            "Quick start:",
            "",
            "- `python3 ./venus_evchargerctl.py --token READ-TOKEN health`",
            "- `python3 ./venus_evchargerctl.py --token READ-TOKEN doctor`",
            "- `python3 ./venus_evchargerctl.py --token READ-TOKEN capabilities`",
            "- `python3 ./venus_evchargerctl.py --token READ-TOKEN state automation`",
            "- `python3 ./venus_evchargerctl.py --token CONTROL-TOKEN safe-write set-mode 1`",
            "- `python3 ./venus_evchargerctl.py --unix-socket /run/venus-evcharger-control.sock --token READ-TOKEN watch --kind command --once`",
            "",
            "For direct HTTP usage, `curl` snippets, optimistic concurrency with `If-Match`,",
            "and a small Python example, see [CONTROL_API.md](CONTROL_API.md).",
            "For a short local developer runbook on a normal PC, see",
            "[DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md).",
        ]
    )


GENERATED_MARKDOWN_BLOCK_RENDERERS: dict[str, Callable[[], str]] = {
    "CONTROL_API_COMMAND_MATRIX": render_control_api_command_matrix_markdown,
    "CONTROL_API_GETTING_STARTED": render_control_api_getting_started_markdown,
    "API_OVERVIEW_CLIENT_STARTING_POINTS": render_api_overview_client_starting_points_markdown,
    "README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED": render_readme_local_http_control_api_getting_started_markdown,
}
