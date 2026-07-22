# Local Control API

The service exposes one small local HTTP API on top of the canonical
`Control API v1`.

This API is intentionally:

- local-first
- command-oriented
- versioned
- transport-thin

It does not introduce a second control model. The HTTP adapter builds the same
canonical `ControlCommand` objects used by DBus writes and returns the same
structured `ControlResult` semantics.

## Enable The API

Set these values in `deploy/venus/config.venus_evcharger.ini`:

```ini
ControlApiEnabled=1
ControlApiHost=127.0.0.1
ControlApiPort=8765
ControlApiAuthToken=
ControlApiReadToken=
ControlApiControlToken=
ControlApiAdminToken=
ControlApiUpdateToken=
ControlApiLocalhostOnly=1
ControlApiUnixSocketPath=
ControlApiAuditPath=/run/dbus-venus-evcharger-control-audit-60.jsonl
ControlApiIdempotencyPath=/run/dbus-venus-evcharger-idempotency-60.json
ControlApiRateLimitMaxRequests=30
ControlApiRateLimitWindowSeconds=5
ControlApiCriticalCooldownSeconds=2
```

Recommended defaults:

- keep `ControlApiLocalhostOnly=1`
- keep `ControlApiHost=127.0.0.1` unless you intentionally front it with another local proxy
- configure at least one explicit token before binding TCP outside loopback; the service refuses to start otherwise
- use `ControlApiReadToken` for read-only clients and `ControlApiControlToken` for writers
- use `ControlApiAdminToken` and `ControlApiUpdateToken` only when you want stricter local separation for advanced control and update operations
- use `ControlApiAuthToken` only when one shared token for both scopes is enough
- prefer `ControlApiUnixSocketPath` when a process-local unix socket is a better fit than TCP
- keep API audit and idempotency paths on `/run/...` or `/tmp/...`
- use the rate-limit settings as a local abuse guard, not as a remote multi-tenant quota system
- treat the unix socket as the preferred local automation transport when your client supports it

## Runtime-Only Policy

The API is intentionally designed not to wear flash storage.

- command audit data is kept in memory and may optionally be mirrored only to `/run/...` or `/tmp/...`
- idempotency replay state is kept in memory and may optionally be mirrored only to `/run/...` or `/tmp/...`
- non-runtime paths for these API metadata files are ignored for persistence on purpose

This keeps API control metadata runtime-only and avoids writing it to flash-backed storage.

## Machine-Readable Contract

- `GET /v1/openapi.json` returns the OpenAPI `3.1.0` document
- OpenAPI `3.1` component schemas are JSON Schema-compatible and are the normative machine-readable contract

## Endpoints

### Public endpoints

- `GET /v1/control/health`
- `GET /v1/state/healthz`
- `GET /v1/openapi.json`

### Authenticated read endpoints

- `GET /v1/capabilities`
- `GET /v1/events`
- all `GET /v1/state/*`

Focused recommendation readout:

- `GET /v1/state/victron-bias-recommendation`

This small state payload is meant for direct operator use and exposes:

- current Victron-bias `kp`, `ki`, and ramp values
- recommended `kp`, `ki`, and ramp values
- recommendation confidence and reason
- a copy-paste-ready INI snippet
- a short operator hint

### Authenticated control endpoint

- `POST /v1/control/command`

## Auth Contract

Header contract:

- `Authorization: Bearer <token>`

Scope rules:

- `ControlApiReadToken` grants read access
- `ControlApiControlToken` grants read and control access
- `ControlApiAdminToken` grants read, basic control, and admin-only control access
- `ControlApiUpdateToken` grants read, control, admin, and update-trigger access
- `ControlApiAuthToken` acts as one shared fallback token for both scopes when the scoped tokens are unset

Locality rules:

- when `ControlApiLocalhostOnly=1`, remote TCP clients are rejected
- unix-socket mode is always treated as local
- unix sockets are created with owner-only permissions
- the API does not terminate TLS; remote TCP belongs behind an authenticated VPN or trusted local TLS proxy

## `GET /v1/capabilities`

Returns the stable capability view for the running setup, including:

- supported command names
- available command sources
- available modes
- supported phase selections
- backend topology
- auth behavior
- endpoint stability information

Notable fields:

- `command_names`
- `available_modes`
- `supported_phase_selections`
- `features`
- `topology`
- `versioning.stable_endpoints`
- `versioning.experimental_endpoints`

