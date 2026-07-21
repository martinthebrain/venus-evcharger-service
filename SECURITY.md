# Security Policy

## Supported Versions

Security fixes are made on the current `main` branch and included in the next
signed release. Production installations should use the latest signed release
and should keep the `noUpdate` marker in place between deliberate maintenance
windows.

Older snapshots, untagged source archives, and locally modified deployments do
not receive separate security backports.

## Reporting A Vulnerability

Do not publish credentials, customer addresses, network details, logs, or an
exploitable vulnerability in a public issue.

Use GitHub's private vulnerability-reporting form for this repository:

<https://github.com/martinthebrain/venus-evcharger-service/security/advisories/new>

Include the affected version or commit, deployment environment, reproduction
steps, expected impact, and any relevant sanitized logs. For ordinary bugs
that do not contain sensitive information, use the public issue tracker.

## Operational Boundary

The Control API is designed for loopback or a protected Unix socket by
default. A non-loopback TCP listener requires explicit authentication tokens.
The API does not provide TLS termination; expose it remotely only through a
trusted local proxy or an authenticated VPN.

Only the DBus gateway adapter may access the Victron DBus. Reports involving
direct DBus access outside that boundary are treated as architecture and
availability defects.
