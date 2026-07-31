# API Overview

The local HTTP surface is organized into four complementary pieces:

- Control:
  command-oriented writes through `POST /v1/control/command`
- State:
  current snapshots through `GET /v1/state/*`
- Events:
  incremental change stream through `GET /v1/events`
- OpenAPI:
  machine-readable contract through `GET /v1/openapi.json`

## Mental model

### Control = commands

Use the Control API when a client wants to ask the service to do something:

- set mode
- change current
- change phase selection
- adjust runtime settings
- trigger software update flow

The preferred request form is canonical and command-first:

```json
{
  "name": "set_mode",
  "value": 1
}
```

Control commands use semantic targets. DBus paths are an adapter detail and
are not part of the public control API.

See:

- [CONTROL_API.md](CONTROL_API.md)
- [DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md)
- [API_OPERATOR_GUIDE.md](API_OPERATOR_GUIDE.md)

### State = snapshots

Use the State API when a client wants the current normalized view of the
running service:

- operational state
- runtime state
- topology
- effective config subset
- update state
- contract references

These endpoints are intentionally snapshot-oriented and are the best first read
for UIs, diagnostics tools, and local automation.

See:

- [STATE_API.md](STATE_API.md)

### Events = incremental stream

Use the Events API when a client wants changes over time instead of repeated
polling:

- initial snapshot plus recent events via `?once=1`
- live follow with `timeout` and `heartbeat`
- resume from `after` or `resume`
- optional `kind` filter for focused consumers

This is useful for dashboards, bridges, and local automations that want
incremental updates.

See:

- [CONTROL_API.md](CONTROL_API.md)

### OpenAPI = machine-readable contract

Use OpenAPI when a client or tool wants the normative machine-readable shape of
the local API:

- endpoint paths
- request schemas
- response schemas
- error object shape
- event and state payload schemas

See:

- [CONTROL_API.md](CONTROL_API.md)
- [API_VERSIONING.md](API_VERSIONING.md)

## Contract discovery

For programmatic discovery, clients should use:

- `GET /v1/openapi.json`
- `GET /v1/capabilities`
- `GET /v1/state/contracts`

Together these answer:

- what endpoints exist
- which parts are stable vs experimental
- which command names and scopes are supported
- which topology/backend constraints apply at runtime

## Client starting points

<!-- BEGIN:API_OVERVIEW_CLIENT_STARTING_POINTS -->
Practical local client entrypoints in this repository:

- Python example: [examples/control_api_client.py](examples/control_api_client.py)
- Small CLI: [venus_evchargerctl.py](venus_evchargerctl.py)
- Target-system wrapper: [venus_evchargerctl.sh](deploy/venus/venus_evchargerctl.sh)

Typical first commands:

- `python3 ./venus_evchargerctl.py --token READ-TOKEN health`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN doctor`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN capabilities`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN state automation`
- `python3 ./venus_evchargerctl.py --token CONTROL-TOKEN command set-mode 1`
- `python3 ./venus_evchargerctl.py --token CONTROL-TOKEN safe-write set-mode 1`

These sit on top of the same canonical command and state contract described in
[CONTROL_API.md](CONTROL_API.md) and [STATE_API.md](STATE_API.md).
<!-- END:API_OVERVIEW_CLIENT_STARTING_POINTS -->

For day-to-day operator checks, use [API_OPERATOR_GUIDE.md](API_OPERATOR_GUIDE.md).
For local PC-based development and quick iteration, use
[DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md).