## `POST /v1/control/command`

Accepts one JSON object and returns one structured command/result payload.

Canonical request style for new clients:

1. Canonical command form

```json
{
  "name": "set_mode",
  "value": 1
}
```

Normative guidance:

- new clients should prefer the canonical `name` form
- commands with one fixed target may omit `target`
- commands that address one of several domain settings must provide a semantic `target`
- DBus paths are not part of the Control API contract

For runtime-setting commands, an explicit semantic target is required:

```json
{
  "name": "set_auto_runtime_setting",
  "target": "auto_start_surplus_watts",
  "value": 1700
}
```

### Request fields

- `name`
- `target` (required only for command families with more than one target)
- `value`
- `detail`
- `command_id`
- `idempotency_key`

### Tracking headers

- `X-Command-Id: <client command id>`
- `Idempotency-Key: <stable retry key>`
- `If-Match: "<state-token>"`
- `X-State-Token: <state-token>`

The HTTP adapter will generate a `command_id` when none is supplied.

## Optimistic concurrency

Clients can protect control writes against stale assumptions about current local
state.

Recommended flow:

- read one state or capabilities endpoint
- capture `ETag` or `X-State-Token`
- send that token back in `If-Match` or `X-State-Token`

Behavior:

- matching token allows the write to proceed
- stale token returns `409` with `code=conflict`
- `If-Match: *` explicitly skips the concurrency check

## Response contract

Every command response uses this stable outer shape:

- `ok`
- `detail`
- `replayed`
- `command`
- `result`
- `error`

`command` includes:

- `name`
- `target`
- `value`
- `source`
- `detail`
- `command_id`
- `idempotency_key`

`result` includes:

- `command`
- `status`
- `accepted`
- `applied`
- `persisted`
- `reversible_failure`
- `external_side_effect_started`
- `detail`

`error` includes:

- `code`
- `message`
- `retryable`
- `details`

## Response semantics

The response intentionally exposes three different layers of meaning:

- HTTP status:
  transport-level outcome of the request
- `ok`:
  coarse success flag for the normalized API envelope
- `result.status`:
  coarse command lifecycle outcome for accepted commands
- `error.code`:
  fine-grained semantic reason when the request or command was not successful

`ok` is intentionally coarse:

- command responses use `ok=true` for accepted/applied command handling
- command responses use `ok=false` for rejected commands and malformed requests
- read/state responses use `ok=true` when the endpoint returns a normalized payload

Clients should therefore interpret responses in this order:

1. check HTTP status for transport success/failure
2. check `ok` for coarse API success/failure
3. check `result.status` for command lifecycle
4. check `error.code` for the precise failure class

Stable `status` values:

- `applied`
- `accepted_in_flight`
- `rejected`

The `status` taxonomy is intentionally coarse within `v1`:

- `applied` means the command was accepted and completed within the current request lifecycle
- `accepted_in_flight` means the command was accepted and side effects have started, but completion is still in flight
- `rejected` means the command was not accepted for execution

Blocked commands stay within this stable taxonomy:

- topology, health, mode, and update blockers remain `status=rejected`
- clients must use `error.code` for the finer reason, for example:
  - `blocked_by_health`
  - `blocked_by_mode`
  - `unsupported_for_topology`
  - `update_in_progress`

`v1` does not define separate top-level statuses such as `blocked` or `no_effect`.
If a client needs the finer class, it should rely on `error.code` and `detail`.

Stable error codes include:

- `invalid_json`
- `validation_error`
- `unauthorized`
- `insufficient_scope`
- `forbidden_remote_client`
- `unsupported_command`
- `unsupported_for_topology`
- `blocked_by_health`
- `blocked_by_mode`
- `update_in_progress`
- `command_rejected`
- `idempotency_conflict`
- `rate_limited`
- `cooldown_active`
- `not_found`

HTTP status mapping:

- `200 OK` for `applied`
- `202 Accepted` for `accepted_in_flight`
- `409 Conflict` for rejected commands or idempotency conflicts
- `429 Too Many Requests` for local rate limits or command cooldowns
- `400 Bad Request` for malformed JSON or invalid command payloads
- `401 Unauthorized` for missing/invalid tokens
- `403 Forbidden` for wrong scope or rejected remote clients

Successful capability, state, and command responses also expose the current
local state token in:

- `ETag`
- `X-State-Token`

