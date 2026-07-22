# Developer API Workflow

This short runbook is the quickest way to exercise the local HTTP Control API
and `venus_evchargerctl` during development on a normal PC.

For the formal contract, see [CONTROL_API.md](CONTROL_API.md) and
[STATE_API.md](STATE_API.md). For target-system operator checks on Venus OS,
see [API_OPERATOR_GUIDE.md](API_OPERATOR_GUIDE.md).

## 1. Prepare a local config

Start from the normal config file:

```bash
cp deploy/venus/config.venus_evcharger.ini /tmp/venus-evcharger-dev.ini
```

Enable the local API and add local-only tokens. A minimal developer setup is:

```ini
ControlApiEnabled=1
ControlApiHost=127.0.0.1
ControlApiPort=8765
ControlApiReadToken=dev-read-token
ControlApiControlToken=dev-control-token
ControlApiUnixSocketPath=/tmp/venus-evcharger-control.sock
```

Notes:

- keep API runtime-only paths on `/run/...` or `/tmp/...`
- keep `ControlApiHost=127.0.0.1` for local development
- prefer a unix socket when you want process-local testing without TCP

## 2. Start the service locally

Run the service from the repository root:

```bash
python3 ./venus_evcharger_service.py
```

If you want to use a temporary config, point the service to it through the same
config override mechanism you normally use in your local shell environment
before starting the process.

If the service does not start, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## 3. Smoke-test the local API

Health check:

```bash
python3 ./venus_evchargerctl.py --token dev-read-token health
```

Doctor/self-test:

```bash
python3 ./venus_evchargerctl.py --token dev-read-token doctor
```

Capabilities:

```bash
python3 ./venus_evchargerctl.py --token dev-read-token capabilities
```

State summary:

```bash
python3 ./venus_evchargerctl.py --token dev-read-token state summary
```

The CLI exit-code contract is:

- `0` successful `2xx` API response
- `1` API reached, but request failed or was rejected
- `2` local CLI usage error

## 4. Exercise a write path locally

Set mode through the canonical command form:

```bash
python3 ./venus_evchargerctl.py \
  --token dev-control-token \
  command set-mode 1
```

Set mode with automatic optimistic concurrency:

```bash
python3 ./venus_evchargerctl.py \
  --token dev-control-token \
  safe-write set-mode 1
```

Change the current setting through its semantic target:

```bash
python3 ./venus_evchargerctl.py \
  --token dev-control-token \
  command set-current-setting 12.5 --target set_current
```

If you want an optimistic concurrency check, fetch the current state token and
reuse it with `If-Match`:

```bash
STATE_TOKEN="$(curl -s -D - -o /tmp/venus-state.json \
  -H 'Authorization: Bearer dev-read-token' \
  http://127.0.0.1:8765/v1/state/health \
  | awk -F': ' '/^X-State-Token:/ {print $2}' | tr -d '\r')"

curl -s \
  -H 'Authorization: Bearer dev-control-token' \
  -H 'Content-Type: application/json' \
  -H "If-Match: \"$STATE_TOKEN\"" \
  -d '{"name":"set_mode","value":1}' \
  http://127.0.0.1:8765/v1/control/command
```

## 5. Inspect the event stream

Read one small batch:

```bash
python3 ./venus_evchargerctl.py \
  --token dev-read-token \
  events --kind command --once
```

Use the ergonomic watch alias:

```bash
python3 ./venus_evchargerctl.py \
  --token dev-read-token \
  watch --kind command --once
```

Follow the stream live over TCP:

```bash
curl -N \
  -H 'Authorization: Bearer dev-read-token' \
  'http://127.0.0.1:8765/v1/events?kind=state&timeout=30'
```

Follow the stream over a unix socket:

```bash
curl --unix-socket /tmp/venus-evcharger-control.sock -N \
  -H 'Authorization: Bearer dev-read-token' \
  'http://localhost/v1/events?kind=command&timeout=30'
```

The stream already includes heartbeats and resume hints, so it is the best
local tool when you want to watch command or state changes without polling.

## 6. Recommended local development loop

For normal PC-based iteration, this is a good rhythm:

1. start the service locally
2. run `health`, `capabilities`, and `state summary`
3. execute one controlled write through `venus_evchargerctl`
4. watch `events` while changing behavior
5. stop the process, edit code, and repeat

## 7. When to switch to the other docs

- use [CONTROL_API.md](CONTROL_API.md) for the exact HTTP contract
- use [STATE_API.md](STATE_API.md) for snapshot payloads
- use [API_OPERATOR_GUIDE.md](API_OPERATOR_GUIDE.md) for installed-system quick checks
- use [API_OVERVIEW.md](API_OVERVIEW.md) when you want the mental model first
