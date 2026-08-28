# Forensic Observer Contracts

The forensic observer is an external, read-only process.

## Boundaries

- Gateway state is consumed only through the semantic gateway diagnostics JSON contract.
- The observer does not import a DBus library, invoke a DBus command, introspect a service, or publish a value.
- Direct device probing is disabled by default. An HTTP Shelly probe is created only for an explicitly selected role whose normalized backend type starts with `shelly`.
- Process output, JSON documents, log tails, HTTP headers, HTTP bodies, and configuration files are bounded before use.
- Incident artifacts are written only to recognized removable-storage mounts while a non-blocking shared maintenance lease is held.
- Main configuration secrets are redacted by key before an artifact is published.

## Incident policy

An incident requires at least one of these conditions:

- a critical semantic gateway field is unavailable, erroneous, or unknown;
- gateway health is protective or unavailable;
- the main runit service is down or its status command failed;
- a bounded runtime-log tail contains a stable trace marker.

Unavailable gateway diagnostics alone do not create an incident. This preserves the established behavior and prevents storage churn while the gateway is starting.

An uninterrupted incident is represented as one episode:

- the transition into an incident writes one immutable incident bundle;
- further unhealthy observations do not write duplicate bundles, regardless of episode duration or changing reason details;
- one healthy observation starts recovery confirmation;
- another incident observation cancels the pending recovery;
- after 60 uninterrupted healthy seconds, `recovery.json` is atomically added to the original incident bundle;
- a later transition into an incident starts a new episode and writes a new bundle.

## Resource policy

- Start delay defaults to 180 seconds.
- Observation interval defaults to 30 seconds and is clamped to at least one second.
- Recovery confirmation defaults to 60 seconds.
- No polling thread, async executor, DBus connection, or persistent network connection is retained.