## Command matrix

The table below is the practical client-facing contract summary. For exact
machine-readable validation rules, treat `GET /v1/openapi.json` as normative.

<!-- BEGIN:CONTROL_API_COMMAND_MATRIX -->
| Command name | Required fields | Value type | Allowed values / ranges | Idempotent shape | `accepted_in_flight` | Required scope | Typical restrictions |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `set_mode` | `name`, `value` | integer | `0`, `1`, `2` | yes | possible | `control_basic` | mode-specific runtime rules |
| `set_auto_start` | `name`, `value` | boolean or `0/1` | binary | yes | uncommon | `control_basic` | none beyond local policy |
| `set_start_stop` | `name`, `value` | boolean or `0/1` | binary | yes | possible | `control_basic` | mode/backend policy |
| `set_enable` | `name`, `value` | boolean or `0/1` | binary | yes | possible | `control_basic` | backend/health policy |
| `set_current_setting` | `name`, `target`, `value` | number | `>= 0` | yes | possible | `control_basic` | backend/current limits |
| `set_phase_selection` | `name`, `value` | string | `P1`, `P1_P2`, `P1_P2_P3` | yes | possible | `control_basic` | supported topology and phase hardware |
| `set_auto_runtime_setting` | `name`, `target`, `value` | boolean or `0/1`, integer, number, or string depending on `target` | target-specific schema | yes | uncommon | `control_admin` | only supported runtime-setting paths |
| `reset_phase_lockout` | `name`, `value` | boolean or `0/1` | binary | no | uncommon | `control_admin` | only meaningful when lockout exists |
| `reset_contactor_lockout` | `name`, `value` | boolean or `0/1` | binary | no | uncommon | `control_admin` | only meaningful when lockout exists |
| `trigger_software_update` | `name`, `value` | boolean or `0/1` | binary | no | possible | `update_admin` | update policy, availability, current update state |
<!-- END:CONTROL_API_COMMAND_MATRIX -->

Notes:

- the canonical form is `name`-first; `target` is required where the command family needs a more specific domain target
- DBus paths remain private to the gateway adapter and are not accepted by this API
- "Idempotent shape" means a client can safely reason about repeated writes to the same target/value pair; replay protection is additionally available through `Idempotency-Key`
- `accepted_in_flight` should be treated as a possible outcome for commands that can trigger external actuation or longer-running local side effects

## Idempotency and command tracking

For safe retries:

- provide one stable `Idempotency-Key`
- optionally provide your own `X-Command-Id`

Behavior:

- same `Idempotency-Key` + same payload returns the cached response with `replayed=true`
- same `Idempotency-Key` + different payload returns `409` with `code=idempotency_conflict`

The replay cache is runtime-only:

- it stays in memory and can optionally be mirrored under `/run/...` or `/tmp/...`
- it is not intended to survive a device reboot
- it should not be pointed at flash-backed storage

## Request schema strictness

`POST /v1/control/command` is now described in OpenAPI as an explicit per-command
`oneOf` contract instead of one generic loose payload.

That means the machine-readable contract now publishes, per command:

- allowed fields
- required fields
- expected types
- value ranges or enums where applicable

Examples:

- `set_mode` accepts only `0`, `1`, or `2`
- `set_phase_selection` accepts only known phase-selection values
- `set_current_setting` requires a non-negative numeric value
- runtime-setting payloads are split into float, integer, string, and binary forms

Runtime validation in the service follows the same stricter rules, so unclear
payloads are rejected early with `validation_error`.

## Local throttling

The HTTP adapter includes small local abuse guards for control traffic:

- `ControlApiRateLimitMaxRequests`
- `ControlApiRateLimitWindowSeconds`
- `ControlApiCriticalCooldownSeconds`

These are intentionally:

- local-only
- lightweight
- runtime-only

They exist to prevent command storms or overly aggressive retry loops from a
local client. They are not intended as a remote quota or multi-tenant traffic
policy.

When throttling is active, the API returns:

- `429 Too Many Requests`
- `code=rate_limited` for general request bursts
- `code=cooldown_active` for short cool-down protection on critical commands
- `Retry-After` when a sensible retry delay is known

## Event stream

`GET /v1/events` returns `application/x-ndjson`.

Each line is one event object with:

- `seq`
- `api_version`
- `kind`
- `timestamp`
- `resume_token`
- `payload`

Useful query parameters:

- `limit`
- `after`
- `resume`
- `heartbeat`
- `timeout`
- `once`
- `kind`

`kind` may be repeated or comma-separated to filter the stream to specific
event types, for example:

- `kind=command`
- `kind=state`
- `kind=command&kind=state`
- `kind=command,state`

Reconnect hints:

- the stream response includes `X-Control-Api-Retry-Ms`
- heartbeat payloads include `retry_hint_ms`
- heartbeat payloads include `resume_hint`

Typical examples:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  'http://127.0.0.1:8765/v1/events?once=1'
```

```bash
curl -N \
  -H 'Authorization: Bearer READ-TOKEN' \
  'http://127.0.0.1:8765/v1/events?limit=10&timeout=30'
```

## Client examples

<!-- BEGIN:CONTROL_API_GETTING_STARTED -->
Official example files:

- Python example: [examples/control_api_client.py](/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/venus-evcharger-service/examples/control_api_client.py)
- Small CLI: [venus_evchargerctl.py](/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/venus-evcharger-service/venus_evchargerctl.py)
- Target-system wrapper: [venus_evchargerctl.sh](/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/venus-evcharger-service/deploy/venus/venus_evchargerctl.sh)
- Local developer runbook: [DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md)

CLI quick start:

```bash
python3 ./venus_evchargerctl.py --token READ-TOKEN health
python3 ./venus_evchargerctl.py --token READ-TOKEN doctor
python3 ./venus_evchargerctl.py --token READ-TOKEN capabilities
python3 ./venus_evchargerctl.py --token READ-TOKEN state automation
python3 ./venus_evchargerctl.py --token CONTROL-TOKEN command set-mode 1
python3 ./venus_evchargerctl.py --token CONTROL-TOKEN safe-write set-mode 1
python3 ./venus_evchargerctl.py --token CONTROL-TOKEN command set-current-setting 12.5 --target set_current
python3 ./venus_evchargerctl.py --unix-socket /run/venus-evcharger-control.sock --token READ-TOKEN watch --kind command --once
```

Read capabilities with `curl`:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/capabilities
```

Execute a command with `curl`:

```bash
curl -s \
  -H 'Authorization: Bearer CONTROL-TOKEN' \
  -H 'Idempotency-Key: mode-1' \
  -H 'Content-Type: application/json' \
  -d '{"name":"set_mode","value":1}' \
  http://127.0.0.1:8765/v1/control/command
```

Use a unix socket:

```bash
curl --unix-socket /run/venus-evcharger-control.sock \
  -H 'Authorization: Bearer CONTROL-TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"name":"set_mode","value":1}' \
  http://localhost/v1/control/command
```

Use `If-Match` with the current state token:

```bash
STATE_TOKEN="$(curl -s -D - -o /tmp/state.json \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/automation \
  | awk -F': ' '/^X-State-Token:/ {print $2}' | tr -d '\r')"

curl -s \
  -H 'Authorization: Bearer CONTROL-TOKEN' \
  -H 'Content-Type: application/json' \
  -H "If-Match: \"$STATE_TOKEN\"" \
  -d '{"name":"set_mode","value":1}' \
  http://127.0.0.1:8765/v1/control/command
```

Python quick start:

```python
from venus_evcharger.control.client import LocalControlApiClient

client = LocalControlApiClient(
    base_url="http://127.0.0.1:8765",
    bearer_token="CONTROL-TOKEN",
)

automation = client.automation()
state_token = client.state_token_from_response(automation)
result = client.safe_command(
    {"name": "set_mode", "value": 1},
    idempotency_key="set-mode-1",
).json()
```
<!-- END:CONTROL_API_GETTING_STARTED -->

## CLI contract

`venus_evchargerctl` is the small first-class operator client for this local
API.

Supported entrypoints:

- repository-local:
  `python3 ./venus_evchargerctl.py`
- installed target wrapper:
  `./deploy/venus/venus_evchargerctl.sh`

Exit codes:

- `0` when the API returned a `2xx` response
- `1` when the request reached the API but was rejected or failed
- `2` when the CLI invocation itself was invalid

That means shell automation can treat `1` as an application/API failure and `2`
as a local usage bug.

Example:

```bash
python3 ./venus_evchargerctl.py --token READ-TOKEN state victron-bias-recommendation
```

## Versioning

The formal versioning rules are documented in [API_VERSIONING.md](API_VERSIONING.md).
